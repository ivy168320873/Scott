"""生成任務 Service。

Phase 3 範圍：任務紀錄的建立、查詢、取消請求與產物關聯管理。
實際排程與執行（Celery / inline、進度回寫、重試）於 Phase 4 接上；
本檔刻意不假裝任務會被執行 —— 建立後狀態就是 `pending`，
直到 Phase 4 的執行器接手。
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from studio.core import ids
from studio.core.errors import StateTransitionError
from studio.models.task import GenerationTask, GenerationTaskLink
from studio.models.types import TaskStatus
from studio.repositories import (
    ChapterRepository,
    ProjectRepository,
    ShotRepository,
    TaskLinkRepository,
    TaskRepository,
)
from studio.schemas.task import TaskCancel, TaskCreate, TaskLinkCreate, TaskLinkUpdate
from studio.services.base import ensure_found, translate_integrity_error


class TaskService:
    """生成任務業務邏輯。"""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.tasks = TaskRepository(session)
        self.links = TaskLinkRepository(session)
        self.projects = ProjectRepository(session)
        self.chapters = ChapterRepository(session)
        self.shots = ShotRepository(session)

    async def list_tasks(
        self,
        *,
        offset: int,
        limit: int,
        search: str | None = None,
        status: TaskStatus | None = None,
        task_kind: str | None = None,
        project_id: str | None = None,
        chapter_id: str | None = None,
        shot_id: str | None = None,
    ) -> tuple[list[GenerationTask], int]:
        """分頁列出任務。

        任務中心以「最近更新」排序，讓進行中的任務自然浮到最上方。
        """

        return await self.tasks.list_page(
            offset=offset,
            limit=limit,
            search=search,
            status=status,
            task_kind=task_kind,
            project_id=project_id,
            chapter_id=chapter_id,
            shot_id=shot_id,
        )

    async def list_active(self, *, limit: int = 50) -> list[GenerationTask]:
        """列出所有進行中的任務。

        供任務中心與重啟後的恢復流程使用 —— 因為狀態存在資料庫，
        process 重啟不會遺失進行中的任務。
        """

        items, _ = await self.tasks.list_page(offset=0, limit=limit)
        return [task for task in items if task.status.is_active]

    async def get_task(self, task_id: str) -> GenerationTask:
        """取得任務，不存在時拋出 404。"""

        return ensure_found(await self.tasks.get(task_id), resource="任務", entity_id=task_id)

    async def create_task(self, payload: TaskCreate, *, dispatch: bool = True) -> GenerationTask:
        """建立任務紀錄。

        先驗證關聯資源存在，讓錯誤是明確的 404 而非稍後執行時才失敗。
        """

        if payload.project_id:
            ensure_found(await self.projects.get(payload.project_id), resource="專案", entity_id=payload.project_id)
        if payload.chapter_id:
            ensure_found(await self.chapters.get(payload.chapter_id), resource="章節", entity_id=payload.chapter_id)
        if payload.shot_id:
            ensure_found(await self.shots.get(payload.shot_id), resource="分鏡", entity_id=payload.shot_id)

        task = GenerationTask(
            id=ids.new_id(ids.TASK),
            task_kind=payload.task_kind.value,
            mode=payload.mode,
            payload=payload.payload,
            project_id=payload.project_id,
            chapter_id=payload.chapter_id,
            shot_id=payload.shot_id,
            provider_id=payload.provider_id,
            model_id=payload.model_id,
            max_retries=payload.max_retries,
            status=TaskStatus.pending,
        )
        try:
            task = await self.tasks.add(task)
        except IntegrityError as exc:
            raise translate_integrity_error(exc, resource="任務") from exc

        if dispatch:
            # 必須先 commit：worker 可能在本交易提交前就開始執行，
            # 屆時會查不到這筆任務。
            await self.session.commit()
            from studio.tasks.dispatch import dispatch_task

            task.executor_type = dispatch_task(task.id)
            await self.session.flush()

        return task

    async def request_cancel(self, task_id: str, payload: TaskCancel) -> GenerationTask:
        """請求取消任務。

        兩段式取消：這裡只設定旗標，實際中止由執行器在安全點偵測後完成，
        避免強制終止導致產物只寫入一半。

        已達終態的任務不可取消 —— 那是無意義的狀態流轉。
        """

        task = await self.get_task(task_id)

        if task.status.is_terminal:
            raise StateTransitionError(
                f"任務已結束（狀態：{task.status.value}），無法取消",
                details={"task_id": task_id, "status": task.status.value},
            )

        if task.cancel_requested:
            # 重複請求視為冪等成功，避免使用者重按產生錯誤。
            return task

        task.cancel_requested = True
        task.cancel_requested_at = datetime.now(UTC)
        task.cancel_reason = payload.reason
        await self.session.flush()
        return task

    async def delete_task(self, task_id: str) -> None:
        """刪除任務紀錄。

        進行中的任務不可刪除：刪掉紀錄後執行器仍在跑，會變成無法追蹤、
        無法取消的孤兒工作。請先取消再刪除。
        """

        task = await self.get_task(task_id)

        if task.status.is_active:
            raise StateTransitionError(
                "任務仍在進行中，請先取消再刪除",
                details={"task_id": task_id, "status": task.status.value},
            )

        await self.tasks.remove(task)

    # ── 產物關聯 ──────────────────────────────────────────────────────────────

    async def list_links(self, task_id: str) -> list[GenerationTaskLink]:
        """列出任務的產物關聯。"""

        await self.get_task(task_id)
        items, _ = await self.links.list_page(offset=0, limit=200, task_id=task_id)
        return items

    async def add_link(self, task_id: str, payload: TaskLinkCreate) -> GenerationTaskLink:
        """建立任務產物關聯。"""

        await self.get_task(task_id)
        link = GenerationTaskLink(task_id=task_id, **payload.model_dump())
        try:
            return await self.links.add(link)
        except IntegrityError as exc:
            raise translate_integrity_error(exc, resource="任務產物關聯") from exc

    async def update_link(self, link_id: int, payload: TaskLinkUpdate) -> GenerationTaskLink:
        """更新產物的採用狀態。"""

        link = ensure_found(await self.links.get(link_id), resource="任務產物關聯", entity_id=link_id)
        link.status = payload.status
        await self.session.flush()
        return link

    async def retry_task(self, task_id: str) -> GenerationTask:
        """重跑一個已結束的任務。

        沿用同一筆紀錄而非另建新任務：使用者關心的是「這件工作」的最終結果，
        分成多筆會讓任務中心出現重複項目、也難以追蹤實際嘗試次數。

        Raises:
            StateTransitionError: 任務仍在執行中，或已達重試上限。
        """

        task = await self.get_task(task_id)

        if task.status.is_active:
            raise StateTransitionError(
                "任務仍在進行中，無法重試",
                details={"task_id": task_id, "status": task.status.value},
            )

        if task.max_retries and task.retry_count >= task.max_retries:
            raise StateTransitionError(
                f"已達重試上限（{task.max_retries} 次）",
                details={"task_id": task_id, "retry_count": task.retry_count},
            )

        task.status = TaskStatus.pending
        task.retry_count += 1
        task.progress = 0
        task.progress_message = ""
        task.error = ""
        task.error_code = ""
        task.result = None
        task.started_at = None
        task.finished_at = None
        task.cancel_requested = False
        task.cancel_requested_at = None
        task.cancelled_at = None
        task.cancel_reason = ""

        await self.session.commit()

        from studio.tasks.dispatch import dispatch_task

        task.executor_type = dispatch_task(task.id)
        await self.session.flush()
        return task

    async def recover_stale_tasks(self) -> int:
        """把重啟前卡在 running 的任務標記為失敗。

        Worker 意外中止時，任務會永遠停在 `running`。啟動時掃描一次，
        讓使用者看到明確的失敗而不是無止盡的「執行中」，並可手動重試。

        Returns:
            被標記為失敗的任務數。
        """

        from datetime import UTC, datetime

        stale, _ = await self.tasks.list_page(offset=0, limit=500, status=TaskStatus.running)
        for task in stale:
            task.status = TaskStatus.failed
            task.error = "服務重啟時任務仍在執行中，已標記為失敗，可重試"
            task.error_code = "task_interrupted"
            task.finished_at = datetime.now(UTC)

        await self.session.flush()
        return len(stale)
