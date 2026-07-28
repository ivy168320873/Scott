"""分鏡與其子資源 API。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, status

from studio.core.deps import DbSession, Paging
from studio.core.security import CurrentUser
from studio.models.types import AssetKind, ShotCandidateStatus, ShotStatus
from studio.schemas.common import ApiResponse, OkData, Page
from studio.schemas.shot import (
    CandidateResolve,
    DialogueCandidateResolve,
    ShotAssetLinkCreate,
    ShotAssetLinkRead,
    ShotCandidateRead,
    ShotCreate,
    ShotDialogueCandidateRead,
    ShotDialogueCreate,
    ShotDialogueRead,
    ShotDialogueUpdate,
    ShotFrameCreate,
    ShotFrameRead,
    ShotFrameUpdate,
    ShotRead,
    ShotReadiness,
    ShotUpdate,
)
from studio.services.shot import ShotService

router = APIRouter(tags=["shots"])


# ── 分鏡 ──────────────────────────────────────────────────────────────────────


@router.get(
    "/chapters/{chapter_id}/shots",
    response_model=ApiResponse[Page[ShotRead]],
    operation_id="listShots",
    summary="列出分鏡",
)
async def list_shots(
    chapter_id: str,
    db: DbSession,
    _user: CurrentUser,
    paging: Paging,
    search: Annotated[str | None, Query(description="以標題、腳本段落做模糊搜尋")] = None,
    shot_status: Annotated[ShotStatus | None, Query(alias="status", description="依提取確認狀態過濾")] = None,
) -> ApiResponse[Page[ShotRead]]:
    """分頁列出某章節的分鏡（依鏡頭序號遞增）。"""

    items, total = await ShotService(db).list_shots(
        chapter_id,
        offset=paging.offset,
        limit=paging.limit,
        search=search,
        status=shot_status,
    )
    return ApiResponse.ok(
        Page.build(
            [ShotRead.model_validate(item) for item in items],
            page=paging.page,
            page_size=paging.page_size,
            total=total,
        )
    )


@router.get(
    "/shots/{shot_id}",
    response_model=ApiResponse[ShotRead],
    operation_id="getShot",
    summary="取得分鏡",
)
async def get_shot(shot_id: str, db: DbSession, _user: CurrentUser) -> ApiResponse[ShotRead]:
    """取得單一分鏡。"""

    shot = await ShotService(db).get_shot(shot_id)
    return ApiResponse.ok(ShotRead.model_validate(shot))


@router.post(
    "/chapters/{chapter_id}/shots",
    response_model=ApiResponse[ShotRead],
    status_code=status.HTTP_201_CREATED,
    operation_id="createShot",
    summary="建立分鏡",
)
async def create_shot(
    chapter_id: str,
    payload: ShotCreate,
    db: DbSession,
    _user: CurrentUser,
) -> ApiResponse[ShotRead]:
    """建立分鏡；序號留空時自動接續。"""

    shot = await ShotService(db).create_shot(chapter_id, payload)
    return ApiResponse.ok(ShotRead.model_validate(shot))


@router.patch(
    "/shots/{shot_id}",
    response_model=ApiResponse[ShotRead],
    operation_id="updateShot",
    summary="更新分鏡",
)
async def update_shot(
    shot_id: str,
    payload: ShotUpdate,
    db: DbSession,
    _user: CurrentUser,
) -> ApiResponse[ShotRead]:
    """更新分鏡；未傳入的欄位保留原值。狀態不可直接寫入。"""

    shot = await ShotService(db).update_shot(shot_id, payload)
    return ApiResponse.ok(ShotRead.model_validate(shot))


@router.delete(
    "/shots/{shot_id}",
    response_model=ApiResponse[OkData],
    operation_id="deleteShot",
    summary="刪除分鏡",
)
async def delete_shot(shot_id: str, db: DbSession, _user: CurrentUser) -> ApiResponse[OkData]:
    """刪除分鏡及其子資源。"""

    await ShotService(db).delete_shot(shot_id)
    return ApiResponse.ok(OkData())


@router.post(
    "/shots/{shot_id}/recompute-status",
    response_model=ApiResponse[ShotRead],
    operation_id="recomputeShotStatus",
    summary="重算分鏡確認狀態",
)
async def recompute_shot_status(shot_id: str, db: DbSession, _user: CurrentUser) -> ApiResponse[ShotRead]:
    """依提取確認進度重算分鏡狀態。"""

    shot = await ShotService(db).recompute_status(shot_id)
    return ApiResponse.ok(ShotRead.model_validate(shot))


@router.get(
    "/shots/{shot_id}/readiness",
    response_model=ApiResponse[ShotReadiness],
    operation_id="getShotReadiness",
    summary="取得分鏡準備度",
)
async def get_shot_readiness(shot_id: str, db: DbSession, _user: CurrentUser) -> ApiResponse[ShotReadiness]:
    """回報分鏡的確認狀態與影片生成準備度（兩者分離）。"""

    data = await ShotService(db).readiness(shot_id)
    return ApiResponse.ok(ShotReadiness.model_validate(data))


@router.post(
    "/shots/{shot_id}/mark-extracted",
    response_model=ApiResponse[ShotRead],
    operation_id="markShotExtracted",
    summary="標記分鏡已完成提取",
)
async def mark_shot_extracted(shot_id: str, db: DbSession, _user: CurrentUser) -> ApiResponse[ShotRead]:
    """標記分鏡已完成一次提取並重算狀態。"""

    shot = await ShotService(db).mark_extracted(shot_id)
    return ApiResponse.ok(ShotRead.model_validate(shot))


# ── 關鍵幀 ────────────────────────────────────────────────────────────────────


@router.get(
    "/shots/{shot_id}/frames",
    response_model=ApiResponse[list[ShotFrameRead]],
    operation_id="listShotFrames",
    summary="列出關鍵幀",
)
async def list_shot_frames(shot_id: str, db: DbSession, _user: CurrentUser) -> ApiResponse[list[ShotFrameRead]]:
    """列出分鏡的所有關鍵幀。"""

    items = await ShotService(db).list_frames(shot_id)
    return ApiResponse.ok([ShotFrameRead.model_validate(item) for item in items])


@router.post(
    "/shots/{shot_id}/frames",
    response_model=ApiResponse[ShotFrameRead],
    status_code=status.HTTP_201_CREATED,
    operation_id="createShotFrame",
    summary="新增關鍵幀",
)
async def create_shot_frame(
    shot_id: str,
    payload: ShotFrameCreate,
    db: DbSession,
    _user: CurrentUser,
) -> ApiResponse[ShotFrameRead]:
    """為分鏡新增關鍵幀。"""

    frame = await ShotService(db).create_frame(shot_id, payload)
    return ApiResponse.ok(ShotFrameRead.model_validate(frame))


@router.patch(
    "/frames/{frame_id}",
    response_model=ApiResponse[ShotFrameRead],
    operation_id="updateShotFrame",
    summary="更新關鍵幀",
)
async def update_shot_frame(
    frame_id: str,
    payload: ShotFrameUpdate,
    db: DbSession,
    _user: CurrentUser,
) -> ApiResponse[ShotFrameRead]:
    """更新關鍵幀；未傳入的欄位保留原值。"""

    frame = await ShotService(db).update_frame(frame_id, payload)
    return ApiResponse.ok(ShotFrameRead.model_validate(frame))


@router.delete(
    "/frames/{frame_id}",
    response_model=ApiResponse[OkData],
    operation_id="deleteShotFrame",
    summary="刪除關鍵幀",
)
async def delete_shot_frame(frame_id: str, db: DbSession, _user: CurrentUser) -> ApiResponse[OkData]:
    """刪除關鍵幀。"""

    await ShotService(db).delete_frame(frame_id)
    return ApiResponse.ok(OkData())


# ── 對白 ──────────────────────────────────────────────────────────────────────


@router.get(
    "/shots/{shot_id}/dialogues",
    response_model=ApiResponse[list[ShotDialogueRead]],
    operation_id="listShotDialogues",
    summary="列出對白",
)
async def list_shot_dialogues(shot_id: str, db: DbSession, _user: CurrentUser) -> ApiResponse[list[ShotDialogueRead]]:
    """列出分鏡的對白（依順序）。"""

    items = await ShotService(db).list_dialogues(shot_id)
    return ApiResponse.ok([ShotDialogueRead.model_validate(item) for item in items])


@router.post(
    "/shots/{shot_id}/dialogues",
    response_model=ApiResponse[ShotDialogueRead],
    status_code=status.HTTP_201_CREATED,
    operation_id="createShotDialogue",
    summary="新增對白",
)
async def create_shot_dialogue(
    shot_id: str,
    payload: ShotDialogueCreate,
    db: DbSession,
    _user: CurrentUser,
) -> ApiResponse[ShotDialogueRead]:
    """新增對白；順序留空時附加於最後。"""

    dialogue = await ShotService(db).create_dialogue(shot_id, payload)
    return ApiResponse.ok(ShotDialogueRead.model_validate(dialogue))


@router.patch(
    "/dialogues/{dialogue_id}",
    response_model=ApiResponse[ShotDialogueRead],
    operation_id="updateShotDialogue",
    summary="更新對白",
)
async def update_shot_dialogue(
    dialogue_id: str,
    payload: ShotDialogueUpdate,
    db: DbSession,
    _user: CurrentUser,
) -> ApiResponse[ShotDialogueRead]:
    """更新對白；未傳入的欄位保留原值。"""

    dialogue = await ShotService(db).update_dialogue(dialogue_id, payload)
    return ApiResponse.ok(ShotDialogueRead.model_validate(dialogue))


@router.delete(
    "/dialogues/{dialogue_id}",
    response_model=ApiResponse[OkData],
    operation_id="deleteShotDialogue",
    summary="刪除對白",
)
async def delete_shot_dialogue(dialogue_id: str, db: DbSession, _user: CurrentUser) -> ApiResponse[OkData]:
    """刪除對白。"""

    await ShotService(db).delete_dialogue(dialogue_id)
    return ApiResponse.ok(OkData())


# ── 提取候選 ──────────────────────────────────────────────────────────────────


@router.get(
    "/shots/{shot_id}/candidates",
    response_model=ApiResponse[list[ShotCandidateRead]],
    operation_id="listShotCandidates",
    summary="列出資產提取候選",
)
async def list_shot_candidates(
    shot_id: str,
    db: DbSession,
    _user: CurrentUser,
    candidate_status: Annotated[ShotCandidateStatus | None, Query(alias="status")] = None,
) -> ApiResponse[list[ShotCandidateRead]]:
    """列出分鏡的資產提取候選。"""

    items = await ShotService(db).list_candidates(shot_id, status=candidate_status)
    return ApiResponse.ok([ShotCandidateRead.model_validate(item) for item in items])


@router.post(
    "/candidates/{candidate_id}/resolve",
    response_model=ApiResponse[ShotCandidateRead],
    operation_id="resolveShotCandidate",
    summary="確認或忽略資產候選",
)
async def resolve_shot_candidate(
    candidate_id: str,
    payload: CandidateResolve,
    db: DbSession,
    _user: CurrentUser,
) -> ApiResponse[ShotCandidateRead]:
    """確認（連結既有資產）或忽略一筆候選。"""

    candidate = await ShotService(db).resolve_candidate(candidate_id, payload)
    return ApiResponse.ok(ShotCandidateRead.model_validate(candidate))


@router.get(
    "/shots/{shot_id}/dialogue-candidates",
    response_model=ApiResponse[list[ShotDialogueCandidateRead]],
    operation_id="listShotDialogueCandidates",
    summary="列出對白提取候選",
)
async def list_shot_dialogue_candidates(
    shot_id: str,
    db: DbSession,
    _user: CurrentUser,
) -> ApiResponse[list[ShotDialogueCandidateRead]]:
    """列出分鏡的對白提取候選。"""

    items = await ShotService(db).list_dialogue_candidates(shot_id)
    return ApiResponse.ok([ShotDialogueCandidateRead.model_validate(item) for item in items])


@router.post(
    "/dialogue-candidates/{candidate_id}/resolve",
    response_model=ApiResponse[ShotDialogueCandidateRead],
    operation_id="resolveShotDialogueCandidate",
    summary="接受或忽略對白候選",
)
async def resolve_shot_dialogue_candidate(
    candidate_id: str,
    payload: DialogueCandidateResolve,
    db: DbSession,
    _user: CurrentUser,
) -> ApiResponse[ShotDialogueCandidateRead]:
    """接受（轉為正式對白）或忽略一筆對白候選。"""

    candidate = await ShotService(db).resolve_dialogue_candidate(candidate_id, payload)
    return ApiResponse.ok(ShotDialogueCandidateRead.model_validate(candidate))


# ── 分鏡資產關聯 ──────────────────────────────────────────────────────────────


@router.get(
    "/shots/{shot_id}/assets",
    response_model=ApiResponse[list[ShotAssetLinkRead]],
    operation_id="listShotAssets",
    summary="列出分鏡引用的資產",
)
async def list_shot_assets(
    shot_id: str,
    db: DbSession,
    _user: CurrentUser,
    asset_kind: Annotated[AssetKind | None, Query(description="依資產類型過濾")] = None,
) -> ApiResponse[list[ShotAssetLinkRead]]:
    """列出分鏡引用的角色／場景／道具／服裝。"""

    items = await ShotService(db).list_asset_links(shot_id, asset_kind=asset_kind)
    return ApiResponse.ok([ShotAssetLinkRead.model_validate(item) for item in items])


@router.post(
    "/shots/{shot_id}/assets",
    response_model=ApiResponse[ShotAssetLinkRead],
    status_code=status.HTTP_201_CREATED,
    operation_id="linkShotAsset",
    summary="關聯資產到分鏡",
)
async def link_shot_asset(
    shot_id: str,
    payload: ShotAssetLinkCreate,
    db: DbSession,
    _user: CurrentUser,
) -> ApiResponse[ShotAssetLinkRead]:
    """把資產關聯到分鏡。"""

    link = await ShotService(db).link_asset(shot_id, payload)
    return ApiResponse.ok(ShotAssetLinkRead.model_validate(link))


@router.delete(
    "/shots/{shot_id}/assets/{asset_kind}/{asset_id}",
    response_model=ApiResponse[OkData],
    operation_id="unlinkShotAsset",
    summary="移除分鏡資產關聯",
)
async def unlink_shot_asset(
    shot_id: str,
    asset_kind: AssetKind,
    asset_id: str,
    db: DbSession,
    _user: CurrentUser,
) -> ApiResponse[OkData]:
    """移除分鏡與資產的關聯。"""

    await ShotService(db).unlink_asset(shot_id, asset_kind, asset_id)
    return ApiResponse.ok(OkData())
