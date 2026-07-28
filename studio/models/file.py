"""檔案與用途追蹤模型。

`FileItem` 是所有二進位資產（上傳與生成產物）的唯一登記處；業務表一律
以 `file_id` 引用，不直接存 URL —— 這樣更換儲存後端或 CDN 時不需要改資料。

`FileUsage` 記錄「這個檔案被誰用在哪裡」，用途有二：
1. 媒體庫可顯示某張圖被哪些鏡頭引用，避免誤刪仍在使用的素材。
2. 清理未引用的孤兒檔案時有明確依據。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import JSON, BigInteger, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from studio.core.db import Base, TimestampMixin, enum_column
from studio.models.types import FileType, FileUsageKind


class FileItem(Base, TimestampMixin):
    """檔案登記。

    `storage_key` 為物件儲存中的 key；公開 URL 由 `ObjectStorage.public_url()`
    於執行期組出，不落地保存。
    """

    __tablename__ = "studio_files"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, comment="檔案 ID")
    file_type: Mapped[FileType] = mapped_column(
        enum_column(FileType, length=16),
        nullable=False,
        index=True,
        comment="檔案類型",
    )
    storage_key: Mapped[str] = mapped_column(
        String(1024),
        nullable=False,
        comment="物件儲存 key；公開 URL 於執行期組出，不存於資料庫",
    )
    filename: Mapped[str] = mapped_column(String(512), nullable=False, default="", comment="原始檔名")
    content_type: Mapped[str] = mapped_column(String(128), nullable=False, default="", comment="MIME type")
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0, comment="檔案大小（bytes）")
    width: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="影像寬度（px）；非影像為 0")
    height: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="影像高度（px）；非影像為 0")
    duration_seconds: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        comment="影音長度（秒）；靜態圖為 0",
    )
    project_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("studio_projects.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
        comment="所屬專案 ID；刪除專案時一併清除其素材",
    )
    source_task_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("studio_generation_tasks.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
        comment="產生此檔案的任務 ID；上傳檔案為空",
    )
    file_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
        comment="額外中繼資料（JSON），如生成參數、種子",
    )

    __table_args__ = (
        Index("ix_studio_files_project_type", "project_id", "file_type"),
        Index("ix_studio_files_created_at", "created_at"),
    )


class FileUsage(Base, TimestampMixin):
    """檔案用途記錄。

    `owner_type` + `owner_id` 為多型引用（分鏡、資產、專案等），
    因此不設外鍵；由 service 層在建立引用時保證目標存在。
    """

    __tablename__ = "studio_file_usages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, comment="用途記錄 ID")
    file_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("studio_files.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="檔案 ID",
    )
    usage_kind: Mapped[FileUsageKind] = mapped_column(
        enum_column(FileUsageKind, length=32),
        nullable=False,
        comment="用途類型",
    )
    owner_type: Mapped[str] = mapped_column(String(32), nullable=False, default="", comment="擁有者類型（多型）")
    owner_id: Mapped[str] = mapped_column(String(64), nullable=False, default="", comment="擁有者 ID（多型）")
    note: Mapped[str] = mapped_column(Text, nullable=False, default="", comment="補充說明")

    __table_args__ = (
        Index("ix_studio_file_usages_owner", "owner_type", "owner_id"),
        Index("ix_studio_file_usages_file_kind", "file_id", "usage_kind"),
    )
