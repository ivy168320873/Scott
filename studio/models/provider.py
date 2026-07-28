"""供應商、模型與提示詞模板。

安全設計（重要）：
`Provider` **不存放 API 金鑰**，只存放金鑰所在的環境變數名稱
（`api_key_env`）。實際金鑰在執行期由 `studio.config.resolve_secret()`
從環境變數讀取。因此：
- 資料庫備份外洩不會連帶洩漏金鑰。
- API 回應可安全回傳供應商設定，只需附帶「金鑰是否就緒」旗標。
- 金鑰輪換不需要改資料。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import JSON, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from studio.core.db import Base, TimestampMixin, enum_column
from studio.models.types import ModelCategory, PromptCategory, ProviderKind, ProviderStatus


class Provider(Base, TimestampMixin):
    """模型供應商設定。"""

    __tablename__ = "studio_providers"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, comment="供應商 ID")
    name: Mapped[str] = mapped_column(String(255), nullable=False, comment="供應商顯示名稱")
    kind: Mapped[ProviderKind] = mapped_column(
        enum_column(ProviderKind, length=32),
        nullable=False,
        comment="協定類型，決定使用哪個 Adapter",
    )
    api_key_env: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
        default="",
        comment="API 金鑰所在的環境變數名稱；此處絕不存放金鑰值本身",
    )
    base_url: Mapped[str] = mapped_column(String(1024), nullable=False, default="", comment="文字 API base URL")
    image_base_url: Mapped[str] = mapped_column(
        String(1024),
        nullable=False,
        default="",
        comment="圖片 API base URL；留空則沿用 base_url",
    )
    video_base_url: Mapped[str] = mapped_column(
        String(1024),
        nullable=False,
        default="",
        comment="影片 API base URL；留空則沿用 base_url",
    )
    status: Mapped[ProviderStatus] = mapped_column(
        enum_column(ProviderStatus, length=16),
        nullable=False,
        default=ProviderStatus.testing,
        comment="啟用狀態",
    )
    description: Mapped[str] = mapped_column(Text, nullable=False, default="", comment="說明")
    timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=120, comment="請求逾時（秒）")
    max_retries: Mapped[int] = mapped_column(Integer, nullable=False, default=2, comment="失敗重試次數")
    extra_config: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
        comment="供應商專屬設定（JSON），如 region、api_version",
    )

    models: Mapped[list[Model]] = relationship(
        back_populates="provider",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    __table_args__ = (
        UniqueConstraint("name", name="uq_studio_providers_name"),
        Index("ix_studio_providers_status", "status"),
        Index("ix_studio_providers_kind", "kind"),
    )


class Model(Base, TimestampMixin):
    """具體模型設定。"""

    __tablename__ = "studio_models"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, comment="模型設定 ID")
    provider_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("studio_providers.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="所屬供應商 ID",
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False, comment="顯示名稱")
    model_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="供應商端的實際模型識別碼，例如 claude-opus-5",
    )
    category: Mapped[ModelCategory] = mapped_column(
        enum_column(ModelCategory, length=16),
        nullable=False,
        index=True,
        comment="模型類別",
    )
    enabled: Mapped[bool] = mapped_column(
        # 以 Integer 而非 Boolean 保存，確保 SQLite 與 PostgreSQL 行為一致。
        Integer,
        nullable=False,
        default=1,
        comment="是否啟用（1 是 0 否）",
    )
    params: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
        comment="預設呼叫參數（JSON），如 temperature、max_tokens、size",
    )
    description: Mapped[str] = mapped_column(Text, nullable=False, default="", comment="說明")

    provider: Mapped[Provider] = relationship(back_populates="models")

    __table_args__ = (
        UniqueConstraint("provider_id", "model_id", "category", name="uq_studio_models_provider_model_category"),
        Index("ix_studio_models_category_enabled", "category", "enabled"),
    )


class ModelSettings(Base, TimestampMixin):
    """全域模型預設（單例表，固定 id=1）。

    外鍵使用 `SET NULL`：刪除某個模型不應讓整張設定表無法更新，
    只需把對應的預設清空並由使用者重新指定。
    """

    __tablename__ = "studio_model_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True, comment="設定行 ID（固定為 1）")
    default_text_model_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("studio_models.id", ondelete="SET NULL"),
        nullable=True,
        comment="預設文字模型 ID",
    )
    default_image_model_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("studio_models.id", ondelete="SET NULL"),
        nullable=True,
        comment="預設圖片模型 ID",
    )
    default_video_model_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("studio_models.id", ondelete="SET NULL"),
        nullable=True,
        comment="預設影片模型 ID",
    )
    task_defaults: Mapped[dict[str, Any]] = mapped_column(
        JSON,
        nullable=False,
        default=dict,
        comment="各任務類型的預設模型覆蓋（JSON：task_kind -> model_id）",
    )


class PromptTemplate(Base, TimestampMixin):
    """提示詞模板。

    模板以 `{變數}` 佔位；service 層在組提示詞時填入鏡頭、資產與專案風格，
    讓提示詞策略可由使用者調整而不需改程式。
    """

    __tablename__ = "studio_prompt_templates"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, comment="模板 ID")
    category: Mapped[PromptCategory] = mapped_column(
        enum_column(PromptCategory, length=32),
        nullable=False,
        index=True,
        comment="模板類別",
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False, comment="模板名稱")
    content: Mapped[str] = mapped_column(Text, nullable=False, default="", comment="模板內容，含 {變數} 佔位")
    description: Mapped[str] = mapped_column(Text, nullable=False, default="", comment="說明")
    is_default: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
        comment="是否為該類別的預設模板（1 是 0 否）",
    )
    project_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey("studio_projects.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
        comment="所屬專案 ID；為空表示全域模板",
    )
    variables: Mapped[list[str]] = mapped_column(
        JSON,
        nullable=False,
        default=list,
        comment="模板可用變數名稱清單（JSON），供前端提示",
    )

    __table_args__ = (
        Index("ix_studio_prompt_templates_category_default", "category", "is_default"),
        Index("ix_studio_prompt_templates_project_category", "project_id", "category"),
    )
