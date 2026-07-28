"""專案與章節模型。

階層：Project → Chapter → Shot。
章節是腳本、分鏡與生成的作業單位。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from sqlalchemy import JSON, Boolean, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from studio.core.db import Base, TimestampMixin, enum_column
from studio.models.types import ChapterStatus, ProjectStatus, ProjectVisualStyle

if TYPE_CHECKING:
    from studio.models.shot import Shot


class Project(Base, TimestampMixin):
    """專案（一部短劇）。

    `stats` 以 JSON 保存聚合統計（章節數、分鏡數、完成率等），
    讓列表頁不需為了顯示摘要而做多表 join；統計由 service 層在變更時更新。
    """

    __tablename__ = "studio_projects"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, comment="專案 ID")
    name: Mapped[str] = mapped_column(String(255), nullable=False, comment="專案名稱")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="", comment="專案簡介")
    genre: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default="",
        comment="題材（自由文字，如都市／科幻／古裝）",
    )
    visual_style: Mapped[ProjectVisualStyle] = mapped_column(
        enum_column(ProjectVisualStyle, length=16),
        nullable=False,
        default=ProjectVisualStyle.live_action,
        comment="畫面表現形式",
    )
    style_prompt: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="",
        comment="專案級風格提示詞；跨鏡頭統一風格的基礎",
    )
    status: Mapped[ProjectStatus] = mapped_column(
        enum_column(ProjectStatus, length=16),
        nullable=False,
        default=ProjectStatus.draft,
        comment="專案狀態",
    )
    seed: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        comment="生成隨機種子；固定種子有助於跨鏡頭一致性（0 表示不指定）",
    )
    unify_style: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        comment="是否在所有章節套用專案級風格",
    )
    default_video_ratio: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default="9:16",
        comment="預設影片比例；短劇以直式 9:16 為主，分鏡可覆蓋",
    )
    cover_file_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("studio_files.id", ondelete="SET NULL"),
        nullable=True,
        comment="封面圖檔案 ID",
    )
    stats: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict, comment="聚合統計（JSON）")

    chapters: Mapped[list[Chapter]] = relationship(
        back_populates="project",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="Chapter.index",
    )

    __table_args__ = (
        Index("ix_studio_projects_updated_at", "updated_at"),
        Index("ix_studio_projects_status", "status"),
    )


class Chapter(Base, TimestampMixin):
    """章節（一集）。

    約束：`project_id + index` 唯一，確保同一專案內集數不重複。

    腳本存兩份：
    - `raw_text`：使用者輸入的原文，永遠保留，作為重新分析的依據。
    - `condensed_text`：模型精簡後的版本，用於後續提取以節省 token。
    重新分析時一律從 `raw_text` 出發，避免精簡誤差逐次累積。
    """

    __tablename__ = "studio_chapters"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, comment="章節 ID")
    project_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("studio_projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="所屬專案 ID",
    )
    index: Mapped[int] = mapped_column(Integer, nullable=False, comment="章節序號（專案內唯一）")
    title: Mapped[str] = mapped_column(String(255), nullable=False, comment="章節標題")
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="", comment="章節摘要")
    raw_text: Mapped[str] = mapped_column(Text, nullable=False, default="", comment="腳本原文")
    condensed_text: Mapped[str] = mapped_column(Text, nullable=False, default="", comment="模型精簡後的腳本")
    status: Mapped[ChapterStatus] = mapped_column(
        enum_column(ChapterStatus, length=16),
        nullable=False,
        default=ChapterStatus.draft,
        comment="章節狀態",
    )
    shot_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        comment="分鏡數量快取；避免列表頁為了顯示數量而 count 子表",
    )

    project: Mapped[Project] = relationship(back_populates="chapters")
    shots: Mapped[list[Shot]] = relationship(
        back_populates="chapter",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="Shot.index",
    )

    __table_args__ = (
        UniqueConstraint("project_id", "index", name="uq_studio_chapters_project_index"),
        Index("ix_studio_chapters_updated_at", "updated_at"),
        Index("ix_studio_chapters_status", "status"),
    )
