"""Phase 6 圖片／影片生成測試。

重點在**產物持久化**：生成結果必須寫入物件儲存、登記為 FileItem、
關聯回業務實體，並在適當時自動採用。

供應商本身以受控替身注入（其正確性由 `test_providers.py` 涵蓋），
這裡驗證的是產物落地與回寫這條路徑。
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from studio.contracts.generation import GeneratedAsset, ImageResult, VideoResult
from studio.core import ids
from studio.models.asset import AssetImage, Character
from studio.models.file import FileItem
from studio.models.project import Chapter, Project
from studio.models.shot import Shot, ShotDetail, ShotFrame
from studio.models.task import GenerationTask, GenerationTaskLink
from studio.models.types import AssetKind, FileType, ShotFrameType, TaskStatus
from studio.tasks.runtime import run_task


@pytest.fixture(name="media_db")
def _media_db(tmp_path, monkeypatch):
    """獨立資料庫 + 本機物件儲存。"""

    from studio.core import db as db_core
    from studio.core import storage as storage_core

    monkeypatch.setenv("STUDIO_STORAGE_LOCAL_DIR", str(tmp_path / "storage"))
    storage_core.reset_storage()

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path/'media.db'}")

    @event.listens_for(engine.sync_engine, "connect")
    def _fk_on(dbapi_connection, _record):  # type: ignore[no-untyped-def]
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _setup() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(db_core.Base.metadata.create_all)

    asyncio.run(_setup())

    monkeypatch.setattr(db_core, "_engine", engine, raising=False)
    monkeypatch.setattr(db_core, "_session_factory", factory, raising=False)

    yield factory

    storage_core.reset_storage()


class _StubImageProvider:
    """回傳固定位元組的圖片供應商替身。"""

    def generate_image(self, _config, request):
        return ImageResult(
            assets=[
                GeneratedAsset(
                    data=b"png-bytes",
                    content_type="image/png",
                    width=1024,
                    height=1024,
                    seed=request.seed,
                )
            ],
            model=request.model,
        )


class _StubVideoProvider:
    """回傳固定位元組的影片供應商替身。"""

    def generate_video(self, _config, request):
        return VideoResult(
            assets=[
                GeneratedAsset(
                    data=b"mp4-bytes",
                    content_type="video/mp4",
                    duration_seconds=request.duration_seconds,
                )
            ],
            model=request.model,
        )


def _stub_resolution(monkeypatch, provider, capability: str) -> None:
    """讓模型解析回傳替身供應商，跳過真實供應商設定。"""

    from studio.tasks import media_tasks, resolve

    class _Resolved:
        model_name = "stub-model"
        provider_kind = "openai"
        config = None
        model_id = "model_stub"
        provider_id = "prov_stub"
        params: dict = {}

    async def _resolve(*_args, **_kwargs):
        return _Resolved()

    monkeypatch.setattr(resolve, "resolve_model", _resolve)

    if capability == "image":
        monkeypatch.setattr("studio.integrations.get_image_provider", lambda _kind: provider)
    else:
        monkeypatch.setattr("studio.integrations.get_video_provider", lambda _kind: provider)

    # media_tasks 內部以函式層級 import 取得，這裡一併覆寫模組屬性。
    monkeypatch.setattr(media_tasks, "logger", media_tasks.logger)


async def _seed_shot(factory) -> tuple[str, str]:
    """建立 專案 → 章節 → 分鏡，回傳 (shot_id, project_id)。"""

    project_id = ids.new_id(ids.PROJECT)
    chapter_id = ids.new_id(ids.CHAPTER)
    shot_id = ids.new_id(ids.SHOT)

    async with factory() as session:
        session.add(Project(id=project_id, name="測試專案", style_prompt="cinematic"))
        session.add(Chapter(id=chapter_id, project_id=project_id, index=1, title="第一集"))
        session.add(Shot(id=shot_id, chapter_id=chapter_id, index=1, title="開場"))
        session.add(ShotDetail(id=shot_id, description="夜晚街道", duration_seconds=6))
        await session.commit()

    return shot_id, project_id


async def _create_task(factory, *, task_kind: str, **kwargs) -> str:
    """建立任務並回傳 ID。"""

    task_id = ids.new_id(ids.TASK)
    async with factory() as session:
        session.add(GenerationTask(id=task_id, task_kind=task_kind, **kwargs))
        await session.commit()
    return task_id


# ── 分鏡脈絡 ──────────────────────────────────────────────────────────────────


async def test_shot_context_includes_project_style_and_assets(media_db) -> None:
    """生成脈絡必須帶入專案風格與已關聯資產的外觀描述。

    這是跨鏡頭一致性的實作基礎：每次生成都引用同一組描述。
    """

    from studio.models.asset import ShotAssetLink
    from studio.tasks.media_tasks import build_shot_context

    shot_id, project_id = await _seed_shot(media_db)

    character_id = ids.new_id(ids.CHARACTER)
    async with media_db() as session:
        session.add(
            Character(
                id=character_id,
                project_id=project_id,
                name="林小雨",
                appearance_prompt="長髮、白色連身裙",
            )
        )
        session.add(
            ShotAssetLink(shot_id=shot_id, asset_kind=AssetKind.character, asset_id=character_id)
        )
        await session.commit()

    context = await build_shot_context(shot_id)

    assert "cinematic" in context["prompt_context"]
    assert "林小雨" in context["prompt_context"]
    assert "白色連身裙" in context["prompt_context"], "資產外觀描述必須帶入提示詞"
    assert context["ratio"] == "9:16"
    assert context["duration_seconds"] == 6


async def test_shot_context_uses_shot_ratio_override(media_db) -> None:
    """分鏡層級的比例覆蓋應優先於專案預設。"""

    from studio.tasks.media_tasks import build_shot_context

    shot_id, _ = await _seed_shot(media_db)

    async with media_db() as session:
        detail = await session.get(ShotDetail, shot_id)
        detail.override_video_ratio = "16:9"
        await session.commit()

    assert (await build_shot_context(shot_id))["ratio"] == "16:9"


# ── 圖片生成持久化 ────────────────────────────────────────────────────────────


async def test_frame_image_generation_persists_result(media_db, monkeypatch) -> None:
    """圖片產物必須寫入儲存、登記 FileItem 並關聯回幀。"""

    _stub_resolution(monkeypatch, _StubImageProvider(), "image")

    shot_id, project_id = await _seed_shot(media_db)

    frame_id = ids.new_id(ids.SHOT_FRAME)
    async with media_db() as session:
        session.add(
            ShotFrame(
                id=frame_id,
                shot_id=shot_id,
                frame_type=ShotFrameType.first,
                index=0,
                prompt="a night street",
            )
        )
        await session.commit()

    task_id = await _create_task(
        media_db,
        task_kind="frame_image_generation",
        shot_id=shot_id,
        payload={"frame_types": ["first"]},
    )

    assert await run_task(task_id, executor_type="inline") == "succeeded"

    async with media_db() as session:
        task = await session.get(GenerationTask, task_id)
        assert task.status is TaskStatus.succeeded
        assert task.result["count"] == 1

        files = list((await session.execute(select(FileItem))).scalars().all())
        assert len(files) == 1
        assert files[0].file_type is FileType.image
        assert files[0].size_bytes == len(b"png-bytes")
        assert files[0].project_id == project_id
        assert files[0].source_task_id == task_id

        links = list((await session.execute(select(GenerationTaskLink))).scalars().all())
        assert len(links) == 1
        assert links[0].relation_type == "shot_frame"
        assert links[0].file_id == files[0].id

        # 幀原本沒有圖片，應自動採用第一張讓流程可以繼續
        frame = await session.get(ShotFrame, frame_id)
        assert frame.file_id == files[0].id

    # 位元組確實寫入了物件儲存
    from studio.core.storage import get_storage

    async with media_db() as session:
        stored_key = (await session.execute(select(FileItem.storage_key))).scalar_one()
    assert get_storage().get_bytes(stored_key) == b"png-bytes"


async def test_asset_image_generation_sets_first_image_primary(media_db, monkeypatch) -> None:
    """資產第一張參考圖應自動設為主要。"""

    _stub_resolution(monkeypatch, _StubImageProvider(), "image")

    _, project_id = await _seed_shot(media_db)

    character_id = ids.new_id(ids.CHARACTER)
    async with media_db() as session:
        session.add(
            Character(
                id=character_id,
                project_id=project_id,
                name="林小雨",
                appearance_prompt="長髮、白色連身裙",
            )
        )
        await session.commit()

    task_id = await _create_task(
        media_db,
        task_kind="asset_image_generation",
        payload={"asset_kind": "character", "asset_id": character_id},
    )

    assert await run_task(task_id, executor_type="inline") == "succeeded"

    async with media_db() as session:
        images = list((await session.execute(select(AssetImage))).scalars().all())
        assert len(images) == 1
        assert images[0].is_primary == 1, "第一張參考圖應自動成為主要"


async def test_asset_without_appearance_fails_clearly(media_db, monkeypatch) -> None:
    """缺少外觀描述時任務應以明確訊息失敗，而非產出無意義的圖。"""

    _stub_resolution(monkeypatch, _StubImageProvider(), "image")

    _, project_id = await _seed_shot(media_db)

    character_id = ids.new_id(ids.CHARACTER)
    async with media_db() as session:
        session.add(Character(id=character_id, project_id=project_id, name="無描述角色"))
        await session.commit()

    task_id = await _create_task(
        media_db,
        task_kind="asset_image_generation",
        payload={"asset_kind": "character", "asset_id": character_id},
    )

    assert await run_task(task_id, executor_type="inline") == "failed"

    async with media_db() as session:
        task = await session.get(GenerationTask, task_id)
        assert "外觀描述" in task.error


# ── 影片生成持久化 ────────────────────────────────────────────────────────────


async def test_video_generation_persists_and_attaches_to_shot(media_db, monkeypatch) -> None:
    """影片產物要落地，並在分鏡尚無影片時自動採用。"""

    _stub_resolution(monkeypatch, _StubVideoProvider(), "video")

    shot_id, project_id = await _seed_shot(media_db)
    task_id = await _create_task(media_db, task_kind="video_generation", shot_id=shot_id)

    assert await run_task(task_id, executor_type="inline") == "succeeded"

    async with media_db() as session:
        files = list((await session.execute(select(FileItem))).scalars().all())
        assert len(files) == 1
        assert files[0].file_type is FileType.video
        assert files[0].duration_seconds == 6, "應沿用分鏡設定的時長"

        shot = await session.get(Shot, shot_id)
        assert shot.generated_video_file_id == files[0].id, "分鏡應自動採用生成的影片"

        links = list((await session.execute(select(GenerationTaskLink))).scalars().all())
        assert links[0].relation_type == "shot"
        assert links[0].status.value == "todo", "產物預設為待採用"


async def test_video_generation_requires_shot(media_db, monkeypatch) -> None:
    """缺少 shot_id 時任務應失敗並說明原因。"""

    _stub_resolution(monkeypatch, _StubVideoProvider(), "video")

    task_id = await _create_task(media_db, task_kind="video_generation")

    assert await run_task(task_id, executor_type="inline") == "failed"

    async with media_db() as session:
        assert "shot_id" in (await session.get(GenerationTask, task_id)).error


async def test_generation_result_survives_reload(media_db, monkeypatch) -> None:
    """任務結果與產物在重新查詢後仍然存在（狀態不在記憶體）。"""

    _stub_resolution(monkeypatch, _StubVideoProvider(), "video")

    shot_id, _ = await _seed_shot(media_db)
    task_id = await _create_task(media_db, task_kind="video_generation", shot_id=shot_id)
    await run_task(task_id, executor_type="inline")

    # 以全新 session 重新查詢，模擬前端重新整理
    async with media_db() as session:
        task = await session.get(GenerationTask, task_id)
        assert task.status is TaskStatus.succeeded
        assert task.result["count"] == 1
        assert task.elapsed_ms is not None

        file_count = await session.scalar(select(func.count()).select_from(FileItem))
        assert file_count == 1
