"""泛型 Repository。

職責邊界：
- Repository **只負責資料存取**：查詢組裝、分頁、排序、存在性檢查。
- 業務規則（狀態流轉、跨資源驗證、統計更新）一律屬於 Service 層。

以泛型基底而非為 13 個資源各寫一份 CRUD：這些資源的存取模式完全相同，
重複實作只會產生 13 份需要同步維護的相同程式碼。
"""

from __future__ import annotations

from typing import Any, Generic, TypeVar

from sqlalchemy import Select, delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from studio.core.db import Base

ModelT = TypeVar("ModelT", bound=Base)


class BaseRepository(Generic[ModelT]):
    """提供共用 CRUD 與分頁的 Repository 基底。

    Attributes:
        model: 對應的 SQLAlchemy 模型類別。
        searchable_fields: `search` 參數會比對的欄位名稱。
    """

    model: type[ModelT]
    searchable_fields: tuple[str, ...] = ()

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ── 查詢組裝 ──────────────────────────────────────────────────────────────

    def _base_select(self) -> Select[tuple[ModelT]]:
        """建立基礎查詢，供子類覆寫以加入預設 join 或過濾。"""

        return select(self.model)

    def _apply_filters(self, stmt: Select[Any], filters: dict[str, Any]) -> Select[Any]:
        """套用等值過濾。

        `None` 值代表「不過濾」，因此呼叫端可直接傳入可選查詢參數，
        不必自行剔除未提供的欄位。
        """

        for field, value in filters.items():
            if value is None:
                continue
            column = getattr(self.model, field, None)
            if column is not None:
                stmt = stmt.where(column == value)
        return stmt

    def _apply_search(self, stmt: Select[Any], search: str | None) -> Select[Any]:
        """套用關鍵字模糊搜尋（跨 `searchable_fields` 做 OR）。"""

        if not search or not self.searchable_fields:
            return stmt

        pattern = f"%{search.strip()}%"
        clauses = [
            getattr(self.model, field).ilike(pattern)
            for field in self.searchable_fields
            if getattr(self.model, field, None) is not None
        ]
        return stmt.where(or_(*clauses)) if clauses else stmt

    def _apply_ordering(self, stmt: Select[Any], order_by: str | None) -> Select[Any]:
        """套用排序。

        接受 `field` 或 `-field`（前綴減號代表遞減）。
        未指定或欄位不存在時，退回以 `updated_at` 遞減排序，
        確保分頁結果穩定。
        """

        default = getattr(self.model, "updated_at", None) or self.model.id

        if not order_by:
            return stmt.order_by(default.desc())

        descending = order_by.startswith("-")
        column = getattr(self.model, order_by.lstrip("-"), None)
        if column is None:
            return stmt.order_by(default.desc())
        return stmt.order_by(column.desc() if descending else column.asc())

    # ── 讀取 ──────────────────────────────────────────────────────────────────

    async def get(self, entity_id: Any) -> ModelT | None:
        """依主鍵取得單筆，不存在時回傳 None。"""

        stmt = self._base_select().where(self.model.id == entity_id)  # type: ignore[attr-defined]
        return (await self.session.execute(stmt)).scalars().first()

    async def exists(self, entity_id: Any) -> bool:
        """判斷主鍵是否存在（不載入整筆資料）。"""

        stmt = select(func.count()).select_from(self.model).where(self.model.id == entity_id)  # type: ignore[attr-defined]
        return bool(await self.session.scalar(stmt))

    async def list_page(
        self,
        *,
        offset: int = 0,
        limit: int = 20,
        search: str | None = None,
        order_by: str | None = None,
        **filters: Any,
    ) -> tuple[list[ModelT], int]:
        """取得一頁資料與符合條件的總筆數。

        Returns:
            `(items, total)`。`total` 為套用過濾與搜尋後、分頁前的總數，
            讓前端能正確計算頁數。
        """

        stmt = self._apply_search(self._apply_filters(self._base_select(), filters), search)

        count_stmt = select(func.count()).select_from(stmt.order_by(None).subquery())
        total = int(await self.session.scalar(count_stmt) or 0)

        stmt = self._apply_ordering(stmt, order_by).offset(offset).limit(limit)
        items = list((await self.session.execute(stmt)).scalars().unique().all())
        return items, total

    async def count(self, **filters: Any) -> int:
        """計算符合條件的筆數。"""

        stmt = self._apply_filters(select(func.count()).select_from(self.model), filters)
        return int(await self.session.scalar(stmt) or 0)

    # ── 寫入 ──────────────────────────────────────────────────────────────────

    async def add(self, entity: ModelT) -> ModelT:
        """新增一筆並 flush，使資料庫層級的約束立即生效。"""

        self.session.add(entity)
        await self.session.flush()
        return entity

    async def remove(self, entity: ModelT) -> None:
        """刪除一筆。"""

        await self.session.delete(entity)
        await self.session.flush()

    async def remove_where(self, **filters: Any) -> int:
        """依條件批次刪除，回傳刪除筆數。"""

        stmt = delete(self.model)
        for field, value in filters.items():
            column = getattr(self.model, field, None)
            if column is not None:
                stmt = stmt.where(column == value)
        result = await self.session.execute(stmt)
        await self.session.flush()
        return int(result.rowcount or 0)

    async def max_index(self, **filters: Any) -> int:
        """取得目前最大的 `index` 值（無資料時回傳 0）。

        供 Service 層自動指派下一個序號使用。
        """

        column = getattr(self.model, "index", None)
        if column is None:
            return 0
        stmt = self._apply_filters(select(func.max(column)), filters)
        return int(await self.session.scalar(stmt) or 0)
