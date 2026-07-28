"""資產模型：角色、演員、場景、道具、服裝與其圖片。

一致性是短劇生成的核心問題：同一角色在不同鏡頭必須看起來是同一個人。
作法是把角色／場景／道具／服裝抽成可重複引用的實體，並為每個實體保存
參考圖與外觀描述；生成任何鏡頭時都引用同一組參考，藉此降低漂移。

角色（Character）與演員（Actor）分離：
- Character 是劇本層的人物設定（性格、年齡、背景）。
- Actor 是視覺層的形象（長相、體型、參考圖）。
一個角色可換綁不同演員而不需重寫劇本設定。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import JSON, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from studio.core.db import Base, TimestampMixin, enum_column
from studio.models.types import AssetKind, AssetViewAngle


class _AssetBase(TimestampMixin):
    """資產共用欄位。

    以 mixin 而非繼承單表的方式共用欄位：各資產查詢模式差異大，分表能各自
    建立合適的索引，也避免單一寬表塞入大量僅對某類資產有意義的欄位。
    """

    id: Mapped[str] = mapped_column(String(64), primary_key=True, comment="資產 ID")
    name: Mapped[str] = mapped_column(String(255), nullable=False, comment="資產名稱")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="", comment="描述")
    appearance_prompt: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="",
        comment="外觀提示詞；生成時附加於各鏡頭提示詞以維持一致性",
    )
    attributes: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
        comment="結構化屬性（JSON），各資產類型自行定義",
    )


class Character(Base, _AssetBase):
    """角色（劇本層人物設定）。"""

    __tablename__ = "studio_characters"

    project_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("studio_projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="所屬專案 ID",
    )
    actor_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("studio_actors.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="綁定的演員 ID；決定該角色的視覺形象",
    )
    role_type: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="",
        comment="角色定位（主角／配角／龍套等）",
    )
    personality: Mapped[str] = mapped_column(Text, nullable=False, default="", comment="性格描述")

    actor: Mapped[Actor | None] = relationship(back_populates="characters")

    __table_args__ = (
        UniqueConstraint("project_id", "name", name="uq_studio_characters_project_name"),
        Index("ix_studio_characters_updated_at", "updated_at"),
    )


class Actor(Base, _AssetBase):
    """演員（視覺層形象）。

    刻意不綁定專案：演員形象可跨專案重複使用，維持系列作品的一致性。
    """

    __tablename__ = "studio_actors"

    gender: Mapped[str] = mapped_column(String(16), nullable=False, default="", comment="性別")
    age_range: Mapped[str] = mapped_column(String(32), nullable=False, default="", comment="年齡區間")
    reference_file_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("studio_files.id", ondelete="SET NULL"),
        nullable=True,
        comment="主要參考圖檔案 ID",
    )

    characters: Mapped[list[Character]] = relationship(back_populates="actor")

    __table_args__ = (
        UniqueConstraint("name", name="uq_studio_actors_name"),
        Index("ix_studio_actors_updated_at", "updated_at"),
    )


class Scene(Base, _AssetBase):
    """場景。"""

    __tablename__ = "studio_scenes"

    project_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("studio_projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="所屬專案 ID",
    )
    location_type: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="",
        comment="場地類型（室內／室外）",
    )
    time_of_day: Mapped[str] = mapped_column(String(32), nullable=False, default="", comment="時間（日／夜／黃昏）")

    __table_args__ = (
        UniqueConstraint("project_id", "name", name="uq_studio_scenes_project_name"),
        Index("ix_studio_scenes_updated_at", "updated_at"),
    )


class Prop(Base, _AssetBase):
    """道具。"""

    __tablename__ = "studio_props"

    project_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("studio_projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="所屬專案 ID",
    )
    category: Mapped[str] = mapped_column(String(64), nullable=False, default="", comment="道具分類")

    __table_args__ = (
        UniqueConstraint("project_id", "name", name="uq_studio_props_project_name"),
        Index("ix_studio_props_updated_at", "updated_at"),
    )


class Costume(Base, _AssetBase):
    """服裝。"""

    __tablename__ = "studio_costumes"

    project_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("studio_projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="所屬專案 ID",
    )
    character_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("studio_characters.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="預設穿著此服裝的角色 ID",
    )
    season: Mapped[str] = mapped_column(String(32), nullable=False, default="", comment="season／場合")

    __table_args__ = (
        UniqueConstraint("project_id", "name", name="uq_studio_costumes_project_name"),
        Index("ix_studio_costumes_updated_at", "updated_at"),
    )


class AssetImage(Base, TimestampMixin):
    """資產參考圖（支援多視角）。

    以 `asset_kind + asset_id` 多型關聯而非為每種資產各建一張圖片表：
    圖片的欄位與操作在各資產間完全相同，分表只會產生五份重複邏輯。
    代價是無法用資料庫外鍵約束 `asset_id`，因此由 service 層負責驗證存在性。
    """

    __tablename__ = "studio_asset_images"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, comment="資產圖片 ID")
    asset_kind: Mapped[AssetKind] = mapped_column(enum_column(AssetKind, length=16), nullable=False, comment="資產類型")
    asset_id: Mapped[str] = mapped_column(String(64), nullable=False, comment="資產 ID（多型，無外鍵）")
    file_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("studio_files.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="圖片檔案 ID",
    )
    view_angle: Mapped[AssetViewAngle] = mapped_column(
        enum_column(AssetViewAngle, length=16),
        nullable=False,
        default=AssetViewAngle.front,
        comment="視角",
    )
    is_primary: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        comment="是否為主要參考圖（1 是 0 否）；生成時優先採用",
    )
    prompt: Mapped[str] = mapped_column(Text, nullable=False, default="", comment="產生此圖所用的提示詞")

    __table_args__ = (
        Index("ix_studio_asset_images_asset", "asset_kind", "asset_id"),
        Index("ix_studio_asset_images_asset_angle", "asset_kind", "asset_id", "view_angle"),
    )


class ShotAssetLink(Base, TimestampMixin):
    """分鏡引用資產的關聯。

    同樣採多型設計：一張表涵蓋角色／場景／道具／服裝的引用關係，
    讓「這個鏡頭用到哪些資產」與「這個資產出現在哪些鏡頭」都是單表查詢。
    """

    __tablename__ = "studio_shot_asset_links"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, comment="關聯行 ID")
    shot_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("studio_shots.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="分鏡 ID",
    )
    asset_kind: Mapped[AssetKind] = mapped_column(enum_column(AssetKind, length=16), nullable=False, comment="資產類型")
    asset_id: Mapped[str] = mapped_column(String(64), nullable=False, comment="資產 ID（多型，無外鍵）")
    index: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        comment="出場順序；角色用於決定提示詞中的主次",
    )
    note: Mapped[str] = mapped_column(Text, nullable=False, default="", comment="此鏡頭中的特殊說明")

    __table_args__ = (
        UniqueConstraint("shot_id", "asset_kind", "asset_id", name="uq_studio_shot_asset_links_shot_asset"),
        Index("ix_studio_shot_asset_links_asset", "asset_kind", "asset_id"),
    )
