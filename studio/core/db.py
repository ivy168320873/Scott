"""資料庫連線與 session 管理。

設計取捨：
- 使用 SQLAlchemy 2.0 async API，讓長時間的生成任務不阻塞事件迴圈。
- SQLite 與 PostgreSQL 的連線池行為差異在此集中處理，上層不需感知。
- Studio 的所有資料表都帶 `studio_` 前綴，且使用獨立的連線字串，
  確保完全不會碰到 Scott 既有的 SQLite 資料表。
"""

from __future__ import annotations

import enum as _enum
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any, TypeVar

from sqlalchemy import DateTime, Enum, event, func
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from studio.config import get_settings


class Base(DeclarativeBase):
    """所有 Studio 模型的宣告基底。"""


def _utc_now() -> datetime:
    """目前的 UTC 時間（Python 端）。"""

    return datetime.now(UTC)


class TimestampMixin:
    """`created_at` / `updated_at` 時間戳混入。

    時間值由 **Python 端**產生，資料庫端的 `server_default` 只作為
    繞過 ORM 直接寫入時的保險。

    為什麼不用 SQL 端的 `onupdate=func.now()`：
    那會讓 SQLAlchemy 在 UPDATE 後把該欄位標記為過期，之後任何讀取都需要
    再發一次 SELECT。在 async session 中，這個延遲載入發生在 Pydantic 的
    同步序列化過程裡，會直接拋出 `MissingGreenlet` —— 也就是每一個
    PATCH 端點都會 500。改用 Python 端 callable 後，值在 flush 當下就已知，
    不需要回查資料庫。
    """

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utc_now,
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utc_now,
        onupdate=_utc_now,
        server_default=func.now(),
        nullable=False,
    )


_EnumT = TypeVar("_EnumT", bound=_enum.Enum)


def enum_column(enum_class: type[_EnumT], *, length: int = 32) -> Enum:
    """建立以字串保存、但讀回時還原為列舉成員的欄位型別。

    為什麼不直接用 `String`：
    用 `String` 搭配 `Mapped[SomeEnum]` 註記會產生型別謊言 —— 讀回來的是 `str`，
    於是 `task.status is TaskStatus.running` 永遠為 False，且不會有任何錯誤提示。

    為什麼不用資料庫原生 ENUM：
    `native_enum=False` 讓欄位在所有資料庫都是 VARCHAR + CHECK，SQLite 與
    PostgreSQL 行為一致，新增列舉值也不需要 ALTER TYPE。

    `values_callable` 讓資料庫存的是成員的 **值**（例如 `"pending"`）而非
    成員名稱，資料內容因此可直接閱讀，也與 API 對外的字串一致。

    Args:
        enum_class: 列舉類別。
        length: 欄位長度，需足以容納最長的值。

    Returns:
        可直接傳給 `mapped_column()` 的 SQLAlchemy 型別。
    """

    return Enum(
        enum_class,
        native_enum=False,
        length=length,
        values_callable=lambda members: [member.value for member in members],
        validate_strings=True,
    )


_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def _engine_kwargs() -> dict[str, Any]:
    """依資料庫種類組出 engine 參數。

    SQLite（尤其是 aiosqlite）不支援 `pool_size` / `max_overflow`，
    傳入會直接拋錯，因此需要分開處理。
    """

    settings = get_settings()
    kwargs: dict[str, Any] = {"echo": settings.db_echo, "future": True}
    if settings.is_sqlite:
        # SQLite 使用預設的 pool；額外開啟 autocommit 前的 FK 檢查於 listener 中處理。
        kwargs["connect_args"] = {"check_same_thread": False}
    else:
        kwargs["pool_size"] = settings.db_pool_size
        kwargs["max_overflow"] = settings.db_max_overflow
        kwargs["pool_pre_ping"] = True
    return kwargs


def get_engine() -> AsyncEngine:
    """取得（必要時建立）全域 async engine。"""

    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = create_async_engine(settings.database_url, **_engine_kwargs())

        if settings.is_sqlite:
            # SQLite 預設不強制外鍵；Studio 大量依賴 ON DELETE CASCADE，必須開啟。
            @event.listens_for(_engine.sync_engine, "connect")
            def _enable_sqlite_foreign_keys(dbapi_connection, _record):  # type: ignore[no-untyped-def]
                cursor = dbapi_connection.cursor()
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.close()

    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """取得（必要時建立）session factory。"""

    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            bind=get_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )
    return _session_factory


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """提供一個自動 commit / rollback 的 session context。

    供 worker 與腳本使用；FastAPI route 請改用 `studio.core.deps.get_db`，
    以便由框架管理生命週期。
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


async def dispose_engine() -> None:
    """關閉 engine 並清除快取，供應用關閉與測試 teardown 使用。"""

    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _session_factory = None


async def ping() -> bool:
    """輕量連線檢查，供 health endpoint 使用。"""

    from sqlalchemy import text

    try:
        async with get_engine().connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
