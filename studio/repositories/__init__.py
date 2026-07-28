"""Scott Studio Repository 層。

只負責資料存取；業務規則屬於 Service 層。
"""

from __future__ import annotations

from sqlalchemy import select

from studio.models.asset import Actor, AssetImage, Character, Costume, Prop, Scene, ShotAssetLink
from studio.models.file import FileItem, FileUsage
from studio.models.project import Chapter, Project
from studio.models.provider import Model, PromptTemplate, Provider
from studio.models.shot import (
    Shot,
    ShotDetail,
    ShotDialogue,
    ShotDialogueCandidate,
    ShotExtractedCandidate,
    ShotFrame,
)
from studio.models.task import GenerationTask, GenerationTaskLink
from studio.repositories.base import BaseRepository


class ProjectRepository(BaseRepository[Project]):
    """專案。"""

    model = Project
    searchable_fields = ("name", "description", "genre")


class ChapterRepository(BaseRepository[Chapter]):
    """章節。"""

    model = Chapter
    searchable_fields = ("title", "summary")


class ShotRepository(BaseRepository[Shot]):
    """分鏡。

    一律 eager load `detail`：回應內含拍攝細節，而 async session 下的
    延遲載入會發生在 Pydantic 的同步序列化過程中，直接拋 `MissingGreenlet`。
    """

    model = Shot
    searchable_fields = ("title", "script_excerpt")

    def _base_select(self):
        """預先載入分鏡細節。"""

        from sqlalchemy.orm import selectinload

        return select(Shot).options(selectinload(Shot.detail))

    def _apply_ordering(self, stmt, order_by):
        """分鏡預設以章節內序號遞增排序。

        分鏡是有序的敘事單位，用「最近更新」排序會讓故事順序錯亂，
        因此覆寫基底的預設排序。
        """

        if not order_by:
            return stmt.order_by(Shot.index.asc())
        return super()._apply_ordering(stmt, order_by)


class ShotDetailRepository(BaseRepository[ShotDetail]):
    """分鏡細節。"""

    model = ShotDetail


class ShotFrameRepository(BaseRepository[ShotFrame]):
    """分鏡關鍵幀。"""

    model = ShotFrame


class ShotDialogueRepository(BaseRepository[ShotDialogue]):
    """分鏡對白。"""

    model = ShotDialogue
    searchable_fields = ("speaker_name", "content")


class ShotCandidateRepository(BaseRepository[ShotExtractedCandidate]):
    """資產提取候選。"""

    model = ShotExtractedCandidate
    searchable_fields = ("name", "description")


class ShotDialogueCandidateRepository(BaseRepository[ShotDialogueCandidate]):
    """對白提取候選。"""

    model = ShotDialogueCandidate
    searchable_fields = ("speaker_name", "content")


class CharacterRepository(BaseRepository[Character]):
    """角色。"""

    model = Character
    searchable_fields = ("name", "description", "personality")


class ActorRepository(BaseRepository[Actor]):
    """演員。"""

    model = Actor
    searchable_fields = ("name", "description")


class SceneRepository(BaseRepository[Scene]):
    """場景。"""

    model = Scene
    searchable_fields = ("name", "description")


class PropRepository(BaseRepository[Prop]):
    """道具。"""

    model = Prop
    searchable_fields = ("name", "description", "category")


class CostumeRepository(BaseRepository[Costume]):
    """服裝。"""

    model = Costume
    searchable_fields = ("name", "description")


class AssetImageRepository(BaseRepository[AssetImage]):
    """資產圖片。"""

    model = AssetImage


class ShotAssetLinkRepository(BaseRepository[ShotAssetLink]):
    """分鏡↔資產關聯。"""

    model = ShotAssetLink


class FileRepository(BaseRepository[FileItem]):
    """媒體檔案。"""

    model = FileItem
    searchable_fields = ("filename",)


class FileUsageRepository(BaseRepository[FileUsage]):
    """檔案用途。"""

    model = FileUsage


class ProviderRepository(BaseRepository[Provider]):
    """供應商。"""

    model = Provider
    searchable_fields = ("name", "description")


class ModelRepository(BaseRepository[Model]):
    """模型設定。

    一律 eager load `provider`：回應要顯示供應商名稱，而 async session 下的
    延遲載入會發生在 Pydantic 的同步序列化過程中，直接拋 `MissingGreenlet`。
    """

    model = Model
    searchable_fields = ("name", "model_id", "description")

    def _base_select(self):
        """預先載入所屬供應商。"""

        from sqlalchemy.orm import selectinload

        return select(Model).options(selectinload(Model.provider))


class PromptTemplateRepository(BaseRepository[PromptTemplate]):
    """提示詞模板。"""

    model = PromptTemplate
    searchable_fields = ("name", "content", "description")


class TaskRepository(BaseRepository[GenerationTask]):
    """生成任務。"""

    model = GenerationTask
    searchable_fields = ("task_kind", "progress_message")


class TaskLinkRepository(BaseRepository[GenerationTaskLink]):
    """任務產物關聯。"""

    model = GenerationTaskLink


__all__ = [
    "BaseRepository",
    "ProjectRepository",
    "ChapterRepository",
    "ShotRepository",
    "ShotDetailRepository",
    "ShotFrameRepository",
    "ShotDialogueRepository",
    "ShotCandidateRepository",
    "ShotDialogueCandidateRepository",
    "CharacterRepository",
    "ActorRepository",
    "SceneRepository",
    "PropRepository",
    "CostumeRepository",
    "AssetImageRepository",
    "ShotAssetLinkRepository",
    "FileRepository",
    "FileUsageRepository",
    "ProviderRepository",
    "ModelRepository",
    "PromptTemplateRepository",
    "TaskRepository",
    "TaskLinkRepository",
]
