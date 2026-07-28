"""圖片與影片生成任務執行器。

產物一律寫入物件儲存並登記為 `FileItem`，再透過 `GenerationTaskLink`
關聯回業務實體 —— 因此重新整理頁面後仍看得到結果，且可追蹤每個產物
是否被採用。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx
from sqlalchemy import select

from studio.core import ids
from studio.core.db import session_scope
from studio.core.errors import ProviderError, ValidationError
from studio.core.storage import get_storage
from studio.models.asset import Character, Costume, Prop, Scene, ShotAssetLink
from studio.models.file import FileItem
from studio.models.project import Chapter, Project
from studio.models.shot import Shot, ShotDetail, ShotFrame
from studio.models.task import GenerationTaskLink
from studio.models.types import (
    AssetKind,
    FileType,
    ModelCategory,
    ShotFrameType,
    TaskKind,
    TaskLinkStatus,
)
from studio.tasks.registry import register_executor
from studio.tasks.runtime import TaskContext

logger = logging.getLogger("studio.tasks.media")

_ASSET_MODELS = {
    AssetKind.character: Character,
    AssetKind.scene: Scene,
    AssetKind.prop: Prop,
    AssetKind.costume: Costume,
}


async def build_shot_context(shot_id: str) -> dict[str, Any]:
    """組出分鏡的完整生成脈絡。

    把專案風格與所有已關聯資產的外觀描述聚合起來 ——
    每次生成都帶上同一組描述，是跨鏡頭一致性的實作基礎。
    """

    async with session_scope() as session:
        shot = await session.get(Shot, shot_id)
        if shot is None:
            raise ValidationError(f"分鏡不存在：{shot_id}")

        detail = await session.get(ShotDetail, shot_id)
        chapter = await session.get(Chapter, shot.chapter_id)
        project = await session.get(Project, chapter.project_id) if chapter else None

        links = list(
            (await session.execute(select(ShotAssetLink).where(ShotAssetLink.shot_id == shot_id)))
            .scalars()
            .all()
        )

        assets: list[str] = []
        for link in links:
            model = _ASSET_MODELS.get(link.asset_kind)
            if model is None:
                continue
            asset = await session.get(model, link.asset_id)
            if asset is None:
                continue
            appearance = asset.appearance_prompt or asset.description
            assets.append(f"[{link.asset_kind.value}] {asset.name}：{appearance}")

        ratio = (detail.override_video_ratio if detail else None) or (
            project.default_video_ratio if project else "9:16"
        )

        lines = [
            f"專案風格：{project.style_prompt or project.genre if project else ''}",
            f"畫面表現：{project.visual_style.value if project else 'live_action'}",
            f"分鏡標題：{shot.title}",
            f"畫面描述：{detail.description if detail else ''}",
            f"景別：{detail.camera_shot.value if detail else 'MS'}",
            f"機位：{detail.angle.value if detail else 'EYE_LEVEL'}",
            f"運鏡：{detail.movement.value if detail else 'STATIC'}",
            f"時長：{detail.duration_seconds if detail else 5} 秒",
            f"氛圍：{detail.atmosphere if detail else ''}",
            f"動作節拍：{'、'.join(detail.action_beats) if detail and detail.action_beats else ''}",
            f"畫面比例：{ratio}",
        ]
        if assets:
            lines.append("出場資產（必須嚴格沿用以下外觀描述）：\n" + "\n".join(assets))

        return {
            "shot_id": shot_id,
            "project_id": project.id if project else None,
            "chapter_id": shot.chapter_id,
            "ratio": ratio,
            "duration_seconds": detail.duration_seconds if detail else 5,
            "seed": project.seed if project else 0,
            "video_prompt": detail.video_prompt if detail else "",
            "prompt_context": "\n".join(line for line in lines if line.split("：", 1)[-1].strip()),
        }


async def _persist_asset(
    asset,
    *,
    file_type: FileType,
    project_id: str | None,
    task_id: str,
    filename_hint: str,
) -> str:
    """把生成產物寫入物件儲存並登記為 `FileItem`。

    供應商可能回傳位元組或暫時性 URL；URL 形式必須立刻下載，
    否則連結過期後產物就永久遺失。

    Returns:
        新建的 `FileItem.id`。
    """

    data = asset.data

    if data is None and asset.url:
        try:
            response = httpx.get(asset.url, timeout=120.0, follow_redirects=True)
            response.raise_for_status()
            data = response.content
        except httpx.HTTPError as exc:
            raise ProviderError(f"下載生成產物失敗：{exc}", provider="storage", retryable=True) from exc

    if not data:
        raise ProviderError("生成產物既無位元組也無可下載的 URL", provider="storage", retryable=False)

    extension = "mp4" if file_type is FileType.video else "png"
    file_id = ids.new_id(ids.FILE)
    storage_key = f"{project_id or 'shared'}/{file_type.value}/{file_id}.{extension}"

    storage = get_storage()
    await asyncio.to_thread(
        storage.put_bytes,
        storage_key,
        data,
        content_type=asset.content_type or f"{file_type.value}/{extension}",
    )

    async with session_scope() as session:
        session.add(
            FileItem(
                id=file_id,
                file_type=file_type,
                storage_key=storage_key,
                filename=f"{filename_hint}.{extension}",
                content_type=asset.content_type or f"{file_type.value}/{extension}",
                size_bytes=len(data),
                width=asset.width,
                height=asset.height,
                duration_seconds=asset.duration_seconds,
                project_id=project_id,
                source_task_id=task_id,
                file_metadata={"seed": asset.seed, **asset.metadata},
            )
        )

    return file_id


async def _link_result(
    task_id: str,
    *,
    resource_type: str,
    relation_type: str,
    relation_entity_id: str,
    file_id: str,
) -> None:
    """把產物關聯回業務實體。"""

    async with session_scope() as session:
        session.add(
            GenerationTaskLink(
                task_id=task_id,
                resource_type=resource_type,
                relation_type=relation_type,
                relation_entity_id=relation_entity_id,
                file_id=file_id,
                status=TaskLinkStatus.todo,
            )
        )


# ── 分鏡關鍵幀圖片生成 ───────────────────────────────────────────────────────


async def _execute_frame_image_generation(context: TaskContext) -> dict[str, Any]:
    """為分鏡的關鍵幀生成圖片。"""

    from studio.contracts.generation import ImageRequest
    from studio.integrations import get_image_provider
    from studio.models.types import ProviderKind
    from studio.tasks.resolve import resolve_model

    shot_id = context.shot_id or context.payload.get("shot_id")
    if not shot_id:
        raise ValidationError("frame_image_generation 任務需要 shot_id")

    frame_types = context.payload.get("frame_types") or ["first"]

    await context.progress(10, "載入分鏡脈絡")
    shot_context = await build_shot_context(shot_id)

    resolved = await resolve_model(ModelCategory.image, model_id=context.model_id, capability="image")
    provider = get_image_provider(ProviderKind(resolved.provider_kind))

    produced: list[dict[str, str]] = []

    for position, frame_type_value in enumerate(frame_types, start=1):
        await context.progress(
            10 + int(position / len(frame_types) * 80),
            f"生成 {frame_type_value} 幀",
        )

        frame_type = ShotFrameType(frame_type_value)

        async with session_scope() as session:
            frame = await session.scalar(
                select(ShotFrame).where(ShotFrame.shot_id == shot_id, ShotFrame.frame_type == frame_type)
            )
            prompt = frame.prompt if frame else ""
            frame_id = frame.id if frame else None

        if not prompt:
            # 沒有專屬提示詞時退回分鏡脈絡，避免整個任務失敗。
            prompt = shot_context["prompt_context"]

        request = ImageRequest(
            model=resolved.model_name,
            prompt=prompt,
            size=str(resolved.params.get("size", "1024x1024")),
            count=1,
            seed=int(shot_context.get("seed") or 0),
            params={k: v for k, v in resolved.params.items() if k != "size"},
        )

        result = await asyncio.to_thread(provider.generate_image, resolved.config, request)
        await context.check_cancelled()

        for asset in result.assets:
            file_id = await _persist_asset(
                asset,
                file_type=FileType.image,
                project_id=shot_context.get("project_id"),
                task_id=context.task_id,
                filename_hint=f"{shot_id}-{frame_type.value}",
            )
            await _link_result(
                context.task_id,
                resource_type="image",
                relation_type="shot_frame",
                relation_entity_id=frame_id or shot_id,
                file_id=file_id,
            )
            produced.append({"frame_type": frame_type.value, "file_id": file_id})

            # 幀尚未採用任何圖片時自動帶入第一張，讓流程可以繼續往下走。
            if frame_id:
                async with session_scope() as session:
                    frame = await session.get(ShotFrame, frame_id)
                    if frame is not None and not frame.file_id:
                        frame.file_id = file_id

    await context.progress(100, "完成")
    return {"shot_id": shot_id, "files": produced, "count": len(produced)}


# ── 資產參考圖生成 ───────────────────────────────────────────────────────────


async def _execute_asset_image_generation(context: TaskContext) -> dict[str, Any]:
    """為資產（角色／場景／道具／服裝）生成參考圖。"""

    from studio.contracts.generation import ImageRequest
    from studio.integrations import get_image_provider
    from studio.models.asset import AssetImage
    from studio.models.types import AssetViewAngle, ProviderKind
    from studio.tasks.resolve import resolve_model

    asset_kind_value = context.payload.get("asset_kind")
    asset_id = context.payload.get("asset_id")
    if not asset_kind_value or not asset_id:
        raise ValidationError("asset_image_generation 任務需要 asset_kind 與 asset_id")

    asset_kind = AssetKind(asset_kind_value)
    model_class = _ASSET_MODELS.get(asset_kind)
    if model_class is None:
        raise ValidationError(f"不支援的資產類型：{asset_kind_value}")

    await context.progress(15, "載入資產")

    async with session_scope() as session:
        asset = await session.get(model_class, asset_id)
        if asset is None:
            raise ValidationError(f"資產不存在：{asset_id}")
        name = asset.name
        appearance = asset.appearance_prompt or asset.description
        project_id = getattr(asset, "project_id", None)

    if not appearance.strip():
        raise ValidationError(f"資產「{name}」尚未填寫外觀描述，無法生成參考圖")

    resolved = await resolve_model(ModelCategory.image, model_id=context.model_id, capability="image")
    provider = get_image_provider(ProviderKind(resolved.provider_kind))

    await context.progress(40, "生成參考圖")

    request = ImageRequest(
        model=resolved.model_name,
        prompt=f"{name}. {appearance}",
        size=str(resolved.params.get("size", "1024x1024")),
        count=int(context.payload.get("count", 1)),
        params={k: v for k, v in resolved.params.items() if k != "size"},
    )

    result = await asyncio.to_thread(provider.generate_image, resolved.config, request)
    await context.check_cancelled()

    await context.progress(80, "儲存產物")

    produced: list[str] = []
    for asset_result in result.assets:
        file_id = await _persist_asset(
            asset_result,
            file_type=FileType.image,
            project_id=project_id,
            task_id=context.task_id,
            filename_hint=f"{asset_kind.value}-{name}",
        )
        produced.append(file_id)

        async with session_scope() as session:
            has_primary = await session.scalar(
                select(AssetImage.id).where(
                    AssetImage.asset_kind == asset_kind,
                    AssetImage.asset_id == asset_id,
                    AssetImage.is_primary == 1,
                )
            )
            session.add(
                AssetImage(
                    id=ids.new_id(ids.ASSET_IMAGE),
                    asset_kind=asset_kind,
                    asset_id=asset_id,
                    file_id=file_id,
                    view_angle=AssetViewAngle.front,
                    # 尚無主要參考圖時，第一張自動設為主要。
                    is_primary=0 if has_primary else 1,
                    prompt=request.prompt,
                )
            )

        await _link_result(
            context.task_id,
            resource_type="image",
            relation_type=asset_kind.value,
            relation_entity_id=asset_id,
            file_id=file_id,
        )

    await context.progress(100, "完成")
    return {"asset_kind": asset_kind.value, "asset_id": asset_id, "file_ids": produced}


# ── 影片生成 ─────────────────────────────────────────────────────────────────


async def _execute_video_generation(context: TaskContext) -> dict[str, Any]:
    """為分鏡生成影片。"""

    from studio.contracts.generation import VideoRequest
    from studio.integrations import get_video_provider
    from studio.models.types import ProviderKind
    from studio.tasks.resolve import resolve_model

    shot_id = context.shot_id or context.payload.get("shot_id")
    if not shot_id:
        raise ValidationError("video_generation 任務需要 shot_id")

    await context.progress(10, "載入分鏡脈絡")
    shot_context = await build_shot_context(shot_id)

    prompt = shot_context.get("video_prompt") or shot_context["prompt_context"]

    # 首幀作為視覺錨點，能顯著降低角色與場景漂移。
    first_frame_bytes: bytes | None = None
    async with session_scope() as session:
        frame = await session.scalar(
            select(ShotFrame).where(
                ShotFrame.shot_id == shot_id, ShotFrame.frame_type == ShotFrameType.first
            )
        )
        storage_key = None
        if frame is not None and frame.file_id:
            file_item = await session.get(FileItem, frame.file_id)
            storage_key = file_item.storage_key if file_item else None

    if storage_key:
        try:
            first_frame_bytes = await asyncio.to_thread(get_storage().get_bytes, storage_key)
        except Exception:  # noqa: BLE001 - 首幀只是加分項，取不到不應讓整個任務失敗
            logger.warning("could not load first frame for shot %s", shot_id)

    resolved = await resolve_model(ModelCategory.video, model_id=context.model_id, capability="video")
    provider = get_video_provider(ProviderKind(resolved.provider_kind))

    await context.progress(30, "提交影片生成（可能需要數分鐘）")

    request = VideoRequest(
        model=resolved.model_name,
        prompt=prompt,
        duration_seconds=int(shot_context.get("duration_seconds") or 5),
        ratio=str(shot_context.get("ratio") or "9:16"),
        first_frame=first_frame_bytes,
        seed=int(shot_context.get("seed") or 0),
        params=dict(resolved.params),
    )

    result = await asyncio.to_thread(provider.generate_video, resolved.config, request)
    await context.check_cancelled()

    await context.progress(85, "儲存影片")

    produced: list[str] = []
    for asset in result.assets:
        file_id = await _persist_asset(
            asset,
            file_type=FileType.video,
            project_id=shot_context.get("project_id"),
            task_id=context.task_id,
            filename_hint=f"{shot_id}-video",
        )
        produced.append(file_id)

        await _link_result(
            context.task_id,
            resource_type="video",
            relation_type="shot",
            relation_entity_id=shot_id,
            file_id=file_id,
        )

        # 分鏡尚未採用影片時自動帶入第一支。
        async with session_scope() as session:
            shot = await session.get(Shot, shot_id)
            if shot is not None and not shot.generated_video_file_id:
                shot.generated_video_file_id = file_id

    await context.progress(100, "完成")
    return {"shot_id": shot_id, "file_ids": produced, "count": len(produced)}


register_executor(TaskKind.frame_image_generation.value, _execute_frame_image_generation)
register_executor(TaskKind.asset_image_generation.value, _execute_asset_image_generation)
register_executor(TaskKind.video_generation.value, _execute_video_generation)
