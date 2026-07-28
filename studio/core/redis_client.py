"""Redis 連線管理。

Redis 在 Studio 中的用途：
- Celery broker / result backend
- 任務進度的即時發布（前端輪詢仍以資料庫為準，Redis 只做加速）

Redis 不可用時不應讓整個應用無法啟動：`inline` 任務模式完全不需要 Redis，
因此連線採延遲建立，並由 health endpoint 回報狀態。
"""

from __future__ import annotations

from redis.asyncio import Redis

from studio.config import get_settings

_client: Redis | None = None


def get_redis() -> Redis:
    """取得（必要時建立）async Redis client。"""

    global _client
    if _client is None:
        settings = get_settings()
        _client = Redis.from_url(settings.redis_url, decode_responses=True)
    return _client


async def ping() -> bool:
    """輕量連線檢查，供 health endpoint 使用。"""

    try:
        return bool(await get_redis().ping())
    except Exception:
        return False


async def close_redis() -> None:
    """關閉連線，供應用關閉與測試 teardown 使用。"""

    global _client
    if _client is not None:
        await _client.aclose()
    _client = None
