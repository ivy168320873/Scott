"""專案、章節與腳本 Service。"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from studio.core import ids
from studio.core.errors import ValidationError
from studio.models.project import Chapter, Project
from studio.models.shot import Shot
from studio.models.types import ShotStatus
from studio.repositories import ChapterRepository, ProjectRepository
from studio.schemas.project import (
    ChapterCreate,
    ChapterUpdate,
    ProjectCreate,
    ProjectUpdate,
    ScriptUpdate,
)
from studio.services.base import apply_updates, ensure_found, translate_integrity_error


class ProjectService:
    """專案業務邏輯。"""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.projects = ProjectRepository(session)
        self.chapters = ChapterRepository(session)

    # ── 專案 ──────────────────────────────────────────────────────────────────

    async def list_projects(
        self,
        *,
        offset: int,
        limit: int,
        search: str | None = None,
        status: str | None = None,
        order_by: str | None = None,
    ) -> tuple[list[Project], int]:
        """分頁列出專案。"""

        return await self.projects.list_page(
            offset=offset,
            limit=limit,
            search=search,
            order_by=order_by,
            status=status,
        )

    async def get_project(self, project_id: str) -> Project:
        """取得專案，不存在時拋出 404。"""

        return ensure_found(await self.projects.get(project_id), resource="專案", entity_id=project_id)

    async def create_project(self, payload: ProjectCreate) -> Project:
        """建立專案。"""

        project = Project(id=ids.new_id(ids.PROJECT), **payload.model_dump())
        try:
            return await self.projects.add(project)
        except IntegrityError as exc:
            raise translate_integrity_error(exc, resource="專案") from exc

    async def update_project(self, project_id: str, payload: ProjectUpdate) -> Project:
        """更新專案；未傳入的欄位保留原值。"""

        project = await self.get_project(project_id)
        apply_updates(project, payload)
        try:
            await self.session.flush()
        except IntegrityError as exc:
            raise translate_integrity_error(exc, resource="專案") from exc
        return project

    async def delete_project(self, project_id: str) -> None:
        """刪除專案。

        章節、分鏡、資產等皆由資料庫外鍵級聯清除，不留孤兒資料。
        """

        await self.projects.remove(await self.get_project(project_id))

    async def archive_project(self, project_id: str) -> Project:
        """封存專案（安全替代刪除）。

        提供給不想真的刪資料的使用情境：封存後仍可查詢與還原。
        """

        from studio.models.types import ProjectStatus

        project = await self.get_project(project_id)
        project.status = ProjectStatus.archived
        await self.session.flush()
        return project

    async def refresh_stats(self, project_id: str) -> Project:
        """重算專案聚合統計。

        統計以 JSON 快取於 `Project.stats`，讓列表頁不必為了顯示摘要
        而對多張子表做 join；此方法是唯一的寫入點。
        """

        from studio.models.asset import Character, Scene

        project = await self.get_project(project_id)

        chapter_count = await self.chapters.count(project_id=project_id)

        shot_total = await self.session.scalar(
            select(func.count())
            .select_from(Shot)
            .join(Chapter, Shot.chapter_id == Chapter.id)
            .where(Chapter.project_id == project_id)
        )
        shot_ready = await self.session.scalar(
            select(func.count())
            .select_from(Shot)
            .join(Chapter, Shot.chapter_id == Chapter.id)
            .where(Chapter.project_id == project_id, Shot.status == ShotStatus.ready)
        )
        character_count = await self.session.scalar(
            select(func.count()).select_from(Character).where(Character.project_id == project_id)
        )
        scene_count = await self.session.scalar(
            select(func.count()).select_from(Scene).where(Scene.project_id == project_id)
        )

        project.stats = {
            "chapter_count": int(chapter_count or 0),
            "shot_count": int(shot_total or 0),
            "ready_shot_count": int(shot_ready or 0),
            "character_count": int(character_count or 0),
            "scene_count": int(scene_count or 0),
        }
        await self.session.flush()
        return project

    # ── 章節 ──────────────────────────────────────────────────────────────────

    async def list_chapters(
        self,
        project_id: str,
        *,
        offset: int,
        limit: int,
        search: str | None = None,
        status: str | None = None,
    ) -> tuple[list[Chapter], int]:
        """分頁列出某專案的章節。"""

        await self.get_project(project_id)  # 專案不存在時回 404 而非空清單
        return await self.chapters.list_page(
            offset=offset,
            limit=limit,
            search=search,
            order_by="index",
            project_id=project_id,
            status=status,
        )

    async def get_chapter(self, chapter_id: str) -> Chapter:
        """取得章節，不存在時拋出 404。"""

        return ensure_found(await self.chapters.get(chapter_id), resource="章節", entity_id=chapter_id)

    async def create_chapter(self, project_id: str, payload: ChapterCreate) -> Chapter:
        """建立章節。

        `index` 未指定時自動接續為專案內的下一個序號，
        讓前端建立章節時不需要先查詢目前最大值。
        """

        await self.get_project(project_id)

        index = payload.index
        if index is None:
            index = await self.chapters.max_index(project_id=project_id) + 1

        chapter = Chapter(
            id=ids.new_id(ids.CHAPTER),
            project_id=project_id,
            index=index,
            title=payload.title,
            summary=payload.summary,
            raw_text=payload.raw_text,
        )
        try:
            chapter = await self.chapters.add(chapter)
        except IntegrityError as exc:
            raise translate_integrity_error(exc, resource="章節") from exc

        await self.refresh_stats(project_id)
        return chapter

    async def update_chapter(self, chapter_id: str, payload: ChapterUpdate) -> Chapter:
        """更新章節；未傳入的欄位保留原值。"""

        chapter = await self.get_chapter(chapter_id)
        apply_updates(chapter, payload)
        try:
            await self.session.flush()
        except IntegrityError as exc:
            raise translate_integrity_error(exc, resource="章節") from exc
        return chapter

    async def delete_chapter(self, chapter_id: str) -> None:
        """刪除章節（分鏡由外鍵級聯清除）。"""

        chapter = await self.get_chapter(chapter_id)
        project_id = chapter.project_id
        await self.chapters.remove(chapter)
        await self.refresh_stats(project_id)

    # ── 腳本 ──────────────────────────────────────────────────────────────────

    async def get_script(self, chapter_id: str) -> Chapter:
        """取得章節腳本。"""

        return await self.get_chapter(chapter_id)

    async def update_script(self, chapter_id: str, payload: ScriptUpdate) -> Chapter:
        """更新章節腳本原文。

        改寫原文會讓既有的精簡稿失去對應關係，因此一併清空
        `condensed_text` —— 下次分析會從新的原文重新產生。
        """

        chapter = await self.get_chapter(chapter_id)

        if len(payload.raw_text) > 2_000_000:
            raise ValidationError("腳本內容過長（上限 2,000,000 字元）")

        if payload.raw_text != chapter.raw_text:
            chapter.raw_text = payload.raw_text
            chapter.condensed_text = ""

        await self.session.flush()
        return chapter
