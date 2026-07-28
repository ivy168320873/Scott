"""Scott Studio 資料模型。

所有模型在此匯出，確保 `Base.metadata` 完整 —— Alembic autogenerate 依賴
這個模組被匯入才能偵測到全部資料表。

所有資料表都帶 `studio_` 前綴，與 Scott 既有的 SQLite 資料表完全隔離。
"""

from __future__ import annotations

from studio.models.asset import (
    Actor,
    AssetImage,
    Character,
    Costume,
    Prop,
    Scene,
    ShotAssetLink,
)
from studio.models.file import FileItem, FileUsage
from studio.models.project import Chapter, Project
from studio.models.provider import Model, ModelSettings, PromptTemplate, Provider
from studio.models.shot import (
    Shot,
    ShotDetail,
    ShotDialogue,
    ShotDialogueCandidate,
    ShotExtractedCandidate,
    ShotFrame,
)
from studio.models.task import GenerationTask, GenerationTaskLink

__all__ = [
    # 專案
    "Project",
    "Chapter",
    # 分鏡
    "Shot",
    "ShotDetail",
    "ShotFrame",
    "ShotDialogue",
    "ShotExtractedCandidate",
    "ShotDialogueCandidate",
    # 資產
    "Character",
    "Actor",
    "Scene",
    "Prop",
    "Costume",
    "AssetImage",
    "ShotAssetLink",
    # 檔案
    "FileItem",
    "FileUsage",
    # 任務
    "GenerationTask",
    "GenerationTaskLink",
    # 供應商與模型
    "Provider",
    "Model",
    "ModelSettings",
    "PromptTemplate",
]
