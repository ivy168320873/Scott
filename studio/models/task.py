"""統一生成任務模型。

所有長時間工作（腳本分析、圖片生成、影片生成）都走這一張表，因此：
- 任務狀態持久化於資料庫，process 重啟後仍可查詢與恢復。
- 前端只需要一套輪詢與呈現邏輯，就能涵蓋所有任務類型。
- 取消、重試、耗時統計有統一語意。

`GenerationTaskLink` 以獨立表記錄任務與業務實體的關聯，讓 `GenerationTask`
維持與業務無關的通用性；新增業務類型不需要修改任務表結構。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

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
from studio.models.types import TaskDeliveryMode, TaskLinkStatus, TaskStatus


class GenerationTask(Base, TimestampMixin):
    """生成任務。

    設計要點：
    - `payload` / `result` 用 JSON，讓不同任務類型共用同一張表而不需為每種
      任務新增欄位。
    - `progress` 為 0-100 整數，前端可直接呈現，不需換算。
    - 取消採「請求 + 確認」兩段式：API 只設定 `cancel_requested`，實際中止
      由執行器在安全點偵測後完成並寫入 `cancelled_at`。這避免了強制終止
      導致產物寫入一半。
    """

    __tablename__ = "studio_generation_tasks"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, comment="任務 ID")
    task_kind: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        index=True,
        comment="業務任務類型，用於執行器路由",
    )
    mode: Mapped[TaskDeliveryMode] = mapped_column(
        enum_column(TaskDeliveryMode, length=32),
        nullable=False,
        default=TaskDeliveryMode.async_polling,
        comment="交付方式",
    )
    status: Mapped[TaskStatus] = mapped_column(
        enum_column(TaskStatus, length=16),
        nullable=False,
        default=TaskStatus.pending,
        index=True,
        comment="任務狀態",
    )
    progress: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="進度 0-100")
    progress_message: Mapped[str] = mapped_column(
        String(512),
        nullable=False,
        default="",
        comment="目前進行中的步驟描述",
    )

    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict, comment="請求參數（JSON）")
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True, comment="執行結果（JSON）")
    error: Mapped[str] = mapped_column(Text, nullable=False, default="", comment="失敗原因；為空表示無錯誤")
    error_code: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default="",
        comment="標準化錯誤碼，供前端分支處理",
    )

    # ── 業務關聯（冗餘欄位，用於任務中心回跳與篩選）─────────────────────────
    project_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("studio_projects.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
        comment="關聯專案 ID；用於任務中心篩選與回跳",
    )
    chapter_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("studio_chapters.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
        comment="關聯章節 ID",
    )
    shot_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("studio_shots.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
        comment="關聯分鏡 ID",
    )

    # ── 供應商 ────────────────────────────────────────────────────────────────
    provider_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("studio_providers.id", ondelete="SET NULL"),
        nullable=True,
        comment="執行此任務的供應商 ID",
    )
    model_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("studio_models.id", ondelete="SET NULL"),
        nullable=True,
        comment="執行此任務的模型 ID",
    )

    # ── 取消 ──────────────────────────────────────────────────────────────────
    cancel_requested: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        comment="是否已請求取消；執行器在安全點偵測此旗標",
    )
    cancel_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="請求取消時間",
    )
    cancel_reason: Mapped[str] = mapped_column(String(255), nullable=False, default="", comment="取消原因")
    cancelled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="實際完成取消的時間",
    )

    # ── 執行 ──────────────────────────────────────────────────────────────────
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="開始執行時間",
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="結束時間（成功／失敗／取消）",
    )
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="已重試次數")
    max_retries: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="最大重試次數")
    executor_type: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="",
        comment="執行器類型（celery / inline）",
    )
    executor_task_id: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
        default="",
        comment="執行器側任務 ID，如 Celery task id",
    )

    links: Mapped[list[GenerationTaskLink]] = relationship(
        back_populates="task",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    __table_args__ = (
        # 任務中心以「狀態 + 最近更新」為主要查詢模式。
        Index("ix_studio_tasks_status_updated", "status", "updated_at"),
        Index("ix_studio_tasks_kind_status", "task_kind", "status"),
        # 恢復流程需快速找出「仍在執行中」與「已請求取消」的任務。
        Index("ix_studio_tasks_status_cancel", "status", "cancel_requested"),
        Index("ix_studio_tasks_project_updated", "project_id", "updated_at"),
    )

    @property
    def elapsed_seconds(self) -> float | None:
        """任務耗時（秒）。

        Returns:
            已結束的任務回傳實際耗時；執行中的任務回傳至今經過的時間；
            尚未開始則回傳 None。
        """

        if self.started_at is None:
            return None
        end = self.finished_at or datetime.now(self.started_at.tzinfo)
        return max(0.0, (end - self.started_at).total_seconds())

    @property
    def elapsed_ms(self) -> int | None:
        """任務耗時（毫秒）。前端以毫秒顯示較精確。"""

        seconds = self.elapsed_seconds
        return None if seconds is None else int(seconds * 1000)

    @property
    def is_cancellable(self) -> bool:
        """任務目前是否可被取消（僅非終態任務可取消）。"""

        return self.status.is_active and not self.cancel_requested


class GenerationTaskLink(Base, TimestampMixin):
    """任務與業務實體／產物的關聯。

    一個任務可能產出多個結果（例如一次生成 4 張候選圖），每個結果都需要
    獨立的採用狀態，因此關聯而非任務本身承載 `status`。
    """

    __tablename__ = "studio_generation_task_links"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, comment="關聯行 ID")
    task_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("studio_generation_tasks.id", ondelete="CASCADE"),
        nullable=False,
        comment="任務 ID",
    )
    resource_type: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="",
        comment="產出資源類型（image / video / text）",
    )
    relation_type: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="",
        comment="業務類型（shot_frame / character / prop 等）",
    )
    relation_entity_id: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default="",
        comment="業務實體 ID（多型，無外鍵）",
    )
    file_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("studio_files.id", ondelete="SET NULL"),
        nullable=True,
        comment="產出檔案 ID",
    )
    status: Mapped[TaskLinkStatus] = mapped_column(
        enum_column(TaskLinkStatus, length=16),
        nullable=False,
        default=TaskLinkStatus.todo,
        comment="採用狀態",
    )

    task: Mapped[GenerationTask] = relationship(back_populates="links")

    __table_args__ = (
        UniqueConstraint(
            "task_id",
            "resource_type",
            "relation_type",
            "relation_entity_id",
            "file_id",
            name="uq_studio_task_links_task_resource_entity_file",
        ),
        Index("ix_studio_task_links_relation", "relation_type", "relation_entity_id"),
        Index("ix_studio_task_links_status_updated", "status", "updated_at"),
        Index("ix_studio_task_links_file", "file_id"),
    )
