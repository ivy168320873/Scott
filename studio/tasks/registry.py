"""任務執行器註冊表。

以 `task_kind` 解析到具體執行器，讓任務編排層與業務實作解耦：
新增任務類型只需要註冊，不必修改分派邏輯。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from threading import RLock
from typing import Any

from studio.tasks.runtime import TaskContext

# 執行器簽章：接收上下文，回傳要寫入 `GenerationTask.result` 的字典。
TaskExecutor = Callable[[TaskContext], Awaitable[dict[str, Any]]]

_EXECUTORS: dict[str, TaskExecutor] = {}
_LOCK = RLock()


def register_executor(task_kind: str, executor: TaskExecutor) -> None:
    """註冊任務執行器。

    重複註冊同一個函式視為冪等（模組可能被多次匯入）；
    註冊不同函式到同一個 `task_kind` 則是設定錯誤，直接拋錯。
    """

    key = (task_kind or "").strip().lower()
    if not key:
        raise ValueError("task_kind must not be empty")

    with _LOCK:
        existing = _EXECUTORS.get(key)
        if existing is not None and existing is not executor:
            raise ValueError(f"task executor conflict for task_kind={key!r}")
        _EXECUTORS[key] = executor


def resolve_executor(task_kind: str) -> TaskExecutor:
    """解析任務執行器。

    Raises:
        RuntimeError: 未註冊時拋出，訊息包含目前已註冊的類型以利診斷。
    """

    key = (task_kind or "").strip().lower()
    with _LOCK:
        executor = _EXECUTORS.get(key)
        known = sorted(_EXECUTORS)

    if executor is None:
        raise RuntimeError(f"Unsupported task_kind: {task_kind!r}; registered: {known}")
    return executor


def list_executors() -> list[str]:
    """列出已註冊的任務類型（供診斷與測試使用）。"""

    with _LOCK:
        return sorted(_EXECUTORS)


def load_builtin_executors() -> None:
    """載入內建執行器。

    以函式而非模組層級 import 觸發註冊，避免匯入順序造成的循環相依。
    """

    from studio.tasks import (
        media_tasks,  # noqa: F401
        script_tasks,  # noqa: F401
    )
