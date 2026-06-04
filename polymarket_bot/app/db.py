"""Async PostgreSQL connection pool and schema bootstrap."""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

import asyncpg

from .config import get_settings

log = logging.getLogger(__name__)

_pool: Optional[asyncpg.Pool] = None

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schema.sql"


async def init_pool() -> asyncpg.Pool:
    """Create the global connection pool and ensure the schema exists."""
    global _pool
    if _pool is not None:
        return _pool

    settings = get_settings()
    _pool = await asyncpg.create_pool(dsn=settings.database_url, min_size=1, max_size=10)
    log.info("PostgreSQL pool created")
    await _apply_schema(_pool)
    return _pool


async def _apply_schema(pool: asyncpg.Pool) -> None:
    if not SCHEMA_PATH.exists():
        log.warning("schema.sql not found at %s; skipping auto-migration", SCHEMA_PATH)
        return
    sql = SCHEMA_PATH.read_text()
    async with pool.acquire() as conn:
        await conn.execute(sql)
    log.info("Schema applied from %s", SCHEMA_PATH)


def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("DB pool not initialised; call init_pool() first")
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
        log.info("PostgreSQL pool closed")
