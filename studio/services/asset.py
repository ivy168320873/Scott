"""資產 Service：角色、演員、場景、道具、服裝與其參考圖。

五類資產的 CRUD 行為完全相同，差別只在模型類別與是否綁定專案，
因此以一個泛型 Service 涵蓋，避免五份需要同步維護的相同程式碼。
"""

from __future__ import annotations

from typing import Any, Generic, TypeVar

from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from studio.core import ids
from studio.core.errors import ValidationError
from studio.models.asset import Actor, AssetImage, Character, Costume, Prop, Scene
from studio.models.types import AssetKind
from studio.repositories import (
    ActorRepository,
    AssetImageRepository,
    BaseRepository,
    CharacterRepository,
    CostumeRepository,
    ProjectRepository,
    PropRepository,
    SceneRepository,
)
from studio.schemas.asset import AssetImageCreate
from studio.services.base import apply_updates, ensure_found, translate_integrity_error

AssetT = TypeVar("AssetT")

# 資產類型 → (模型, Repository, ID 前綴, 是否綁定專案, 顯示名稱)
_ASSET_REGISTRY: dict[AssetKind, tuple[Any, type[BaseRepository], str, bool, str]] = {
    AssetKind.character: (Character, CharacterRepository, ids.CHARACTER, True, "角色"),
    AssetKind.actor: (Actor, ActorRepository, ids.ACTOR, False, "演員"),
    AssetKind.scene: (Scene, SceneRepository, ids.SCENE, True, "場景"),
    AssetKind.prop: (Prop, PropRepository, ids.PROP, True, "道具"),
    AssetKind.costume: (Costume, CostumeRepository, ids.COSTUME, True, "服裝"),
}


class AssetService(Generic[AssetT]):
    """單一資產類型的業務邏輯。"""

    def __init__(self, session: AsyncSession, kind: AssetKind) -> None:
        model, repo_class, prefix, scoped, label = _ASSET_REGISTRY[kind]
        self.session = session
        self.kind = kind
        self.model = model
        self.repo: BaseRepository = repo_class(session)
        self.id_prefix = prefix
        self.project_scoped = scoped
        self.label = label
        self.projects = ProjectRepository(session)
        self.images = AssetImageRepository(session)

    async def _ensure_project(self, project_id: str | None) -> None:
        """確認專案存在（僅對綁定專案的資產）。"""

        if self.project_scoped and project_id is not None:
            ensure_found(await self.projects.get(project_id), resource="專案", entity_id=project_id)

    async def list_assets(
        self,
        *,
        offset: int,
        limit: int,
        project_id: str | None = None,
        search: str | None = None,
        order_by: str | None = None,
    ) -> tuple[list[AssetT], int]:
        """分頁列出資產。"""

        await self._ensure_project(project_id)
        filters: dict[str, Any] = {}
        if self.project_scoped:
            filters["project_id"] = project_id
        return await self.repo.list_page(offset=offset, limit=limit, search=search, order_by=order_by, **filters)

    async def get_asset(self, asset_id: str) -> AssetT:
        """取得資產，不存在時拋出 404。"""

        return ensure_found(await self.repo.get(asset_id), resource=self.label, entity_id=asset_id)

    async def create_asset(self, payload: BaseModel, *, project_id: str | None = None) -> AssetT:
        """建立資產。"""

        await self._ensure_project(project_id)

        if self.project_scoped and not project_id:
            raise ValidationError(f"{self.label}必須指定所屬專案")

        data = payload.model_dump()
        if self.project_scoped:
            data["project_id"] = project_id

        entity = self.model(id=ids.new_id(self.id_prefix), **data)
        try:
            return await self.repo.add(entity)
        except IntegrityError as exc:
            raise translate_integrity_error(exc, resource=self.label) from exc

    async def update_asset(self, asset_id: str, payload: BaseModel) -> AssetT:
        """更新資產；未傳入的欄位保留原值。"""

        entity = await self.get_asset(asset_id)
        apply_updates(entity, payload)
        try:
            await self.session.flush()
        except IntegrityError as exc:
            raise translate_integrity_error(exc, resource=self.label) from exc
        return entity

    async def delete_asset(self, asset_id: str) -> None:
        """刪除資產。"""

        await self.repo.remove(await self.get_asset(asset_id))

    async def name_exists(self, name: str, *, project_id: str | None = None) -> bool:
        """檢查名稱是否已存在。

        供前端在建立前提示「已有同名資產，是否直接沿用」，
        引導使用者重用既有資產而非產生分身 —— 這是一致性的關鍵。
        """

        filters: dict[str, Any] = {"name": name}
        if self.project_scoped:
            filters["project_id"] = project_id
        return bool(await self.repo.count(**filters))

    # ── 資產圖片 ──────────────────────────────────────────────────────────────

    async def list_images(self, asset_id: str) -> list[AssetImage]:
        """列出資產的參考圖。"""

        await self.get_asset(asset_id)
        items, _ = await self.images.list_page(
            offset=0, limit=200, asset_kind=self.kind, asset_id=asset_id
        )
        return items

    async def add_image(self, asset_id: str, payload: AssetImageCreate) -> AssetImage:
        """為資產新增參考圖。

        設為主要參考圖時，會自動取消同資產其他圖片的主要標記 ——
        「主要」在語義上必須唯一，否則生成時無從選擇。
        """

        await self.get_asset(asset_id)

        if payload.is_primary:
            existing = await self.list_images(asset_id)
            for image in existing:
                image.is_primary = 0

        image = AssetImage(
            id=ids.new_id(ids.ASSET_IMAGE),
            asset_kind=self.kind,
            asset_id=asset_id,
            file_id=payload.file_id,
            view_angle=payload.view_angle,
            is_primary=1 if payload.is_primary else 0,
            prompt=payload.prompt,
        )
        try:
            return await self.images.add(image)
        except IntegrityError as exc:
            raise translate_integrity_error(exc, resource=f"{self.label}圖片") from exc

    async def delete_image(self, image_id: str) -> None:
        """刪除資產圖片。"""

        await self.images.remove(
            ensure_found(await self.images.get(image_id), resource=f"{self.label}圖片", entity_id=image_id)
        )
