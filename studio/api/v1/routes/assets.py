"""資產 API：角色、演員、場景、道具、服裝。

五類資產的端點形狀完全相同，因此以工廠函式產生 router：
手寫五份會產生 5 倍的維護負擔，且容易出現不一致的行為。
工廠仍為每類資產綁定各自的 Pydantic schema 與 `operation_id`，
因此 OpenAPI 產生的前端型別依然是分開且精確的。
"""

# 注意：本模組刻意**不使用** `from __future__ import annotations`。
# 端點是由工廠函式產生的，型別註記引用的是閉包變數（create_schema 等）；
# 延後評估會讓它們變成無法解析的字串 ForwardRef，FastAPI 便無法產生 schema。

from typing import Annotated, Any

from fastapi import APIRouter, Query, status

from studio.core.deps import DbSession, Paging
from studio.core.security import CurrentUser
from studio.models.types import AssetKind
from studio.schemas.asset import (
    ActorCreate,
    ActorRead,
    ActorUpdate,
    AssetImageCreate,
    AssetImageRead,
    CharacterCreate,
    CharacterRead,
    CharacterUpdate,
    CostumeCreate,
    CostumeRead,
    CostumeUpdate,
    PropCreate,
    PropRead,
    PropUpdate,
    SceneCreate,
    SceneRead,
    SceneUpdate,
)
from studio.schemas.common import ApiResponse, OkData, Page
from studio.services.asset import AssetService


class NameExists(ApiResponse[dict]):
    """名稱查重結果（僅作為文件用途的別名）。"""


