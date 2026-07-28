"""Studio 測試共用 fixture。

資料庫測試對每個 test 使用獨立的 SQLite 檔案，確保測試彼此隔離，
且外鍵約束真的被強制執行（否則級聯刪除的測試會失去意義）。
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from studio.core.db import Base


@pytest_asyncio.fixture(name="session")
async def _session(tmp_path) -> AsyncIterator[AsyncSession]:
    """提供已建好 schema 的資料庫 session。

    直接由 `Base.metadata.create_all` 建表而非跑 Alembic：
    測試關心的是模型定義本身，migration 的正確性另由 drift 檢查涵蓋。
    """

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path/'test.db'}")

    # SQLite 預設不強制外鍵，必須明確開啟，級聯刪除才會真的發生。
    @event.listens_for(engine.sync_engine, "connect")
    def _fk_on(dbapi_connection, _record):  # type: ignore[no-untyped-def]
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session

    await engine.dispose()


@pytest.fixture(name="anyio_backend")
def _anyio_backend() -> str:
    """限定 async 測試只跑 asyncio backend。"""

    return "asyncio"
