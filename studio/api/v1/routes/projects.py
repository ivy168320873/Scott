"""專案、章節與腳本 API。

API 層職責僅限於：收參、驗證、權限、回應組合。
所有業務規則都在 `ProjectService`。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, status

from studio.core.deps import DbSession, Paging
from studio.core.security import CurrentUser
from studio.models.types import ChapterStatus, ProjectStatus
from studio.schemas.common import ApiResponse, OkData, Page
from studio.schemas.project import (
    ChapterCreate,
    ChapterRead,
    ChapterUpdate,
    ProjectCreate,
    ProjectRead,
    ProjectUpdate,
    ScriptRead,
    ScriptUpdate,
)
from studio.services.project import ProjectService

router = APIRouter(tags=["projects"])


def _chapter_read(chapter) -> ChapterRead:
    """組出章節回應，附上「是否已有腳本」而不夾帶腳本全文。"""

    data = ChapterRead.model_validate(chapter)
    data.has_script = bool(chapter.raw_text)
    return data


# ── 專案 ──────────────────────────────────────────────────────────────────────


@router.get(
    "/projects",
    response_model=ApiResponse[Page[ProjectRead]],
    operation_id="listProjects",
    summary="列出專案",
)
async def list_projects(
    db: DbSession,
    _user: CurrentUser,
    paging: Paging,
    search: Annotated[str | None, Query(description="以名稱、簡介、題材做模糊搜尋")] = None,
    project_status: Annotated[ProjectStatus | None, Query(alias="status", description="依狀態過濾")] = None,
    order_by: Annotated[str | None, Query(description="排序欄位；前綴 - 表示遞減")] = None,
) -> ApiResponse[Page[ProjectRead]]:
    """分頁列出專案。"""

    items, total = await ProjectService(db).list_projects(
        offset=paging.offset,
        limit=paging.limit,
        search=search,
        status=project_status,
        order_by=order_by,
    )
    return ApiResponse.ok(
        Page.build(
            [ProjectRead.model_validate(item) for item in items],
            page=paging.page,
            page_size=paging.page_size,
            total=total,
        )
    )


@router.get(
    "/projects/{project_id}",
    response_model=ApiResponse[ProjectRead],
    operation_id="getProject",
    summary="取得專案",
)
async def get_project(project_id: str, db: DbSession, _user: CurrentUser) -> ApiResponse[ProjectRead]:
    """取得單一專案。"""

    project = await ProjectService(db).get_project(project_id)
    return ApiResponse.ok(ProjectRead.model_validate(project))


@router.post(
    "/projects",
    response_model=ApiResponse[ProjectRead],
    status_code=status.HTTP_201_CREATED,
    operation_id="createProject",
    summary="建立專案",
)
async def create_project(payload: ProjectCreate, db: DbSession, _user: CurrentUser) -> ApiResponse[ProjectRead]:
    """建立專案。"""

    project = await ProjectService(db).create_project(payload)
    return ApiResponse.ok(ProjectRead.model_validate(project))


@router.patch(
    "/projects/{project_id}",
    response_model=ApiResponse[ProjectRead],
    operation_id="updateProject",
    summary="更新專案",
)
async def update_project(
    project_id: str,
    payload: ProjectUpdate,
    db: DbSession,
    _user: CurrentUser,
) -> ApiResponse[ProjectRead]:
    """更新專案；未傳入的欄位保留原值。"""

    project = await ProjectService(db).update_project(project_id, payload)
    return ApiResponse.ok(ProjectRead.model_validate(project))


@router.delete(
    "/projects/{project_id}",
    response_model=ApiResponse[OkData],
    operation_id="deleteProject",
    summary="刪除專案",
)
async def delete_project(project_id: str, db: DbSession, _user: CurrentUser) -> ApiResponse[OkData]:
    """刪除專案及其所有下層資料。"""

    await ProjectService(db).delete_project(project_id)
    return ApiResponse.ok(OkData())


@router.post(
    "/projects/{project_id}/archive",
    response_model=ApiResponse[ProjectRead],
    operation_id="archiveProject",
    summary="封存專案",
)
async def archive_project(project_id: str, db: DbSession, _user: CurrentUser) -> ApiResponse[ProjectRead]:
    """封存專案（保留資料的安全替代方案）。"""

    project = await ProjectService(db).archive_project(project_id)
    return ApiResponse.ok(ProjectRead.model_validate(project))


@router.post(
    "/projects/{project_id}/refresh-stats",
    response_model=ApiResponse[ProjectRead],
    operation_id="refreshProjectStats",
    summary="重算專案統計",
)
async def refresh_project_stats(project_id: str, db: DbSession, _user: CurrentUser) -> ApiResponse[ProjectRead]:
    """重算專案的聚合統計。"""

    project = await ProjectService(db).refresh_stats(project_id)
    return ApiResponse.ok(ProjectRead.model_validate(project))


# ── 章節 ──────────────────────────────────────────────────────────────────────


@router.get(
    "/projects/{project_id}/chapters",
    response_model=ApiResponse[Page[ChapterRead]],
    operation_id="listChapters",
    summary="列出章節",
)
async def list_chapters(
    project_id: str,
    db: DbSession,
    _user: CurrentUser,
    paging: Paging,
    search: Annotated[str | None, Query(description="以標題、摘要做模糊搜尋")] = None,
    chapter_status: Annotated[ChapterStatus | None, Query(alias="status", description="依狀態過濾")] = None,
) -> ApiResponse[Page[ChapterRead]]:
    """分頁列出某專案的章節（依序號遞增）。"""

    items, total = await ProjectService(db).list_chapters(
        project_id,
        offset=paging.offset,
        limit=paging.limit,
        search=search,
        status=chapter_status,
    )
    return ApiResponse.ok(
        Page.build(
            [_chapter_read(item) for item in items],
            page=paging.page,
            page_size=paging.page_size,
            total=total,
        )
    )


@router.get(
    "/chapters/{chapter_id}",
    response_model=ApiResponse[ChapterRead],
    operation_id="getChapter",
    summary="取得章節",
)
async def get_chapter(chapter_id: str, db: DbSession, _user: CurrentUser) -> ApiResponse[ChapterRead]:
    """取得單一章節。"""

    chapter = await ProjectService(db).get_chapter(chapter_id)
    return ApiResponse.ok(_chapter_read(chapter))


@router.post(
    "/projects/{project_id}/chapters",
    response_model=ApiResponse[ChapterRead],
    status_code=status.HTTP_201_CREATED,
    operation_id="createChapter",
    summary="建立章節",
)
async def create_chapter(
    project_id: str,
    payload: ChapterCreate,
    db: DbSession,
    _user: CurrentUser,
) -> ApiResponse[ChapterRead]:
    """建立章節；序號留空時自動接續。"""

    chapter = await ProjectService(db).create_chapter(project_id, payload)
    return ApiResponse.ok(_chapter_read(chapter))


@router.patch(
    "/chapters/{chapter_id}",
    response_model=ApiResponse[ChapterRead],
    operation_id="updateChapter",
    summary="更新章節",
)
async def update_chapter(
    chapter_id: str,
    payload: ChapterUpdate,
    db: DbSession,
    _user: CurrentUser,
) -> ApiResponse[ChapterRead]:
    """更新章節；未傳入的欄位保留原值。"""

    chapter = await ProjectService(db).update_chapter(chapter_id, payload)
    return ApiResponse.ok(_chapter_read(chapter))


@router.delete(
    "/chapters/{chapter_id}",
    response_model=ApiResponse[OkData],
    operation_id="deleteChapter",
    summary="刪除章節",
)
async def delete_chapter(chapter_id: str, db: DbSession, _user: CurrentUser) -> ApiResponse[OkData]:
    """刪除章節及其分鏡。"""

    await ProjectService(db).delete_chapter(chapter_id)
    return ApiResponse.ok(OkData())


# ── 腳本 ──────────────────────────────────────────────────────────────────────


@router.get(
    "/chapters/{chapter_id}/script",
    response_model=ApiResponse[ScriptRead],
    operation_id="getScript",
    summary="取得章節腳本",
)
async def get_script(chapter_id: str, db: DbSession, _user: CurrentUser) -> ApiResponse[ScriptRead]:
    """取得章節腳本原文與精簡稿。"""

    chapter = await ProjectService(db).get_script(chapter_id)
    return ApiResponse.ok(
        ScriptRead(
            chapter_id=chapter.id,
            raw_text=chapter.raw_text,
            condensed_text=chapter.condensed_text,
            raw_length=len(chapter.raw_text),
            condensed_length=len(chapter.condensed_text),
        )
    )


@router.put(
    "/chapters/{chapter_id}/script",
    response_model=ApiResponse[ScriptRead],
    operation_id="updateScript",
    summary="更新章節腳本",
)
async def update_script(
    chapter_id: str,
    payload: ScriptUpdate,
    db: DbSession,
    _user: CurrentUser,
) -> ApiResponse[ScriptRead]:
    """更新腳本原文；內容變更時會清空既有精簡稿。"""

    chapter = await ProjectService(db).update_script(chapter_id, payload)
    return ApiResponse.ok(
        ScriptRead(
            chapter_id=chapter.id,
            raw_text=chapter.raw_text,
            condensed_text=chapter.condensed_text,
            raw_length=len(chapter.raw_text),
            condensed_length=len(chapter.condensed_text),
        )
    )
