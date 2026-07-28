"""媒體檔案 Service。"""

from __future__ import annotations

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from studio.core import ids
from studio.core.storage import ObjectStorage, get_storage
from studio.models.file import FileItem, FileUsage
from studio.models.types import FileType
from studio.repositories import FileRepository, FileUsageRepository, ProjectRepository
from studio.schemas.asset import MediaCreate, MediaRead, MediaUpdate, MediaUsageCreate
from studio.services.base import apply_updates, ensure_found, translate_integrity_error


class MediaService:
    """媒體檔案業務邏輯。"""

    def __init__(self, session: AsyncSession, storage: ObjectStorage | None = None) -> None:
        self.session = session
        self.files = FileRepository(session)
        self.usages = FileUsageRepository(session)
        self.projects = ProjectRepository(session)
        self._storage = storage

    @property
    def storage(self) -> ObjectStorage:
        """物件儲存（延遲取得，讓測試可注入替身）。"""

        if self._storage is None:
            self._storage = get_storage()
        return self._storage

    def to_read(self, item: FileItem) -> MediaRead:
        """轉為對外表示。

        `url` 於執行期由儲存後端組出而非存入資料庫 ——
        這樣更換 CDN 或儲存後端時，既有紀錄不需要遷移。
        """

        return MediaRead(
            id=item.id,
            file_type=item.file_type,
            filename=item.filename,
            content_type=item.content_type,
            size_bytes=item.size_bytes,
            width=item.width,
            height=item.height,
            duration_seconds=item.duration_seconds,
            project_id=item.project_id,
            source_task_id=item.source_task_id,
            url=self.storage.public_url(item.storage_key),
            file_metadata=item.file_metadata,
            created_at=item.created_at,
            updated_at=item.updated_at,
        )

    async def list_media(
        self,
        *,
        offset: int,
        limit: int,
        search: str | None = None,
        project_id: str | None = None,
        file_type: FileType | None = None,
    ) -> tuple[list[FileItem], int]:
        """分頁列出媒體檔案。"""

        return await self.files.list_page(
            offset=offset, limit=limit, search=search, project_id=project_id, file_type=file_type
        )

    async def get_media(self, file_id: str) -> FileItem:
        """取得媒體檔案，不存在時拋出 404。"""

        return ensure_found(await self.files.get(file_id), resource="媒體檔案", entity_id=file_id)

    async def create_media(self, payload: MediaCreate) -> FileItem:
        """登記一筆媒體檔案中繼資料。"""

        if payload.project_id:
            ensure_found(
                await self.projects.get(payload.project_id), resource="專案", entity_id=payload.project_id
            )

        item = FileItem(id=ids.new_id(ids.FILE), **payload.model_dump())
        try:
            return await self.files.add(item)
        except IntegrityError as exc:
            raise translate_integrity_error(exc, resource="媒體檔案") from exc

    async def update_media(self, file_id: str, payload: MediaUpdate) -> FileItem:
        """更新媒體中繼資料；未傳入的欄位保留原值。"""

        item = await self.get_media(file_id)
        apply_updates(item, payload)
        await self.session.flush()
        return item

    async def delete_media(self, file_id: str, *, purge_object: bool = False) -> None:
        """刪除媒體紀錄。

        Args:
            purge_object: 是否一併刪除物件儲存中的實體檔案。
                預設為 False —— 刪紀錄可回復，刪實體檔案不可回復，
                因此需要呼叫端明確要求。
        """

        item = await self.get_media(file_id)
        storage_key = item.storage_key

        await self.files.remove(item)

        if purge_object:
            self.storage.delete(storage_key)

    async def list_usages(self, file_id: str) -> list[FileUsage]:
        """列出某檔案的所有用途。

        用於在刪除前提示「這張圖仍被哪些鏡頭引用」。
        """

        await self.get_media(file_id)
        items, _ = await self.usages.list_page(offset=0, limit=500, file_id=file_id)
        return items

    async def add_usage(self, file_id: str, payload: MediaUsageCreate) -> FileUsage:
        """登記檔案用途。"""

        await self.get_media(file_id)
        usage = FileUsage(file_id=file_id, **payload.model_dump())
        try:
            return await self.usages.add(usage)
        except IntegrityError as exc:
            raise translate_integrity_error(exc, resource="檔案用途") from exc
