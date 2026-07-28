"""健康檢查端點。

回報各相依元件（資料庫、Redis、物件儲存）的實際狀態，而非只回 200，
讓 Docker Compose 的 healthcheck 與部署監控能區分「服務活著」與「服務可用」。
"""

from __future__ import annotations

from fastapi import APIRouter

from studio.config import TaskExecutionMode, get_settings
from studio.core import db as db_core
from studio.core import redis_client
from studio.core.storage import get_storage
from studio.schemas.common import ApiResponse, HealthComponent, HealthData

router = APIRouter(tags=["health"])


@router.get(
    "/health",
    response_model=ApiResponse[HealthData],
    summary="健康檢查",
)
async def health() -> ApiResponse[HealthData]:
    """回報 Studio 與其相依元件的健康狀態。

    整體狀態判定：
    - `ok`：資料庫可連線，且所有「必要」元件正常。
    - `degraded`：資料庫正常但有非必要元件異常（例如 inline 模式下 Redis 不可用）。

    Redis 在 `inline` 任務模式下屬非必要元件，不會使整體狀態變成失敗。
    """

    settings = get_settings()
    components: list[HealthComponent] = []

    db_ok = await db_core.ping()
    components.append(
        HealthComponent(
            name="database",
            healthy=db_ok,
            detail="" if db_ok else "無法連線至資料庫",
        )
    )

    redis_required = settings.task_execution_mode is TaskExecutionMode.celery
    redis_ok = await redis_client.ping()
    components.append(
        HealthComponent(
            name="redis",
            healthy=redis_ok,
            detail=(
                ""
                if redis_ok
                else ("Celery 模式下必須可用" if redis_required else "inline 模式下非必要")
            ),
        )
    )

    try:
        storage_ok = get_storage().healthy()
        storage_detail = "" if storage_ok else "儲存後端不可用"
    except Exception as exc:  # 設定錯誤（例如缺少 S3 金鑰）不應讓端點崩潰
        storage_ok = False
        storage_detail = str(exc)
    components.append(HealthComponent(name="storage", healthy=storage_ok, detail=storage_detail))

    critical_ok = db_ok and storage_ok and (redis_ok or not redis_required)
    status = "ok" if critical_ok else "degraded"

    return ApiResponse.ok(
        HealthData(
            status=status,
            app=settings.app_name,
            task_mode=settings.task_execution_mode.value,
            storage_backend=settings.storage_backend.value,
            components=components,
        )
    )


@router.get(
    "/health/live",
    response_model=ApiResponse[HealthData],
    summary="存活探針",
)
async def liveness() -> ApiResponse[HealthData]:
    """僅確認 process 存活，不檢查任何外部相依。

    供容器 liveness probe 使用：外部服務短暫異常不應導致容器被重啟。
    """

    settings = get_settings()
    return ApiResponse.ok(
        HealthData(
            status="ok",
            app=settings.app_name,
            task_mode=settings.task_execution_mode.value,
            storage_backend=settings.storage_backend.value,
            components=[],
        )
    )
