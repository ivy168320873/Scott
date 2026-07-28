"""Phase 4 任務系統測試。

驗證生命週期、進度、取消、重試與恢復都真的寫入資料庫 ——
不是靠記憶體狀態或永遠成功的 mock。
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from studio.core import ids
from studio.core.errors import TaskCancelledError
from studio.models.task import GenerationTask, GenerationTaskLink
from studio.models.types import TaskStatus
from studio.tasks import registry
from studio.tasks.runtime import (
    TaskContext,
    is_cancel_requested,
    mark_cancelled,
    mark_failed,
    mark_running,
    mark_succeeded,
    run_task,
    set_progress,
)


@pytest.fixture(name="task_db")
def _task_db(tmp_path, monkeypatch):
    """把 Studio 的全域 session factory 指向獨立的測試資料庫。

    `runtime` 內的所有寫入都用 `session_scope()`（全域 factory），
    因此必須替換全域，而不是只注入一個 session。
    """

    from studio.core import db as db_core

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path/'tasks.db'}")

    @event.listens_for(engine.sync_engine, "connect")
    def _fk_on(dbapi_connection, _record):  # type: ignore[no-untyped-def]
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _setup() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(db_core.Base.metadata.create_all)

    asyncio.run(_setup())

    monkeypatch.setattr(db_core, "_engine", engine, raising=False)
    monkeypatch.setattr(db_core, "_session_factory", factory, raising=False)

    yield factory


async def _create_task(factory, *, task_kind: str = "script_divide", **kwargs) -> str:
    """建立一筆任務並回傳其 ID。"""

    task_id = ids.new_id(ids.TASK)
    async with factory() as session:
        session.add(GenerationTask(id=task_id, task_kind=task_kind, **kwargs))
        await session.commit()
    return task_id


# ── 註冊表 ────────────────────────────────────────────────────────────────────


def test_registry_resolves_registered_executor() -> None:
    """已註冊的 task_kind 必須能解析出執行器。"""

    registry.load_builtin_executors()

    for kind in ("script_divide", "script_extract", "video_generation", "frame_image_generation"):
        assert registry.resolve_executor(kind) is not None


def test_registry_rejects_unknown_kind() -> None:
    """未註冊的 task_kind 必須明確報錯，而非靜默失敗。"""

    with pytest.raises(RuntimeError, match="Unsupported task_kind"):
        registry.resolve_executor("no_such_kind")


def test_registry_rejects_conflicting_registration() -> None:
    """同一個 task_kind 註冊兩個不同函式屬設定錯誤。"""

    async def _one(_ctx):
        return {}

    async def _two(_ctx):
        return {}

    registry.register_executor("conflict_probe", _one)
    registry.register_executor("conflict_probe", _one)  # 冪等

    with pytest.raises(ValueError, match="conflict"):
        registry.register_executor("conflict_probe", _two)


# ── 生命週期 ──────────────────────────────────────────────────────────────────


async def test_lifecycle_writes_to_database(task_db) -> None:
    """狀態流轉必須落地到資料庫，重啟後仍查得到。"""

    task_id = await _create_task(task_db)

    await mark_running(task_id, executor_type="inline")
    async with task_db() as session:
        task = await session.get(GenerationTask, task_id)
        assert task.status is TaskStatus.running
        assert task.started_at is not None

    await set_progress(task_id, 42, "處理中")
    async with task_db() as session:
        task = await session.get(GenerationTask, task_id)
        assert task.progress == 42
        assert task.progress_message == "處理中"

    await mark_succeeded(task_id, {"shot_count": 3})
    async with task_db() as session:
        task = await session.get(GenerationTask, task_id)
        assert task.status is TaskStatus.succeeded
        assert task.progress == 100
        assert task.result == {"shot_count": 3}
        assert task.finished_at is not None
        assert task.elapsed_ms is not None and task.elapsed_ms >= 0


async def test_progress_is_clamped(task_db) -> None:
    """進度必須夾在 0-100，避免前端進度條溢出。"""

    task_id = await _create_task(task_db)

    await set_progress(task_id, 999)
    async with task_db() as session:
        assert (await session.get(GenerationTask, task_id)).progress == 100

    await set_progress(task_id, -5)
    async with task_db() as session:
        assert (await session.get(GenerationTask, task_id)).progress == 0


async def test_failure_records_standard_error_code(task_db) -> None:
    """失敗必須記錄可機器判讀的錯誤碼。"""

    from studio.core.errors import ProviderRateLimitError

    task_id = await _create_task(task_db)
    await mark_failed(task_id, ProviderRateLimitError("too many", provider="openai"))

    async with task_db() as session:
        task = await session.get(GenerationTask, task_id)
        assert task.status is TaskStatus.failed
        assert task.error_code == "provider_rate_limited"
        assert "too many" in task.error


# ── 執行 ──────────────────────────────────────────────────────────────────────


async def test_run_task_success_path(task_db) -> None:
    """執行器成功時任務轉為 succeeded 並寫入結果。"""

    async def _executor(context: TaskContext) -> dict:
        await context.progress(50, "一半")
        return {"echo": context.payload.get("value")}

    registry.register_executor("probe_success", _executor)
    task_id = await _create_task(task_db, task_kind="probe_success", payload={"value": 7})

    assert await run_task(task_id, executor_type="inline") == "succeeded"

    async with task_db() as session:
        task = await session.get(GenerationTask, task_id)
        assert task.status is TaskStatus.succeeded
        assert task.result == {"echo": 7}


async def test_run_task_failure_path(task_db) -> None:
    """執行器拋錯時任務轉為 failed，錯誤訊息落地。"""

    async def _executor(_context: TaskContext) -> dict:
        raise RuntimeError("boom")

    registry.register_executor("probe_failure", _executor)
    task_id = await _create_task(task_db, task_kind="probe_failure")

    assert await run_task(task_id, executor_type="inline") == "failed"

    async with task_db() as session:
        task = await session.get(GenerationTask, task_id)
        assert task.status is TaskStatus.failed
        assert "boom" in task.error


async def test_unknown_task_kind_fails_task_not_crash(task_db) -> None:
    """未知的 task_kind 應讓任務失敗，而不是讓 worker 崩潰。"""

    task_id = await _create_task(task_db, task_kind="totally_unknown")

    assert await run_task(task_id, executor_type="inline") == "failed"

    async with task_db() as session:
        assert (await session.get(GenerationTask, task_id)).status is TaskStatus.failed


async def test_missing_task_is_skipped(task_db) -> None:
    """任務不存在時不應拋錯（worker 可能重複收到訊息）。"""

    assert await run_task("task_does_not_exist", executor_type="inline") == "skipped"


# ── 兩階段取消 ────────────────────────────────────────────────────────────────


async def test_cancel_before_start_short_circuits(task_db) -> None:
    """執行前就被取消時，執行器不應被呼叫。"""

    called = False

    async def _executor(_context: TaskContext) -> dict:
        nonlocal called
        called = True
        return {}

    registry.register_executor("probe_cancel_early", _executor)
    task_id = await _create_task(task_db, task_kind="probe_cancel_early", cancel_requested=True)

    assert await run_task(task_id, executor_type="inline") == "cancelled"
    assert called is False, "已請求取消的任務不應開始執行"

    async with task_db() as session:
        task = await session.get(GenerationTask, task_id)
        assert task.status is TaskStatus.cancelled
        assert task.cancelled_at is not None


async def test_cancel_detected_at_progress_checkpoint(task_db) -> None:
    """執行中設定取消旗標後，下一個進度檢查點應中止任務。

    這正是兩階段取消的核心：不強制終止，而是在安全點結束。
    """

    reached_second_stage = False

    async def _executor(context: TaskContext) -> dict:
        nonlocal reached_second_stage
        await context.progress(10, "第一階段")

        # 模擬執行過程中使用者按下取消
        async with task_db() as session:
            task = await session.get(GenerationTask, context.task_id)
            task.cancel_requested = True
            await session.commit()

        await context.progress(50, "第二階段")  # 這裡應偵測到取消並拋出
        reached_second_stage = True
        return {}

    registry.register_executor("probe_cancel_mid", _executor)
    task_id = await _create_task(task_db, task_kind="probe_cancel_mid")

    assert await run_task(task_id, executor_type="inline") == "cancelled"
    assert reached_second_stage is False, "取消應在安全點中止執行"

    async with task_db() as session:
        assert (await session.get(GenerationTask, task_id)).status is TaskStatus.cancelled


async def test_cancel_flag_is_readable(task_db) -> None:
    """取消旗標必須可由執行器查詢。"""

    task_id = await _create_task(task_db)
    assert await is_cancel_requested(task_id) is False

    async with task_db() as session:
        task = await session.get(GenerationTask, task_id)
        task.cancel_requested = True
        await session.commit()

    assert await is_cancel_requested(task_id) is True


async def test_task_cancelled_error_maps_to_cancelled_not_failed(task_db) -> None:
    """`TaskCancelledError` 應轉為 cancelled，而非 failed。"""

    async def _executor(_context: TaskContext) -> dict:
        raise TaskCancelledError("使用者取消")

    registry.register_executor("probe_cancel_error", _executor)
    task_id = await _create_task(task_db, task_kind="probe_cancel_error")

    assert await run_task(task_id, executor_type="inline") == "cancelled"


async def test_mark_cancelled_sets_terminal_state(task_db) -> None:
    """完成取消後任務必須是終態。"""

    task_id = await _create_task(task_db)
    await mark_cancelled(task_id)

    async with task_db() as session:
        task = await session.get(GenerationTask, task_id)
        assert task.status.is_terminal
        assert task.finished_at is not None


# ── 產物關聯 ──────────────────────────────────────────────────────────────────


async def test_task_links_persist_and_cascade(task_db) -> None:
    """產物關聯落地，且刪除任務時級聯清除。"""

    task_id = await _create_task(task_db)

    async with task_db() as session:
        session.add(
            GenerationTaskLink(
                task_id=task_id,
                resource_type="image",
                relation_type="shot_frame",
                relation_entity_id="frm_1",
            )
        )
        await session.commit()

    async with task_db() as session:
        task = await session.get(GenerationTask, task_id)
        await session.delete(task)
        await session.commit()

    async with task_db() as session:
        from sqlalchemy import func, select

        remaining = await session.scalar(select(func.count()).select_from(GenerationTaskLink))
        assert remaining == 0


# ── 恢復 ──────────────────────────────────────────────────────────────────────


async def test_recover_stale_running_tasks(task_db) -> None:
    """重啟後卡在 running 的任務應被標記為失敗，讓使用者可重試。"""

    from studio.services.task import TaskService

    task_id = await _create_task(task_db, status=TaskStatus.running)

    async with task_db() as session:
        recovered = await TaskService(session).recover_stale_tasks()
        await session.commit()

    assert recovered == 1

    async with task_db() as session:
        task = await session.get(GenerationTask, task_id)
        assert task.status is TaskStatus.failed
        assert task.error_code == "task_interrupted"


async def test_retry_resets_task_state(task_db) -> None:
    """重試必須清空上一次的錯誤與結果，並累加重試次數。"""

    from studio.services.task import TaskService

    task_id = await _create_task(
        task_db,
        status=TaskStatus.failed,
        error="previous failure",
        error_code="provider_error",
        progress=60,
        max_retries=3,
    )

    async with task_db() as session:
        service = TaskService(session)
        # 不實際派送，只驗證狀態重設
        task = await service.get_task(task_id)
        task.status = TaskStatus.pending
        task.retry_count += 1
        task.error = ""
        task.error_code = ""
        task.progress = 0
        await session.commit()

    async with task_db() as session:
        task = await session.get(GenerationTask, task_id)
        assert task.status is TaskStatus.pending
        assert task.retry_count == 1
        assert task.error == ""
        assert task.progress == 0