def build_asset_router(
    *,
    kind: AssetKind,
    plural: str,
    singular: str,
    create_schema: type,
    update_schema: type,
    read_schema: type,
    project_scoped: bool,
) -> APIRouter:
    """為單一資產類型建立完整的 CRUD router。

    Args:
        kind: 資產類型。
        plural: 路徑用的複數名稱，例如 `characters`。
        singular: `operation_id` 用的單數名稱，例如 `Character`。
        create_schema / update_schema / read_schema: 該資產的 Pydantic 模型。
        project_scoped: 是否綁定專案（角色／場景／道具／服裝為 True，演員為 False）。

    Returns:
        設定完成的 `APIRouter`。
    """

    router = APIRouter(tags=[plural])
    list_path = f"/projects/{{project_id}}/{plural}" if project_scoped else f"/{plural}"

    @router.get(
        list_path,
        response_model=ApiResponse[Page[read_schema]],
        operation_id=f"list{singular}s",
        summary=f"列出{plural}",
    )
    async def list_assets(  # type: ignore[misc]
        db: DbSession,
        _user: CurrentUser,
        paging: Paging,
        project_id: str | None = None,
        search: Annotated[str | None, Query(description="以名稱、描述做模糊搜尋")] = None,
        order_by: Annotated[str | None, Query(description="排序欄位；前綴 - 表示遞減")] = None,
    ) -> Any:
        """分頁列出資產。"""

        service: AssetService = AssetService(db, kind)
        items, total = await service.list_assets(
            offset=paging.offset,
            limit=paging.limit,
            project_id=project_id,
            search=search,
            order_by=order_by,
        )
        return ApiResponse.ok(
            Page.build(
                [read_schema.model_validate(item) for item in items],
                page=paging.page,
                page_size=paging.page_size,
                total=total,
            )
        )

    @router.get(
        f"/{plural}/{{asset_id}}",
        response_model=ApiResponse[read_schema],
        operation_id=f"get{singular}",
        summary=f"取得{singular}",
    )
    async def get_asset(asset_id: str, db: DbSession, _user: CurrentUser) -> Any:  # type: ignore[misc]
        """取得單一資產。"""

        entity = await AssetService(db, kind).get_asset(asset_id)
        return ApiResponse.ok(read_schema.model_validate(entity))

    @router.post(
        list_path,
        response_model=ApiResponse[read_schema],
        status_code=status.HTTP_201_CREATED,
        operation_id=f"create{singular}",
        summary=f"建立{singular}",
    )
    async def create_asset(  # type: ignore[misc]
        payload: create_schema,  # type: ignore[valid-type]
        db: DbSession,
        _user: CurrentUser,
        project_id: str | None = None,
    ) -> Any:
        """建立資產。"""

        entity = await AssetService(db, kind).create_asset(payload, project_id=project_id)
        return ApiResponse.ok(read_schema.model_validate(entity))

    @router.patch(
        f"/{plural}/{{asset_id}}",
        response_model=ApiResponse[read_schema],
        operation_id=f"update{singular}",
        summary=f"更新{singular}",
    )
    async def update_asset(  # type: ignore[misc]
        asset_id: str,
        payload: update_schema,  # type: ignore[valid-type]
        db: DbSession,
        _user: CurrentUser,
    ) -> Any:
        """更新資產；未傳入的欄位保留原值。"""

        entity = await AssetService(db, kind).update_asset(asset_id, payload)
        return ApiResponse.ok(read_schema.model_validate(entity))

    @router.delete(
        f"/{plural}/{{asset_id}}",
        response_model=ApiResponse[OkData],
        operation_id=f"delete{singular}",
        summary=f"刪除{singular}",
    )
    async def delete_asset(asset_id: str, db: DbSession, _user: CurrentUser) -> Any:  # type: ignore[misc]
        """刪除資產。"""

        await AssetService(db, kind).delete_asset(asset_id)
        return ApiResponse.ok(OkData())

    @router.get(
        f"/{plural}/exists/check",
        response_model=ApiResponse[dict],
        operation_id=f"check{singular}Exists",
        summary=f"檢查{singular}名稱是否已存在",
    )
    async def check_exists(  # type: ignore[misc]
        db: DbSession,
        _user: CurrentUser,
        name: Annotated[str, Query(description="要檢查的名稱")],
        project_id: str | None = None,
    ) -> Any:
        """檢查名稱是否已存在，引導使用者沿用既有資產而非建立分身。"""

        exists = await AssetService(db, kind).name_exists(name, project_id=project_id)
        return ApiResponse.ok({"name": name, "exists": exists})

    @router.get(
        f"/{plural}/{{asset_id}}/images",
        response_model=ApiResponse[list[AssetImageRead]],
        operation_id=f"list{singular}Images",
        summary=f"列出{singular}參考圖",
    )
    async def list_images(asset_id: str, db: DbSession, _user: CurrentUser) -> Any:  # type: ignore[misc]
        """列出資產的參考圖。"""

        items = await AssetService(db, kind).list_images(asset_id)
        return ApiResponse.ok([AssetImageRead.model_validate(item) for item in items])

    @router.post(
        f"/{plural}/{{asset_id}}/images",
        response_model=ApiResponse[AssetImageRead],
        status_code=status.HTTP_201_CREATED,
        operation_id=f"add{singular}Image",
        summary=f"新增{singular}參考圖",
    )
    async def add_image(  # type: ignore[misc]
        asset_id: str,
        payload: AssetImageCreate,
        db: DbSession,
        _user: CurrentUser,
    ) -> Any:
        """為資產新增參考圖；設為主要時會取消其他圖片的主要標記。"""

        image = await AssetService(db, kind).add_image(asset_id, payload)
        return ApiResponse.ok(AssetImageRead.model_validate(image))

    @router.delete(
        f"/{plural}/images/{{image_id}}",
        response_model=ApiResponse[OkData],
        operation_id=f"delete{singular}Image",
        summary=f"刪除{singular}參考圖",
    )
    async def delete_image(image_id: str, db: DbSession, _user: CurrentUser) -> Any:  # type: ignore[misc]
        """刪除資產參考圖。"""

        await AssetService(db, kind).delete_image(image_id)
        return ApiResponse.ok(OkData())

    return router


character_router = build_asset_router(
    kind=AssetKind.character,
    plural="characters",
    singular="Character",
    create_schema=CharacterCreate,
    update_schema=CharacterUpdate,
    read_schema=CharacterRead,
    project_scoped=True,
)

actor_router = build_asset_router(
    kind=AssetKind.actor,
    plural="actors",
    singular="Actor",
    create_schema=ActorCreate,
    update_schema=ActorUpdate,
    read_schema=ActorRead,
    project_scoped=False,
)

scene_router = build_asset_router(
    kind=AssetKind.scene,
    plural="scenes",
    singular="Scene",
    create_schema=SceneCreate,
    update_schema=SceneUpdate,
    read_schema=SceneRead,
    project_scoped=True,
)

prop_router = build_asset_router(
    kind=AssetKind.prop,
    plural="props",
    singular="Prop",
    create_schema=PropCreate,
    update_schema=PropUpdate,
    read_schema=PropRead,
    project_scoped=True,
)

costume_router = build_asset_router(
    kind=AssetKind.costume,
    plural="costumes",
    singular="Costume",
    create_schema=CostumeCreate,
    update_schema=CostumeUpdate,
    read_schema=CostumeRead,
    project_scoped=True,
)
