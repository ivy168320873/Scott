"""任務執行期：生命週期寫入、進度回報與取消偵測。

所有狀態變更都直接寫入資料庫（而非只存在記憶體），因此：
- 重新整理頁面或重啟服務後，任務狀態仍然查得到。
- inline 與 celery 兩種執行模式共用同一份生命週期邏輯。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, update

from studio.core.db import session_scope
from studio.core.errors import ProviderError, StudioError, TaskCancelledError
from studio.models.task import GenerationTask
from studio.models.types import TaskStatus

logger = logging.getLogger("studio.tasks")


@dataclass(slots=True)
class TaskContext:
    """交給執行器的任務上下文。

    執行器透過本物件回報進度與檢查取消，不直接碰資料庫 session ——
    每次寫入都用獨立的短交易，避免長時間執行時佔住連線。
    """

    task_id: str
    task_kind: str
    payload: dict[str, Any]
    project_id: str | None = None
    chapter_id: str | None = None
    shot_id: str | None = None
    provider_id: str | None = None
    model_id: str | None = None

    async def progress(self, value: int, message: str = "") -> None:
        """回報進度並順帶檢查取消。

        Raises:
            TaskCancelledError: 已被請求取消時拋出，讓執行器在安全點結束。
        """

        await set_progress(self.task_id, value, message)
        await raise_if_cancelled(self.task_id)

    async def check_cancelled(self) -> None:
        """在安全點檢查是否已被請求取消。"""

        await raise_if_cancelled(self.task_id)


async def load_context(task_id: str) -> TaskContext | None:
    """從資料庫載入任務上下文。"""

    async with session_scope() as session:
        task = await session.get(GenerationTask, task_id)
        if task is None:
            return None
        return TaskContext(
            task_id=task.id,
            task_kind=task.task_kind,
            payload=dict(task.payload or {}),
            project_id=task.project_id,
            chapter_id=task.chapter_id,
            shot_id=task.shot_id,
            provider_id=task.provider_id,
            model_id=task.model_id,
        )


async def mark_running(task_id: str, *, executor_type: str, executor_task_id: str = "") -> None:
    """標記任務開始執行。"""

    async with session_scope() as session:
        await session.execute(
            update(GenerationTask)
            .where(GenerationTask.id == task_id)
            .values(
                status=TaskStatus.running,
                started_at=datetime.now(UTC),
                executor_type=executor_type,
                executor_task_id=executor_task_id,
                error="",
                error_code="",
            )
        )


async def set_progress(task_id: str, value: int, message: str = "") -> None:
    """更新任務進度（0-100）。"""

    async with session_scope() as session:
        await session.execute(
            update(GenerationTask)
            .where(GenerationTask.id == task_id)
            .values(progress=max(0, min(100, int(value))), progress_message=message[:512])
        )


async def is_cancel_requested(task_id: str) -> bool:
    """查詢任務是否已被請求取消。"""

    async with session_scope() as session:
        value = await session.scalar(
            select(GenerationTask.cancel_requested).where(GenerationTask.id == task_id)
        )
    return bool(value)


async def raise_if_cancelled(task_id: str) -> None:
    """已請求取消時拋出 `TaskCancelledError`。"""

    if await is_cancel_requested(task_id):
        raise TaskCancelledError("任務已被請求取消")


async def mark_succeeded(task_id: str, result: dict[str, Any] | None = None) -> None:
    """標記任務成功完成。"""

    async with session_scope() as session:
        await session.execute(
            update(GenerationTask)
            .where(GenerationTask.id == task_id)
            .values(
                status=TaskStatus.succeeded,
                progress=100,
                progress_message="完成",
                result=result or {},
                finished_at=datetime.now(UTC),
                error="",
                error_code="",
            )
        )


async def mark_failed(task_id: str, error: Exception) -> None:
    """標記任務失敗。

    錯誤碼取自 `StudioError.code`，讓前端能分辨「供應商限流」與「設定錯誤」
    等不同情境，而不必解析訊息字串。
    """

    code = getattr(error, "code", "internal_error")
    message = str(error)

    async with session_scope() as session:
        await session.execute(
            update(GenerationTask)
            .where(GenerationTask.id == task_id)
            .values(
                status=TaskStatus.failed,
                error=message[:4000],
                error_code=str(code)[:64],
                finished_at=datetime.now(UTC),
            )
        )


async def mark_cancelled(task_id: str) -> None:
    """標記任務已完成取消。

    這是兩段式取消的第二段：第一段由 API 設定 `cancel_requested`，
    這裡才寫入實際的終態，確保產物不會停在寫到一半的狀態。
    """

    async with session_scope() as session:
        await session.execute(
            update(GenerationTask)
            .where(GenerationTask.id == task_id)
            .values(
                status=TaskStatus.cancelled,
                cancelled_at=datetime.now(UTC),
                finished_at=datetime.now(UTC),
                progress_message="已取消",
            )
        )


async def run_task(task_id: str, *, executor_type: str, executor_task_id: str = "") -> str:
    """執行一個任務的完整生命週期。

    這是 inline 與 celery 兩種模式的共同進入點，確保狀態流轉只有一份實作。

    Returns:
        任務的最終狀態值（`succeeded` / `failed` / `cancelled` / `skipped`）。
    """

    from studio.tasks.registry import resolve_executor

    context = await load_context(task_id)
    if context is None:
        logger.warning("task %s not found, skipping", task_id)
        return "skipped"

    # 執行前先看一次：使用者可能在排程與實際執行之間就按了取消。
    if await is_cancel_requested(task_id):
        await mark_cancelled(task_id)
        return "cancelled"

    await mark_running(task_id, executor_type=executor_type, executor_task_id=executor_task_id)

    try:
        executor = resolve_executor(context.task_kind)
        result = await executor(context)
        await mark_succeeded(task_id, result)
        return "succeeded"
    except TaskCancelledError:
        await mark_cancelled(task_id)
        return "cancelled"
    except (ProviderError, StudioError) as exc:
        logger.warning("task %s failed: %s", task_id, exc)
        await mark_failed(task_id, exc)
        return "failed"
    except Exception as exc:  # noqa: BLE001 - 任何未預期錯誤都要落地成失敗狀態
        logger.exception("task %s crashed", task_id)
        await mark_failed(task_id, exc)
        return "failed"
