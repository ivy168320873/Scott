"""生成任務 API。

Phase 3 提供任務紀錄的建立、查詢與取消請求；
實際排程與執行於 Phase 4 接上。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, status

from studio.core.deps import DbSession, Paging
from studio.core.security import CurrentUser
from studio.models.types import TaskStatus
from studio.schemas.common import ApiResponse, OkData, Page
from studio.schemas.task import (
    TaskCancel,
    TaskCreate,
    TaskLinkCreate,
    TaskLinkRead,
    TaskLinkUpdate,
    TaskRead,
)
from studio.services.task import TaskService

router = APIRouter(tags=["tasks"])


@router.get(
    "/tasks",
    response_model=ApiResponse[Page[TaskRead]],
    operation_id="listTasks",
    summary="列出生成任務",
)
async def list_tasks(
    db: DbSession,
    _user: CurrentUser,
    paging: Paging,
    search: Annotated[str | None, Query(description="以任務類型、步驟描述做模糊搜尋")] = None,
    task_status: Annotated[TaskStatus | None, Query(alias="status", description="依狀態過濾")] = None,
    task_kind: Annotated[str | None, Query(description="依任務類型過濾")] = None,
    project_id: Annotated[str | None, Query(description="依專案過濾")] = None,
    chapter_id: Annotated[str | None, Query(description="依章節過濾")] = None,
    shot_id: Annotated[str | None, Query(description="依分鏡過濾")] = None,
) -> ApiResponse[Page[TaskRead]]:
    """分頁列出任務（依最近更新排序）。"""

    items, total = await TaskService(db).list_tasks(
        offset=paging.offset,
        limit=paging.limit,
        search=search,
        status=task_status,
        task_kind=task_kind,
        project_id=project_id,
        chapter_id=chapter_id,
        shot_id=shot_id,
    )
    return ApiResponse.ok(
        Page.build(
            [TaskRead.model_validate(item) for item in items],
            page=paging.page,
            page_size=paging.page_size,
            total=total,
        )
    )


@router.get(
    "/tasks/active",
    response_model=ApiResponse[list[TaskRead]],
    operation_id="listActiveTasks",
    summary="列出進行中的任務",
)
async def list_active_tasks(db: DbSession, _user: CurrentUser) -> ApiResponse[list[TaskRead]]:
    """列出所有尚未結束的任務。

    任務狀態存於資料庫，因此重新整理頁面或重啟服務後仍可恢復顯示。
    """

    items = await TaskService(db).list_active()
    return ApiResponse.ok([TaskRead.model_validate(item) for item in items])


@router.get(
    "/tasks/{task_id}",
    response_model=ApiResponse[TaskRead],
    operation_id="getTask",
    summary="取得任務",
)
async def get_task(task_id: str, db: DbSession, _user: CurrentUser) -> ApiResponse[TaskRead]:
    """取得單一任務的完整狀態。"""

    task = await TaskService(db).get_task(task_id)
    return ApiResponse.ok(TaskRead.model_validate(task))


@router.post(
    "/tasks",
    response_model=ApiResponse[TaskRead],
    status_code=status.HTTP_201_CREATED,
    operation_id="createTask",
    summary="建立生成任務",
)
async def create_task(payload: TaskCreate, db: DbSession, _user: CurrentUser) -> ApiResponse[TaskRead]:
    """建立任務紀錄。關聯資源不存在時回 404。"""

    task = await TaskService(db).create_task(payload)
    return ApiResponse.ok(TaskRead.model_validate(task))


@router.post(
    "/tasks/{task_id}/cancel",
    response_model=ApiResponse[TaskRead],
    operation_id="cancelTask",
    summary="請求取消任務",
)
async def cancel_task(
    task_id: str,
    payload: TaskCancel,
    db: DbSession,
    _user: CurrentUser,
) -> ApiResponse[TaskRead]:
    """請求取消任務。已結束的任務回 409。"""

    task = await TaskService(db).request_cancel(task_id, payload)
    return ApiResponse.ok(TaskRead.model_validate(task))


@router.post(
    "/tasks/{task_id}/retry",
    response_model=ApiResponse[TaskRead],
    operation_id="retryTask",
    summary="重試任務",
)
async def retry_task(task_id: str, db: DbSession, _user: CurrentUser) -> ApiResponse[TaskRead]:
    """重跑一個已結束的任務。仍在執行或已達重試上限時回 409。"""

    task = await TaskService(db).retry_task(task_id)
    return ApiResponse.ok(TaskRead.model_validate(task))


@router.delete(
    "/tasks/{task_id}",
    response_model=ApiResponse[OkData],
    operation_id="deleteTask",
    summary="刪除任務紀錄",
)
async def delete_task(task_id: str, db: DbSession, _user: CurrentUser) -> ApiResponse[OkData]:
    """刪除任務紀錄。進行中的任務回 409，請先取消。"""

    await TaskService(db).delete_task(task_id)
    return ApiResponse.ok(OkData())


@router.get(
    "/tasks/{task_id}/links",
    response_model=ApiResponse[list[TaskLinkRead]],
    operation_id="listTaskLinks",
    summary="列出任務產物",
)
async def list_task_links(task_id: str, db: DbSession, _user: CurrentUser) -> ApiResponse[list[TaskLinkRead]]:
    """列出任務的產物關聯與各自的採用狀態。"""

    items = await TaskService(db).list_links(task_id)
    return ApiResponse.ok([TaskLinkRead.model_validate(item) for item in items])


@router.post(
    "/tasks/{task_id}/links",
    response_model=ApiResponse[TaskLinkRead],
    status_code=status.HTTP_201_CREATED,
    operation_id="createTaskLink",
    summary="建立任務產物關聯",
)
async def create_task_link(
    task_id: str,
    payload: TaskLinkCreate,
    db: DbSession,
    _user: CurrentUser,
) -> ApiResponse[TaskLinkRead]:
    """建立任務與業務資源／產物檔案的關聯。"""

    link = await TaskService(db).add_link(task_id, payload)
    return ApiResponse.ok(TaskLinkRead.model_validate(link))


@router.patch(
    "/task-links/{link_id}",
    response_model=ApiResponse[TaskLinkRead],
    operation_id="updateTaskLink",
    summary="更新產物採用狀態",
)
async def update_task_link(
    link_id: int,
    payload: TaskLinkUpdate,
    db: DbSession,
    _user: CurrentUser,
) -> ApiResponse[TaskLinkRead]:
    """更新任務產物的採用狀態（accepted / todo / rejected）。"""

    link = await TaskService(db).update_link(link_id, payload)
    return ApiResponse.ok(TaskLinkRead.model_validate(link))
