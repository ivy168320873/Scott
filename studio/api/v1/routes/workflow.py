"""工作流端點：提交 AI 任務。

這些端點只負責**建立並派送任務**，立即回傳任務物件；
長時間的模型呼叫一律在 worker 或背景執行緒中進行，
絕不阻塞 HTTP 請求。

前端拿到任務 ID 後以任務中心輪詢進度。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, status
from pydantic import BaseModel, Field

from studio.core.deps import DbSession
from studio.core.security import CurrentUser
from studio.models.types import AssetKind, TaskKind
from studio.schemas.common import ApiResponse
from studio.schemas.task import TaskCreate, TaskRead
from studio.services.project import ProjectService
from studio.services.shot import ShotService
from studio.services.task import TaskService

router = APIRouter(tags=["workflow"])


class ScriptTaskOptions(BaseModel):
    """腳本類任務的共用選項。"""

    model_id: str | None = Field(default=None, description="指定文字模型；留空使用預設")
    replace_existing: bool = Field(default=True, description="重新拆解時是否清除既有分鏡")
    max_retries: int = Field(default=1, ge=0, le=5, description="失敗時的重試次數")


class ImageTaskOptions(BaseModel):
    """圖片生成選項。"""

    model_id: str | None = Field(default=None, description="指定圖片模型；留空使用預設")
    frame_types: list[str] = Field(default_factory=lambda: ["first"], description="要生成的幀類型")
    max_retries: int = Field(default=1, ge=0, le=5, description="重試次數")


class AssetImageTaskOptions(BaseModel):
    """資產參考圖生成選項。"""

    asset_kind: AssetKind = Field(description="資產類型")
    asset_id: str = Field(min_length=1, description="資產 ID")
    model_id: str | None = Field(default=None, description="指定圖片模型")
    count: int = Field(default=1, ge=1, le=4, description="生成張數")


class VideoTaskOptions(BaseModel):
    """影片生成選項。"""

    model_id: str | None = Field(default=None, description="指定影片模型；留空使用預設")
    max_retries: int = Field(default=0, ge=0, le=3, description="重試次數（影片成本高，預設不重試）")


class BatchShotRequest(BaseModel):
    """批次生成請求。"""

    shot_ids: list[str] = Field(min_length=1, max_length=50, description="要處理的分鏡 ID")
    model_id: str | None = Field(default=None, description="指定模型")


class BatchResult(BaseModel):
    """批次提交結果。"""

    task_ids: list[str] = Field(description="已建立的任務 ID")
    skipped: list[dict[str, str]] = Field(default_factory=list, description="被略過的分鏡與原因")


async def _submit(
    db,
    *,
    kind: TaskKind,
    payload: dict[str, Any],
    chapter_id: str | None = None,
    shot_id: str | None = None,
    project_id: str | None = None,
    model_id: str | None = None,
    max_retries: int = 1,
) -> TaskRead:
    """建立並派送一個任務。"""

    task = await TaskService(db).create_task(
        TaskCreate(
            task_kind=kind,
            payload=payload,
            project_id=project_id,
            chapter_id=chapter_id,
            shot_id=shot_id,
            model_id=model_id,
            max_retries=max_retries,
        )
    )
    return TaskRead.model_validate(task)


# ── 腳本工作流 ────────────────────────────────────────────────────────────────


@router.post(
    "/chapters/{chapter_id}/analyze",
    response_model=ApiResponse[TaskRead],
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="analyzeChapterScript",
    summary="提交腳本拆分鏡任務",
)
async def analyze_chapter(
    chapter_id: str,
    payload: ScriptTaskOptions,
    db: DbSession,
    _user: CurrentUser,
) -> ApiResponse[TaskRead]:
    """把章節腳本拆解成分鏡。回傳任務物件，實際工作在背景執行。"""

    chapter = await ProjectService(db).get_chapter(chapter_id)
    task = await _submit(
        db,
        kind=TaskKind.script_divide,
        payload={"replace_existing": payload.replace_existing},
        chapter_id=chapter_id,
        project_id=chapter.project_id,
        model_id=payload.model_id,
        max_retries=payload.max_retries,
    )
    return ApiResponse.ok(task)


@router.post(
    "/chapters/{chapter_id}/extract",
    response_model=ApiResponse[TaskRead],
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="extractChapterEntities",
    summary="提交實體提取任務",
)
async def extract_chapter_entities(
    chapter_id: str,
    payload: ScriptTaskOptions,
    db: DbSession,
    _user: CurrentUser,
) -> ApiResponse[TaskRead]:
    """從章節的所有分鏡提取角色／場景／道具／服裝／對白候選。"""

    chapter = await ProjectService(db).get_chapter(chapter_id)
    task = await _submit(
        db,
        kind=TaskKind.script_extract,
        payload={},
        chapter_id=chapter_id,
        project_id=chapter.project_id,
        model_id=payload.model_id,
        max_retries=payload.max_retries,
    )
    return ApiResponse.ok(task)


@router.post(
    "/chapters/{chapter_id}/simplify",
    response_model=ApiResponse[TaskRead],
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="simplifyChapterScript",
    summary="提交腳本精簡任務",
)
async def simplify_chapter(
    chapter_id: str,
    payload: ScriptTaskOptions,
    db: DbSession,
    _user: CurrentUser,
) -> ApiResponse[TaskRead]:
    """精簡腳本並寫入 condensed_text，供後續分析節省 token。"""

    chapter = await ProjectService(db).get_chapter(chapter_id)
    task = await _submit(
        db,
        kind=TaskKind.script_simplify,
        payload={},
        chapter_id=chapter_id,
        project_id=chapter.project_id,
        model_id=payload.model_id,
        max_retries=payload.max_retries,
    )
    return ApiResponse.ok(task)


@router.post(
    "/chapters/{chapter_id}/optimize",
    response_model=ApiResponse[TaskRead],
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="optimizeChapterScript",
    summary="提交腳本優化任務",
)
async def optimize_chapter(
    chapter_id: str,
    payload: ScriptTaskOptions,
    db: DbSession,
    _user: CurrentUser,
) -> ApiResponse[TaskRead]:
    """產生優化建議稿；結果放在任務 result 中，不直接覆蓋原文。"""

    chapter = await ProjectService(db).get_chapter(chapter_id)
    task = await _submit(
        db,
        kind=TaskKind.script_optimize,
        payload={},
        chapter_id=chapter_id,
        project_id=chapter.project_id,
        model_id=payload.model_id,
        max_retries=payload.max_retries,
    )
    return ApiResponse.ok(task)


@router.post(
    "/chapters/{chapter_id}/consistency-check",
    response_model=ApiResponse[TaskRead],
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="checkChapterConsistency",
    summary="提交一致性檢查任務",
)
async def check_chapter_consistency(
    chapter_id: str,
    payload: ScriptTaskOptions,
    db: DbSession,
    _user: CurrentUser,
) -> ApiResponse[TaskRead]:
    """檢查分鏡序列的敘事與視覺一致性問題。"""

    chapter = await ProjectService(db).get_chapter(chapter_id)
    task = await _submit(
        db,
        kind=TaskKind.script_consistency,
        payload={},
        chapter_id=chapter_id,
        project_id=chapter.project_id,
        model_id=payload.model_id,
        max_retries=payload.max_retries,
    )
    return ApiResponse.ok(task)


# ── 分鏡生成工作流 ────────────────────────────────────────────────────────────


@router.post(
    "/shots/{shot_id}/generate-prompts",
    response_model=ApiResponse[TaskRead],
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="generateShotPrompts",
    summary="提交分鏡提示詞生成任務",
)
async def generate_shot_prompts(
    shot_id: str,
    payload: ScriptTaskOptions,
    db: DbSession,
    _user: CurrentUser,
) -> ApiResponse[TaskRead]:
    """為分鏡產生首尾幀圖片提示詞與影片提示詞。"""

    await ShotService(db).get_shot(shot_id)
    task = await _submit(
        db,
        kind=TaskKind.shot_frame_prompt,
        payload={},
        shot_id=shot_id,
        model_id=payload.model_id,
        max_retries=payload.max_retries,
    )
    return ApiResponse.ok(task)


@router.post(
    "/shots/{shot_id}/generate-images",
    response_model=ApiResponse[TaskRead],
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="generateShotImages",
    summary="提交分鏡關鍵幀圖片生成任務",
)
async def generate_shot_images(
    shot_id: str,
    payload: ImageTaskOptions,
    db: DbSession,
    _user: CurrentUser,
) -> ApiResponse[TaskRead]:
    """為分鏡的關鍵幀生成圖片。"""

    await ShotService(db).get_shot(shot_id)
    task = await _submit(
        db,
        kind=TaskKind.frame_image_generation,
        payload={"frame_types": payload.frame_types},
        shot_id=shot_id,
        model_id=payload.model_id,
        max_retries=payload.max_retries,
    )
    return ApiResponse.ok(task)


@router.post(
    "/shots/{shot_id}/generate-video",
    response_model=ApiResponse[TaskRead],
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="generateShotVideo",
    summary="提交分鏡影片生成任務",
)
async def generate_shot_video(
    shot_id: str,
    payload: VideoTaskOptions,
    db: DbSession,
    _user: CurrentUser,
) -> ApiResponse[TaskRead]:
    """為分鏡生成影片。

    不強制要求 video-readiness 通過 —— 使用者可能刻意先試跑。
    準備度資訊由 `GET /shots/{id}/readiness` 提供，由前端提示。
    """

    await ShotService(db).get_shot(shot_id)
    task = await _submit(
        db,
        kind=TaskKind.video_generation,
        payload={},
        shot_id=shot_id,
        model_id=payload.model_id,
        max_retries=payload.max_retries,
    )
    return ApiResponse.ok(task)


@router.post(
    "/assets/generate-image",
    response_model=ApiResponse[TaskRead],
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="generateAssetImage",
    summary="提交資產參考圖生成任務",
)
async def generate_asset_image(
    payload: AssetImageTaskOptions,
    db: DbSession,
    _user: CurrentUser,
) -> ApiResponse[TaskRead]:
    """為角色／場景／道具／服裝生成參考圖。"""

    task = await _submit(
        db,
        kind=TaskKind.asset_image_generation,
        payload={
            "asset_kind": payload.asset_kind.value,
            "asset_id": payload.asset_id,
            "count": payload.count,
        },
        model_id=payload.model_id,
    )
    return ApiResponse.ok(task)


# ── 批次 ──────────────────────────────────────────────────────────────────────


@router.post(
    "/shots/batch/generate-images",
    response_model=ApiResponse[BatchResult],
    status_code=status.HTTP_202_ACCEPTED,
    operation_id="batchGenerateShotImages",
    summary="批次提交圖片生成",
)
async def batch_generate_images(
    payload: BatchShotRequest,
    db: DbSession,
    _user: CurrentUser,
) -> ApiResponse[BatchResult]:
    """為多個分鏡批次提交圖片生成。

    每個分鏡各建一筆任務，讓進度與失敗可以個別追蹤與重試；
    找不到的分鏡會被略過並列在 `skipped`，不讓整批失敗。
    """

    service = ShotService(db)
    task_ids: list[str] = []
    skipped: list[dict[str, str]] = []

    for shot_id in payload.shot_ids:
        try:
            await service.get_shot(shot_id)
        except Exception as exc:  # noqa: BLE001 - 單一分鏡失敗不應中斷整批
            skipped.append({"shot_id": shot_id, "reason": str(exc)})
            continue

        task = await _submit(
            db,
            kind=TaskKind.frame_image_generation,
            payload={"frame_types": ["first"]},
            shot_id=shot_id,
            model_id=payload.model_id,
        )
        task_ids.append(task.id)

    return ApiResponse.ok(BatchResult(task_ids=task_ids, skipped=skipped))
