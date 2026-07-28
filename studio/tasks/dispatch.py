"""任務分派：把任務送去執行。

兩種模式：
- `celery`：送往 worker，Web 程序立即回應。
- `inline`：在背景執行緒跑，供單機與測試環境使用。

無論哪種模式，**Web request 都不會等待任務完成** ——
長時間生成不可綁在 HTTP 請求生命週期內。
"""

from __future__ import annotations

import asyncio
import logging
import threading

from studio.config import TaskExecutionMode, get_settings

logger = logging.getLogger("studio.tasks.dispatch")

# 追蹤 inline 執行緒，讓測試能等待其完成。
_inline_threads: list[threading.Thread] = []


def _run_inline(task_id: str) -> None:
    """在獨立執行緒中以新的事件迴圈執行任務。

    必須另開事件迴圈：呼叫端（FastAPI）已經有一個正在跑的迴圈，
    不能在其上同步等待。
    """

    from studio.tasks.registry import load_builtin_executors
    from studio.tasks.runtime import run_task

    load_builtin_executors()

    loop = asyncio.new_event_loop()
    try:
        asyncio.set_event_loop(loop)
        loop.run_until_complete(run_task(task_id, executor_type="inline"))
    except Exception:  # noqa: BLE001 - 執行緒內未處理例外不得靜默消失
        logger.exception("inline task %s crashed", task_id)
    finally:
        # 關閉前先釋放 engine，避免每個任務都留下懸掛連線。
        try:
            from studio.core.db import dispose_engine

            loop.run_until_complete(dispose_engine())
        except Exception:  # noqa: BLE001
            pass
        loop.close()


def dispatch_task(task_id: str) -> str:
    """把任務送去執行。

    Returns:
        實際使用的執行器類型（`celery` 或 `inline`）。
    """

    settings = get_settings()

    if settings.task_execution_mode is TaskExecutionMode.celery:
        try:
            from studio.tasks.execute import execute_generation_task

            async_result = execute_generation_task.delay(task_id)
            logger.info("task %s dispatched to celery (%s)", task_id, async_result.id)
            return "celery"
        except Exception:  # noqa: BLE001 - broker 不可用時降級而非讓請求失敗
            logger.exception("celery dispatch failed for %s, falling back to inline", task_id)

    thread = threading.Thread(target=_run_inline, args=(task_id,), daemon=True, name=f"studio-task-{task_id}")
    thread.start()
    _inline_threads.append(thread)
    return "inline"


def wait_for_inline_tasks(timeout: float = 60.0) -> None:
    """等待所有 inline 任務結束。

    僅供測試使用；production 不應等待背景任務。
    """

    deadline = timeout
    for thread in list(_inline_threads):
        thread.join(timeout=deadline)
    _inline_threads[:] = [t for t in _inline_threads if t.is_alive()]
