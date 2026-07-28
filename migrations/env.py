"""Alembic 執行環境。

要點：
- 連線字串一律從 `studio.config` 取得（來源為 `STUDIO_DATABASE_URL` 環境變數），
  不從 alembic.ini 讀取，避免帳密進版控。
- 支援 async driver（aiosqlite / asyncpg）。
- `target_metadata` 指向 Studio 的 `Base.metadata`；只涵蓋 `studio_*` 資料表，
  不會偵測到也不會改動 Scott 既有的 SQLite 資料表。
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# 匯入所有模型模組，確保 Base.metadata 完整（autogenerate 需要）。
import studio.models  # noqa: F401  pylint: disable=unused-import
from studio.config import get_settings
from studio.core.db import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _database_url() -> str:
    """取得 migration 用的連線字串。"""

    return get_settings().database_url


def run_migrations_offline() -> None:
    """離線模式：只產生 SQL，不建立連線。"""

    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection: Connection) -> None:
    """在既有連線上執行 migration。

    `render_as_batch=True` 讓 SQLite 也能執行 ALTER TABLE 類變更
    （SQLite 原生不支援大部分 ALTER，Alembic 以重建表的方式模擬）。
    """

    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """線上模式：建立 async engine 並執行 migration。"""

    config.set_main_option("sqlalchemy.url", _database_url())
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(_do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
