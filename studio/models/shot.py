"""分鏡模型：Shot 與其子資源。

拆表理由：
- `Shot` 為主表，欄位穩定，列表查詢只需讀這張表。
- `ShotDetail` 存拍攝參數等細節，與主表 1:1 共享主鍵，避免主表欄位無限膨脹。
- 幀、對白、候選各自成表，因為它們是一對多且各有獨立的生命週期。

狀態語意：`Shot.status` 只表示資訊提取確認狀態（pending / ready），
不表示「生成中」——那是任務系統的職責。
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from studio.core.db import Base, TimestampMixin, enum_column
from studio.models.types import (
    CameraAngle,
    CameraMovement,
    CameraShotType,
    DialogueLineMode,
    ShotCandidateStatus,
    ShotCandidateType,
    ShotDialogueCandidateStatus,
    ShotFrameType,
    ShotStatus,
    VFXType,
)

if TYPE_CHECKING:
    from studio.models.project import Chapter


class Shot(Base, TimestampMixin):
    """分鏡（鏡頭）。

    約束：`chapter_id + index` 唯一，確保章節內鏡頭序號不重複。
    """

    __tablename__ = "studio_shots"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, comment="分鏡 ID")
    chapter_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("studio_chapters.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="所屬章節 ID",
    )
    index: Mapped[int] = mapped_column(Integer, nullable=False, comment="鏡頭序號（章節內唯一）")
    title: Mapped[str] = mapped_column(String(255), nullable=False, default="", comment="鏡頭標題")
    script_excerpt: Mapped[str] = mapped_column(Text, nullable=False, default="", comment="對應的腳本段落")
    status: Mapped[ShotStatus] = mapped_column(
        enum_column(ShotStatus, length=16),
        nullable=False,
        default=ShotStatus.pending,
        comment="資訊提取確認狀態（非執行時狀態）",
    )
    skip_extraction: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        comment="明確跳過提取；為 true 時可直接判定為 ready",
    )
    last_extracted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="最近一次完成提取的時間；用於區分「尚未提取」與「提取結果為空」",
    )
    thumbnail_file_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("studio_files.id", ondelete="SET NULL"),
        nullable=True,
        comment="縮圖檔案 ID",
    )
    generated_video_file_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("studio_files.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="已採用的成品影片檔案 ID",
    )

    chapter: Mapped[Chapter] = relationship(back_populates="shots")
    detail: Mapped[ShotDetail | None] = relationship(
        back_populates="shot",
        cascade="all, delete-orphan",
        passive_deletes=True,
        uselist=False,
    )
    frames: Mapped[list[ShotFrame]] = relationship(
        back_populates="shot",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="ShotFrame.index",
    )
    dialogues: Mapped[list[ShotDialogue]] = relationship(
        back_populates="shot",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="ShotDialogue.index",
    )
    candidates: Mapped[list[ShotExtractedCandidate]] = relationship(
        back_populates="shot",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    dialogue_candidates: Mapped[list[ShotDialogueCandidate]] = relationship(
        back_populates="shot",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="ShotDialogueCandidate.index",
    )

    __table_args__ = (
        UniqueConstraint("chapter_id", "index", name="uq_studio_shots_chapter_index"),
        Index("ix_studio_shots_status", "status"),
        Index("ix_studio_shots_chapter_status", "chapter_id", "status"),
    )


class ShotDetail(Base, TimestampMixin):
    """分鏡拍攝細節（與 Shot 1:1）。

    以外鍵作為主鍵強制一對一，並在刪除鏡頭時級聯清除。
    """

    __tablename__ = "studio_shot_details"

    id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("studio_shots.id", ondelete="CASCADE"),
        primary_key=True,
        comment="分鏡 ID（與 studio_shots.id 共享主鍵）",
    )
    camera_shot: Mapped[CameraShotType] = mapped_column(
        enum_column(CameraShotType, length=16),
        nullable=False,
        default=CameraShotType.ms,
        comment="景別",
    )
    angle: Mapped[CameraAngle] = mapped_column(
        enum_column(CameraAngle, length=16),
        nullable=False,
        default=CameraAngle.eye_level,
        comment="機位角度",
    )
    movement: Mapped[CameraMovement] = mapped_column(
        enum_column(CameraMovement, length=16),
        nullable=False,
        default=CameraMovement.static,
        comment="運鏡方式",
    )
    scene_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("studio_scenes.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="關聯場景 ID",
    )
    duration_seconds: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=5,
        comment="鏡頭時長（秒）；影片生成的唯一時長來源",
    )
    override_video_ratio: Mapped[str | None] = mapped_column(
        String(16),
        nullable=True,
        comment="分鏡級影片比例覆蓋；為空表示繼承專案預設",
    )
    description: Mapped[str] = mapped_column(Text, nullable=False, default="", comment="鏡頭整體描述")
    action_beats: Mapped[list[str]] = mapped_column(
        JSON,
        nullable=False,
        default=list,
        comment="動作節拍（JSON 陣列）；影片生成時轉為時序描述",
    )
    mood_tags: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list, comment="情緒標籤（JSON 陣列）")
    atmosphere: Mapped[str] = mapped_column(Text, nullable=False, default="", comment="氛圍描述")
    vfx_type: Mapped[VFXType] = mapped_column(
        enum_column(VFXType, length=32),
        nullable=False,
        default=VFXType.none,
        comment="視效類型",
    )
    vfx_note: Mapped[str] = mapped_column(Text, nullable=False, default="", comment="視效說明")
    has_bgm: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, comment="是否包含 BGM")
    video_prompt: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="",
        comment="影片生成提示詞；由模板與鏡頭資料組出後可人工修改",
    )

    shot: Mapped[Shot] = relationship(back_populates="detail")


class ShotFrame(Base, TimestampMixin):
    """分鏡關鍵幀（首幀／尾幀／關鍵幀）。

    影片生成通常以首尾幀作為視覺錨點，因此幀與其提示詞需獨立保存與重生成。
    """

    __tablename__ = "studio_shot_frames"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, comment="幀 ID")
    shot_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("studio_shots.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="所屬分鏡 ID",
    )
    frame_type: Mapped[ShotFrameType] = mapped_column(
        enum_column(ShotFrameType, length=16),
        nullable=False,
        comment="幀類型",
    )
    index: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="同類型內的序號")
    prompt: Mapped[str] = mapped_column(Text, nullable=False, default="", comment="該幀的圖片生成提示詞")
    file_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("studio_files.id", ondelete="SET NULL"),
        nullable=True,
        comment="已採用的圖片檔案 ID",
    )

    shot: Mapped[Shot] = relationship(back_populates="frames")

    __table_args__ = (
        UniqueConstraint("shot_id", "frame_type", "index", name="uq_studio_shot_frames_shot_type_index"),
        Index("ix_studio_shot_frames_shot_type", "shot_id", "frame_type"),
    )


class ShotDialogue(Base, TimestampMixin):
    """已確認的分鏡對白。"""

    __tablename__ = "studio_shot_dialogues"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, comment="對白 ID")
    shot_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("studio_shots.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="所屬分鏡 ID",
    )
    index: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="對白順序")
    character_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("studio_characters.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="說話角色 ID；旁白可為空",
    )
    speaker_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        default="",
        comment="說話者名稱；角色尚未建立時的暫存值",
    )
    content: Mapped[str] = mapped_column(Text, nullable=False, default="", comment="對白內容")
    mode: Mapped[DialogueLineMode] = mapped_column(
        enum_column(DialogueLineMode, length=16),
        nullable=False,
        default=DialogueLineMode.dialogue,
        comment="對白模式",
    )
    emotion: Mapped[str] = mapped_column(String(64), nullable=False, default="", comment="情緒提示")

    shot: Mapped[Shot] = relationship(back_populates="dialogues")

    __table_args__ = (
        UniqueConstraint("shot_id", "index", name="uq_studio_shot_dialogues_shot_index"),
    )


class ShotExtractedCandidate(Base, TimestampMixin):
    """AI 提取出的資產候選，等待人工確認。

    候選與正式資產分離的理由：模型提取結果可能重複、錯誤或與既有資產同義。
    先落地為候選，由使用者選擇「連結既有資產」或「忽略」，才能避免資產庫被
    自動產生的雜訊污染。
    """

    __tablename__ = "studio_shot_candidates"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, comment="候選 ID")
    shot_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("studio_shots.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="所屬分鏡 ID",
    )
    candidate_type: Mapped[ShotCandidateType] = mapped_column(
        enum_column(ShotCandidateType, length=16),
        nullable=False,
        comment="候選資產類型",
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False, comment="提取出的名稱")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="", comment="提取出的描述")
    status: Mapped[ShotCandidateStatus] = mapped_column(
        enum_column(ShotCandidateStatus, length=16),
        nullable=False,
        default=ShotCandidateStatus.pending,
        comment="確認狀態",
    )
    linked_entity_id: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        comment="確認後連結到的資產 ID；跨多張資產表，故不設外鍵",
    )

    shot: Mapped[Shot] = relationship(back_populates="candidates")

    __table_args__ = (
        UniqueConstraint("shot_id", "candidate_type", "name", name="uq_studio_shot_candidates_shot_type_name"),
        Index("ix_studio_shot_candidates_status", "shot_id", "status"),
    )


class ShotDialogueCandidate(Base, TimestampMixin):
    """AI 提取出的對白候選，等待人工確認。"""

    __tablename__ = "studio_shot_dialogue_candidates"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, comment="對白候選 ID")
    shot_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("studio_shots.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="所屬分鏡 ID",
    )
    index: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="對白順序")
    speaker_name: Mapped[str] = mapped_column(String(255), nullable=False, default="", comment="提取出的說話者")
    content: Mapped[str] = mapped_column(Text, nullable=False, default="", comment="提取出的對白內容")
    mode: Mapped[DialogueLineMode] = mapped_column(
        enum_column(DialogueLineMode, length=16),
        nullable=False,
        default=DialogueLineMode.dialogue,
        comment="對白模式",
    )
    status: Mapped[ShotDialogueCandidateStatus] = mapped_column(
        enum_column(ShotDialogueCandidateStatus, length=16),
        nullable=False,
        default=ShotDialogueCandidateStatus.pending,
        comment="確認狀態",
    )

    shot: Mapped[Shot] = relationship(back_populates="dialogue_candidates")

    __table_args__ = (
        UniqueConstraint("shot_id", "index", name="uq_studio_shot_dialogue_candidates_shot_index"),
        Index("ix_studio_shot_dialogue_candidates_status", "shot_id", "status"),
    )
