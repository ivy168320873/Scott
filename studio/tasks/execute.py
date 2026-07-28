"""Celery worker 的任務進入點。

Worker 只做一件事：呼叫共用的 `run_task()`。狀態流轉、進度、取消與錯誤
處理全部在 `studio.tasks.runtime`，因此 inline 與 celery 行為完全一致。
"""

from __future__ import annotations

import asyncio
import logging

from studio.tasks.celery_app import celery_app

logger = logging.getLogger("studio.tasks.execute")


@celery_app.task(name="studio.execute_generation_task", bind=True)
def execute_generation_task(self, task_id: str) -> str:  # type: ignore[no-untyped-def]
    """在 worker 中執行一個生成任務。

    Args:
        task_id: `studio_generation_tasks.id`。

    Returns:
        任務最終狀態值。
    """

    from studio.core.db import dispose_engine
    from studio.tasks.registry import load_builtin_executors
    from studio.tasks.runtime import run_task

    load_builtin_executors()

    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        outcome = loop.run_until_complete(
            run_task(task_id, executor_type="celery", executor_task_id=self.request.id or "")
        )
        logger.info("task %s finished with %s", task_id, outcome)
        return outcome
    finally:
        try:
            loop.run_until_complete(dispose_engine())
        except Exception:  # noqa: BLE001
            pass
        loop.close()
