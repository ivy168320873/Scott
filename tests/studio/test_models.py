"""Phase 2 資料模型測試。

以真實資料庫（SQLite，外鍵已開啟）驗證約束、級聯刪除與狀態語意，
而非只檢查類別屬性存在。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from studio.core.ids import new_id
from studio.models import (
    Chapter,
    Character,
    FileItem,
    GenerationTask,
    GenerationTaskLink,
    Model,
    Project,
    PromptTemplate,
    Provider,
    Shot,
    ShotDetail,
    ShotExtractedCandidate,
    ShotFrame,
)
from studio.models.types import (
    FileType,
    ModelCategory,
    PromptCategory,
    ProviderKind,
    ShotCandidateStatus,
    ShotCandidateType,
    ShotFrameType,
    ShotStatus,
    TaskKind,
    TaskStatus,
)

# ── 建構輔助 ──────────────────────────────────────────────────────────────────


async def _make_project(session: AsyncSession, name: str = "測試專案") -> Project:
    """建立並寫入一個專案。"""

    project = Project(id=new_id("prj"), name=name)
    session.add(project)
    await session.flush()
    return project


async def _make_chapter(session: AsyncSession, project: Project, index: int = 1) -> Chapter:
    """建立並寫入一個章節。"""

    chapter = Chapter(id=new_id("cha"), project_id=project.id, index=index, title=f"第 {index} 集")
    session.add(chapter)
    await session.flush()
    return chapter


async def _make_shot(session: AsyncSession, chapter: Chapter, index: int = 1) -> Shot:
    """建立並寫入一個分鏡。"""

    shot = Shot(id=new_id("shot"), chapter_id=chapter.id, index=index, title=f"鏡頭 {index}")
    session.add(shot)
    await session.flush()
    return shot


# ── 狀態語意 ──────────────────────────────────────────────────────────────────


def test_shot_status_has_no_generating_value() -> None:
    """`ShotStatus` 不得含 `generating`。

    執行時狀態屬於任務系統；混入 shot 狀態會讓「已確認」與「生成中」語意糾纏。
    """

    values = {member.value for member in ShotStatus}
    assert values == {"pending", "ready"}
    assert not hasattr(ShotStatus, "generating")


def test_task_status_terminal_classification() -> None:
    """終態與進行中的分類必須正確，恢復流程依賴此判斷。"""

    assert TaskStatus.succeeded.is_terminal
    assert TaskStatus.failed.is_terminal
    assert TaskStatus.cancelled.is_terminal

    assert TaskStatus.pending.is_active
    assert TaskStatus.running.is_active
    assert TaskStatus.streaming.is_active


# ── 唯一性約束 ────────────────────────────────────────────────────────────────


async def test_chapter_index_unique_within_project(session: AsyncSession) -> None:
    """同一專案內章節序號不可重複。"""

    project = await _make_project(session)
    await _make_chapter(session, project, index=1)

    session.add(Chapter(id=new_id("cha"), project_id=project.id, index=1, title="重複"))
    with pytest.raises(IntegrityError):
        await session.flush()


async def test_same_chapter_index_allowed_across_projects(session: AsyncSession) -> None:
    """不同專案可以有相同的章節序號。"""

    first = await _make_project(session, "專案一")
    second = await _make_project(session, "專案二")

    await _make_chapter(session, first, index=1)
    await _make_chapter(session, second, index=1)

    count = await session.scalar(select(func.count()).select_from(Chapter))
    assert count == 2


async def test_shot_index_unique_within_chapter(session: AsyncSession) -> None:
    """同一章節內鏡頭序號不可重複。"""

    project = await _make_project(session)
    chapter = await _make_chapter(session, project)
    await _make_shot(session, chapter, index=1)

    session.add(Shot(id=new_id("shot"), chapter_id=chapter.id, index=1, title="重複"))
    with pytest.raises(IntegrityError):
        await session.flush()


async def test_character_name_unique_within_project(session: AsyncSession) -> None:
    """同一專案內角色名稱不可重複，避免資產庫出現同名分身。"""

    project = await _make_project(session)
    session.add(Character(id=new_id("char"), project_id=project.id, name="李明"))
    await session.flush()

    session.add(Character(id=new_id("char"), project_id=project.id, name="李明"))
    with pytest.raises(IntegrityError):
        await session.flush()


async def test_shot_frame_unique_per_type_and_index(session: AsyncSession) -> None:
    """同一鏡頭的同類型幀序號不可重複。"""

    project = await _make_project(session)
    chapter = await _make_chapter(session, project)
    shot = await _make_shot(session, chapter)

    session.add(ShotFrame(id=new_id("frm"), shot_id=shot.id, frame_type=ShotFrameType.first, index=0))
    await session.flush()

    session.add(ShotFrame(id=new_id("frm"), shot_id=shot.id, frame_type=ShotFrameType.first, index=0))
    with pytest.raises(IntegrityError):
        await session.flush()


async def test_first_and_last_frames_coexist(session: AsyncSession) -> None:
    """首幀與尾幀屬不同類型，可同時存在。"""

    project = await _make_project(session)
    chapter = await _make_chapter(session, project)
    shot = await _make_shot(session, chapter)

    session.add(ShotFrame(id=new_id("frm"), shot_id=shot.id, frame_type=ShotFrameType.first, index=0))
    session.add(ShotFrame(id=new_id("frm"), shot_id=shot.id, frame_type=ShotFrameType.last, index=0))
    await session.flush()

    count = await session.scalar(select(func.count()).select_from(ShotFrame))
    assert count == 2


# ── 級聯刪除 ──────────────────────────────────────────────────────────────────


async def test_deleting_project_cascades_to_chapters_and_shots(session: AsyncSession) -> None:
    """刪除專案應連帶清除其章節與分鏡，不留孤兒資料。"""

    project = await _make_project(session)
    chapter = await _make_chapter(session, project)
    await _make_shot(session, chapter)
    await session.commit()

    await session.delete(project)
    await session.commit()

    assert await session.scalar(select(func.count()).select_from(Chapter)) == 0
    assert await session.scalar(select(func.count()).select_from(Shot)) == 0


async def test_deleting_shot_cascades_to_detail_and_candidates(session: AsyncSession) -> None:
    """刪除分鏡應連帶清除細節與候選。"""

    project = await _make_project(session)
    chapter = await _make_chapter(session, project)
    shot = await _make_shot(session, chapter)

    session.add(ShotDetail(id=shot.id))
    session.add(
        ShotExtractedCandidate(
            id=new_id("cand"),
            shot_id=shot.id,
            candidate_type=ShotCandidateType.character,
            name="路人甲",
        )
    )
    await session.commit()

    await session.delete(shot)
    await session.commit()

    assert await session.scalar(select(func.count()).select_from(ShotDetail)) == 0
    assert await session.scalar(select(func.count()).select_from(ShotExtractedCandidate)) == 0


async def test_deleting_task_cascades_to_links(session: AsyncSession) -> None:
    """刪除任務應清除其產物關聯。"""

    task = GenerationTask(id=new_id("task"), task_kind=TaskKind.video_generation.value)
    session.add(task)
    await session.flush()

    session.add(
        GenerationTaskLink(
            task_id=task.id,
            resource_type="video",
            relation_type="shot",
            relation_entity_id="shot_x",
        )
    )
    await session.commit()

    await session.delete(task)
    await session.commit()

    assert await session.scalar(select(func.count()).select_from(GenerationTaskLink)) == 0


async def test_deleting_provider_cascades_to_models(session: AsyncSession) -> None:
    """刪除供應商應連帶刪除其模型設定。"""

    provider = Provider(id=new_id("prov"), name="Anthropic", kind=ProviderKind.anthropic)
    session.add(provider)
    await session.flush()

    session.add(
        Model(
            id=new_id("model"),
            provider_id=provider.id,
            name="Opus",
            model_id="claude-opus-5",
            category=ModelCategory.text,
        )
    )
    await session.commit()

    await session.delete(provider)
    await session.commit()

    assert await session.scalar(select(func.count()).select_from(Model)) == 0


async def test_deleting_file_nulls_shot_reference(session: AsyncSession) -> None:
    """刪除檔案時鏡頭的影片引用應設為 NULL，而非刪除整個鏡頭。"""

    project = await _make_project(session)
    chapter = await _make_chapter(session, project)
    shot = await _make_shot(session, chapter)

    video = FileItem(id=new_id("file"), file_type=FileType.video, storage_key="v/1.mp4")
    session.add(video)
    await session.flush()

    shot.generated_video_file_id = video.id
    await session.commit()

    await session.delete(video)
    await session.commit()
    await session.refresh(shot)

    assert shot.generated_video_file_id is None
    # 鏡頭本身必須存活
    assert await session.scalar(select(func.count()).select_from(Shot)) == 1


# ── 預設值 ────────────────────────────────────────────────────────────────────


async def test_shot_defaults(session: AsyncSession) -> None:
    """新建分鏡預設為待確認、未跳過提取、未提取過。"""

    project = await _make_project(session)
    chapter = await _make_chapter(session, project)
    shot = await _make_shot(session, chapter)
    await session.commit()
    await session.refresh(shot)

    assert shot.status is ShotStatus.pending
    assert shot.skip_extraction is False
    assert shot.last_extracted_at is None
    assert shot.generated_video_file_id is None


async def test_candidate_defaults_to_pending(session: AsyncSession) -> None:
    """提取候選預設為待確認，不會自動進入資產庫。"""

    project = await _make_project(session)
    chapter = await _make_chapter(session, project)
    shot = await _make_shot(session, chapter)

    candidate = ShotExtractedCandidate(
        id=new_id("cand"),
        shot_id=shot.id,
        candidate_type=ShotCandidateType.prop,
        name="舊懷錶",
    )
    session.add(candidate)
    await session.commit()
    await session.refresh(candidate)

    assert candidate.status is ShotCandidateStatus.pending
    assert candidate.linked_entity_id is None


async def test_task_defaults(session: AsyncSession) -> None:
    """新建任務預設為 pending、進度 0、未請求取消。"""

    task = GenerationTask(id=new_id("task"), task_kind=TaskKind.script_divide.value)
    session.add(task)
    await session.commit()
    await session.refresh(task)

    assert task.status is TaskStatus.pending
    assert task.progress == 0
    assert task.cancel_requested is False
    assert task.started_at is None
    assert task.finished_at is None
    assert task.retry_count == 0


async def test_project_defaults_to_vertical_ratio(session: AsyncSession) -> None:
    """短劇以直式為主，預設比例應為 9:16。"""

    project = await _make_project(session)
    await session.commit()
    await session.refresh(project)

    assert project.default_video_ratio == "9:16"
    assert project.unify_style is True


# ── 任務衍生屬性 ──────────────────────────────────────────────────────────────


def test_elapsed_seconds_is_none_before_start() -> None:
    """尚未開始的任務沒有耗時。"""

    assert GenerationTask(id="t", task_kind="x").elapsed_seconds is None


def test_elapsed_seconds_uses_finished_at_when_done() -> None:
    """已結束的任務以 finished_at 計算耗時。"""

    started = datetime(2026, 1, 1, tzinfo=UTC)
    task = GenerationTask(
        id="t",
        task_kind="x",
        started_at=started,
        finished_at=started + timedelta(seconds=42),
    )
    assert task.elapsed_seconds == 42.0


def test_running_task_reports_growing_elapsed() -> None:
    """執行中的任務應回報至今經過的時間，而非 None。"""

    task = GenerationTask(
        id="t",
        task_kind="x",
        started_at=datetime.now(UTC) - timedelta(seconds=5),
    )
    elapsed = task.elapsed_seconds
    assert elapsed is not None and elapsed >= 5.0


def test_is_cancellable_only_for_active_untouched_tasks() -> None:
    """僅未請求取消的進行中任務可被取消。"""

    running = GenerationTask(id="t", task_kind="x", status=TaskStatus.running)
    assert running.is_cancellable is True

    already_requested = GenerationTask(id="t", task_kind="x", status=TaskStatus.running, cancel_requested=True)
    assert already_requested.is_cancellable is False

    finished = GenerationTask(id="t", task_kind="x", status=TaskStatus.succeeded)
    assert finished.is_cancellable is False


# ── 安全性 ────────────────────────────────────────────────────────────────────


async def test_provider_stores_env_var_name_not_secret(session: AsyncSession) -> None:
    """Provider 只存環境變數名稱，模型不得有存放金鑰的欄位。

    這是「金鑰不落地」的結構性保證。
    """

    provider = Provider(
        id=new_id("prov"),
        name="OpenAI",
        kind=ProviderKind.openai,
        api_key_env="OPENAI_API_KEY",
    )
    session.add(provider)
    await session.commit()

    assert provider.api_key_env == "OPENAI_API_KEY"

    columns = set(Provider.__table__.columns.keys())
    for forbidden in ("api_key", "api_secret", "secret", "token", "password"):
        assert forbidden not in columns, f"Provider 不得有 {forbidden} 欄位"


# ── JSON 欄位 ─────────────────────────────────────────────────────────────────


async def test_json_columns_roundtrip(session: AsyncSession) -> None:
    """JSON 欄位需能正確往返，任務 payload 與結果依賴此行為。"""

    task = GenerationTask(
        id=new_id("task"),
        task_kind=TaskKind.frame_image_generation.value,
        payload={"prompt": "夜晚的街道", "size": "1024x1024", "refs": ["a", "b"]},
        result={"files": [{"id": "file_1", "seed": 7}]},
    )
    session.add(task)
    await session.commit()
    await session.refresh(task)

    assert task.payload["refs"] == ["a", "b"]
    assert task.result is not None
    assert task.result["files"][0]["seed"] == 7


async def test_prompt_template_variables_roundtrip(session: AsyncSession) -> None:
    """模板變數清單需正確保存，前端據此提示可用變數。"""

    template = PromptTemplate(
        id=new_id("tpl"),
        category=PromptCategory.video_prompt,
        name="預設影片提示詞",
        content="{style}, {shot_description}, {duration}s",
        variables=["style", "shot_description", "duration"],
    )
    session.add(template)
    await session.commit()
    await session.refresh(template)

    assert template.variables == ["style", "shot_description", "duration"]


# ── 命名慣例 ──────────────────────────────────────────────────────────────────


def test_all_tables_use_studio_prefix() -> None:
    """所有資料表必須帶 `studio_` 前綴，確保與 Scott 既有資料表隔離。"""

    from studio.core.db import Base

    offenders = [name for name in Base.metadata.tables if not name.startswith("studio_")]
    assert offenders == [], f"缺少 studio_ 前綴：{offenders}"
