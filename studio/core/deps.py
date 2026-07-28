"""FastAPI 相依注入。

集中管理 route 需要的資源（DB session、儲存、設定），
讓 route 函式簽章保持乾淨，測試時也能以 `dependency_overrides` 替換。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from studio.config import Settings, get_settings
from studio.core.db import get_session_factory
from studio.core.storage import ObjectStorage, get_storage


async def get_db() -> AsyncIterator[AsyncSession]:
    """提供 request 範圍的資料庫 session。

    request 正常結束時 commit，發生例外時 rollback，
    確保 route 不需要自行處理交易邊界。
    """

    factory = get_session_factory()
    session = factory()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()


def get_settings_dep() -> Settings:
    """以相依注入方式取得設定，方便測試覆寫。"""

    return get_settings()


def get_storage_dep() -> ObjectStorage:
    """以相依注入方式取得物件儲存。"""

    return get_storage()


class PageParams:
    """共用分頁查詢參數。

    以 class 形式宣告，讓 OpenAPI 產生的前端型別能重複使用同一組參數。
    """

    def __init__(
        self,
        page: Annotated[int, Query(ge=1, description="頁碼，從 1 開始")] = 1,
        page_size: Annotated[int, Query(ge=1, le=200, description="每頁筆數")] = 20,
    ) -> None:
        self.page = page
        self.page_size = page_size

    @property
    def offset(self) -> int:
        """轉為 SQL OFFSET。"""

        return (self.page - 1) * self.page_size

    @property
    def limit(self) -> int:
        """轉為 SQL LIMIT。"""

        return self.page_size


DbSession = Annotated[AsyncSession, Depends(get_db)]
SettingsDep = Annotated[Settings, Depends(get_settings_dep)]
StorageDep = Annotated[ObjectStorage, Depends(get_storage_dep)]
Paging = Annotated[PageParams, Depends(PageParams)]
