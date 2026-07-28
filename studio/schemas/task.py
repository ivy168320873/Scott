"""生成任務 schema。

任務對外表示刻意包含 `elapsed_seconds` 與 `is_cancellable` 兩個衍生欄位：
前端若自行計算，會因時區與時鐘偏差產生不一致的顯示，
且「能不能取消」的判斷規則必須只有一份。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from studio.models.types import TaskDeliveryMode, TaskKind, TaskLinkStatus, TaskStatus


class TaskCreate(BaseModel):
    """建立生成任務。

    Phase 3 只負責建立與查詢任務紀錄；實際排程與執行於 Phase 4 接上。
    """

    task_kind: TaskKind = Field(description="業務任務類型")
    mode: TaskDeliveryMode = Field(default=TaskDeliveryMode.async_polling, description="交付方式")
    payload: dict[str, Any] = Field(default_factory=dict, description="請求參數")
    project_id: str | None = Field(default=None, description="關聯專案 ID")
    chapter_id: str | None = Field(default=None, description="關聯章節 ID")
    shot_id: str | None = Field(default=None, description="關聯分鏡 ID")
    provider_id: str | None = Field(default=None, description="指定供應商 ID")
    model_id: str | None = Field(default=None, description="指定模型 ID")
    max_retries: int = Field(default=0, ge=0, le=10, description="最大重試次數")


class TaskCancel(BaseModel):
    """請求取消任務。"""

    reason: str = Field(default="", max_length=255, description="取消原因")


class TaskRead(BaseModel):
    """任務對外表示。"""

    model_config = ConfigDict(from_attributes=True, protected_namespaces=())

    id: str = Field(description="任務 ID")
    task_kind: str = Field(description="業務任務類型")
    mode: TaskDeliveryMode = Field(description="交付方式")
    status: TaskStatus = Field(description="任務狀態")
    progress: int = Field(description="進度 0-100")
    progress_message: str = Field(description="目前步驟描述")
    payload: dict[str, Any] = Field(description="請求參數")
    result: dict[str, Any] | None = Field(description="執行結果")
    error: str = Field(description="失敗原因")
    error_code: str = Field(description="標準化錯誤碼")
    project_id: str | None = Field(description="關聯專案 ID")
    chapter_id: str | None = Field(description="關聯章節 ID")
    shot_id: str | None = Field(description="關聯分鏡 ID")
    provider_id: str | None = Field(description="供應商 ID")
    model_id: str | None = Field(description="模型 ID")
    cancel_requested: bool = Field(description="是否已請求取消")
    cancel_reason: str = Field(description="取消原因")
    started_at: datetime | None = Field(description="開始執行時間")
    finished_at: datetime | None = Field(description="結束時間")
    elapsed_seconds: float | None = Field(description="耗時（秒）；未開始為 null")
    is_cancellable: bool = Field(description="目前是否可取消")
    retry_count: int = Field(description="已重試次數")
    max_retries: int = Field(description="最大重試次數")
    created_at: datetime = Field(description="建立時間")
    updated_at: datetime = Field(description="更新時間")


class TaskLinkCreate(BaseModel):
    """建立任務產物關聯。"""

    resource_type: str = Field(default="", max_length=32, description="產出資源類型")
    relation_type: str = Field(default="", max_length=32, description="業務類型")
    relation_entity_id: str = Field(default="", max_length=64, description="業務實體 ID")
    file_id: str | None = Field(default=None, description="產出檔案 ID")


class TaskLinkUpdate(BaseModel):
    """更新任務產物的採用狀態。"""

    status: TaskLinkStatus = Field(description="採用狀態")


class TaskLinkRead(BaseModel):
    """任務產物關聯對外表示。"""

    model_config = ConfigDict(from_attributes=True)

    id: int = Field(description="關聯行 ID")
    task_id: str = Field(description="任務 ID")
    resource_type: str = Field(description="產出資源類型")
    relation_type: str = Field(description="業務類型")
    relation_entity_id: str = Field(description="業務實體 ID")
    file_id: str | None = Field(description="產出檔案 ID")
    status: TaskLinkStatus = Field(description="採用狀態")
    created_at: datetime = Field(description="建立時間")
    updated_at: datetime = Field(description="更新時間")


__all__ = [
    "TaskCreate",
    "TaskCancel",
    "TaskRead",
    "TaskLinkCreate",
    "TaskLinkUpdate",
    "TaskLinkRead",
]
