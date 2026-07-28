"""Celery 應用設定。

Studio 的長時間任務（腳本分析、圖片生成、影片生成）一律不在 web request
中執行。預設走 Celery worker；在單機或測試環境可用 `STUDIO_TASK_EXECUTION_MODE=inline`
降級為背景執行緒（任務狀態仍寫入資料庫，因此仍可查詢、取消與恢復）。
"""

from __future__ import annotations

from celery import Celery

from studio.config import get_settings


def create_celery() -> Celery:
    """建立並設定 Celery 應用。

    `task_acks_late` + `task_reject_on_worker_lost` 讓 worker 意外中止時
    任務會重新入列，而不是靜默消失。
    """

    settings = get_settings()
    app = Celery(
        "scott_studio",
        broker=settings.effective_celery_broker,
        backend=settings.effective_celery_backend,
        include=["studio.tasks.execute"],
    )
    app.conf.update(
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
        timezone="UTC",
        enable_utc=True,
        task_track_started=True,
        task_acks_late=True,
        task_reject_on_worker_lost=True,
        task_time_limit=settings.task_default_timeout + 60,
        task_soft_time_limit=settings.task_default_timeout,
        worker_prefetch_multiplier=1,
        broker_connection_retry_on_startup=True,
    )
    return app


celery_app = create_celery()
