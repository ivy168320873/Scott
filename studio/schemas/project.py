"""專案與章節 schema。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from studio.models.types import ChapterStatus, ProjectStatus, ProjectVisualStyle


class ProjectCreate(BaseModel):
    """建立專案。"""

    name: str = Field(min_length=1, max_length=255, description="專案名稱")
    description: str = Field(default="", description="專案簡介")
    genre: str = Field(default="", max_length=64, description="題材")
    visual_style: ProjectVisualStyle = Field(
        default=ProjectVisualStyle.live_action,
        description="畫面表現形式",
    )
    style_prompt: str = Field(default="", description="專案級風格提示詞")
    seed: int = Field(default=0, ge=0, description="生成隨機種子；0 表示不指定")
    unify_style: bool = Field(default=True, description="是否跨章節統一風格")
    default_video_ratio: str = Field(default="9:16", max_length=16, description="預設影片比例")


class ProjectUpdate(BaseModel):
    """更新專案；未傳入的欄位保留原值。"""

    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    genre: str | None = Field(default=None, max_length=64)
    visual_style: ProjectVisualStyle | None = None
    style_prompt: str | None = None
    seed: int | None = Field(default=None, ge=0)
    unify_style: bool | None = None
    default_video_ratio: str | None = Field(default=None, max_length=16)
    status: ProjectStatus | None = None
    cover_file_id: str | None = None


class ProjectStats(BaseModel):
    """專案聚合統計。"""

    chapter_count: int = Field(default=0, description="章節數")
    shot_count: int = Field(default=0, description="分鏡總數")
    ready_shot_count: int = Field(default=0, description="已完成提取確認的分鏡數")
    character_count: int = Field(default=0, description="角色數")
    scene_count: int = Field(default=0, description="場景數")


class ProjectRead(BaseModel):
    """專案對外表示。"""

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(description="專案 ID")
    name: str = Field(description="專案名稱")
    description: str = Field(description="專案簡介")
    genre: str = Field(description="題材")
    visual_style: ProjectVisualStyle = Field(description="畫面表現形式")
    style_prompt: str = Field(description="專案級風格提示詞")
    status: ProjectStatus = Field(description="專案狀態")
    seed: int = Field(description="生成隨機種子")
    unify_style: bool = Field(description="是否統一風格")
    default_video_ratio: str = Field(description="預設影片比例")
    cover_file_id: str | None = Field(description="封面圖檔案 ID")
    stats: dict[str, Any] = Field(description="聚合統計")
    created_at: datetime = Field(description="建立時間")
    updated_at: datetime = Field(description="更新時間")


# ── 章節 ──────────────────────────────────────────────────────────────────────


class ChapterCreate(BaseModel):
    """建立章節。

    `index` 留空時由 Service 自動指派為專案內的下一個序號。
    """

    title: str = Field(min_length=1, max_length=255, description="章節標題")
    index: int | None = Field(default=None, ge=1, description="章節序號；留空自動指派")
    summary: str = Field(default="", description="章節摘要")
    raw_text: str = Field(default="", description="腳本原文")


class ChapterUpdate(BaseModel):
    """更新章節；未傳入的欄位保留原值。"""

    title: str | None = Field(default=None, min_length=1, max_length=255)
    index: int | None = Field(default=None, ge=1)
    summary: str | None = None
    status: ChapterStatus | None = None


class ChapterRead(BaseModel):
    """章節對外表示。

    刻意不含腳本全文：章節列表可能有數十筆，每筆夾帶完整腳本會讓
    回應體積失控。腳本請用 `GET /chapters/{id}/script`。
    """

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(description="章節 ID")
    project_id: str = Field(description="所屬專案 ID")
    index: int = Field(description="章節序號")
    title: str = Field(description="章節標題")
    summary: str = Field(description="章節摘要")
    status: ChapterStatus = Field(description="章節狀態")
    shot_count: int = Field(description="分鏡數量")
    has_script: bool = Field(default=False, description="是否已輸入腳本")
    created_at: datetime = Field(description="建立時間")
    updated_at: datetime = Field(description="更新時間")


# ── 腳本 ──────────────────────────────────────────────────────────────────────


class ScriptRead(BaseModel):
    """章節腳本內容。"""

    chapter_id: str = Field(description="章節 ID")
    raw_text: str = Field(description="腳本原文")
    condensed_text: str = Field(description="模型精簡後的腳本")
    raw_length: int = Field(description="原文字數")
    condensed_length: int = Field(description="精簡後字數")


class ScriptUpdate(BaseModel):
    """更新章節腳本。

    只允許更新 `raw_text`：`condensed_text` 是 AI 產物，
    由腳本處理任務寫入，不開放人工直接改寫，否則兩者會失去對應關係。
    """

    raw_text: str = Field(description="腳本原文")


__all__ = [
    "ProjectCreate",
    "ProjectUpdate",
    "ProjectRead",
    "ProjectStats",
    "ChapterCreate",
    "ChapterUpdate",
    "ChapterRead",
    "ScriptRead",
    "ScriptUpdate",
]
