"""媒體檔案 API。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, status

from studio.core.deps import DbSession, Paging, StorageDep
from studio.core.security import CurrentUser
from studio.models.types import FileType
from studio.schemas.asset import (
    MediaCreate,
    MediaRead,
    MediaUpdate,
    MediaUsageCreate,
    MediaUsageRead,
)
from studio.schemas.common import ApiResponse, OkData, Page
from studio.services.media import MediaService

router = APIRouter(tags=["media"])


@router.get(
    "/media",
    response_model=ApiResponse[Page[MediaRead]],
    operation_id="listMedia",
    summary="列出媒體檔案",
)
async def list_media(
    db: DbSession,
    storage: StorageDep,
    _user: CurrentUser,
    paging: Paging,
    search: Annotated[str | None, Query(description="以檔名做模糊搜尋")] = None,
    project_id: Annotated[str | None, Query(description="依專案過濾")] = None,
    file_type: Annotated[FileType | None, Query(description="依檔案類型過濾")] = None,
) -> ApiResponse[Page[MediaRead]]:
    """分頁列出媒體檔案。"""

    service = MediaService(db, storage)
    items, total = await service.list_media(
        offset=paging.offset, limit=paging.limit, search=search, project_id=project_id, file_type=file_type
    )
    return ApiResponse.ok(
        Page.build(
            [service.to_read(item) for item in items],
            page=paging.page,
            page_size=paging.page_size,
            total=total,
        )
    )


@router.get(
    "/media/{file_id}",
    response_model=ApiResponse[MediaRead],
    operation_id="getMedia",
    summary="取得媒體檔案",
)
async def get_media(
    file_id: str,
    db: DbSession,
    storage: StorageDep,
    _user: CurrentUser,
) -> ApiResponse[MediaRead]:
    """取得單一媒體檔案的中繼資料。"""

    service = MediaService(db, storage)
    return ApiResponse.ok(service.to_read(await service.get_media(file_id)))


@router.post(
    "/media",
    response_model=ApiResponse[MediaRead],
    status_code=status.HTTP_201_CREATED,
    operation_id="createMedia",
    summary="登記媒體檔案",
)
async def create_media(
    payload: MediaCreate,
    db: DbSession,
    storage: StorageDep,
    _user: CurrentUser,
) -> ApiResponse[MediaRead]:
    """登記一筆媒體檔案中繼資料。"""

    service = MediaService(db, storage)
    return ApiResponse.ok(service.to_read(await service.create_media(payload)))


@router.patch(
    "/media/{file_id}",
    response_model=ApiResponse[MediaRead],
    operation_id="updateMedia",
    summary="更新媒體中繼資料",
)
async def update_media(
    file_id: str,
    payload: MediaUpdate,
    db: DbSession,
    storage: StorageDep,
    _user: CurrentUser,
) -> ApiResponse[MediaRead]:
    """更新媒體中繼資料；未傳入的欄位保留原值。"""

    service = MediaService(db, storage)
    return ApiResponse.ok(service.to_read(await service.update_media(file_id, payload)))


@router.delete(
    "/media/{file_id}",
    response_model=ApiResponse[OkData],
    operation_id="deleteMedia",
    summary="刪除媒體檔案",
)
async def delete_media(
    file_id: str,
    db: DbSession,
    storage: StorageDep,
    _user: CurrentUser,
    purge_object: Annotated[bool, Query(description="是否一併刪除物件儲存中的實體檔案（不可回復）")] = False,
) -> ApiResponse[OkData]:
    """刪除媒體紀錄；預設保留實體檔案。"""

    await MediaService(db, storage).delete_media(file_id, purge_object=purge_object)
    return ApiResponse.ok(OkData())


@router.get(
    "/media/{file_id}/usages",
    response_model=ApiResponse[list[MediaUsageRead]],
    operation_id="listMediaUsages",
    summary="列出媒體用途",
)
async def list_media_usages(
    file_id: str,
    db: DbSession,
    storage: StorageDep,
    _user: CurrentUser,
) -> ApiResponse[list[MediaUsageRead]]:
    """列出某檔案被哪些業務資源引用，避免誤刪仍在使用的素材。"""

    items = await MediaService(db, storage).list_usages(file_id)
    return ApiResponse.ok([MediaUsageRead.model_validate(item) for item in items])


@router.post(
    "/media/{file_id}/usages",
    response_model=ApiResponse[MediaUsageRead],
    status_code=status.HTTP_201_CREATED,
    operation_id="addMediaUsage",
    summary="登記媒體用途",
)
async def add_media_usage(
    file_id: str,
    payload: MediaUsageCreate,
    db: DbSession,
    storage: StorageDep,
    _user: CurrentUser,
) -> ApiResponse[MediaUsageRead]:
    """登記檔案在業務鏈上的用途。"""

    usage = await MediaService(db, storage).add_usage(file_id, payload)
    return ApiResponse.ok(MediaUsageRead.model_validate(usage))
