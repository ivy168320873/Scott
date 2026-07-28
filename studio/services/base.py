"""Service 層共用工具。

職責邊界：
- Service 擁有**業務規則**：狀態流轉、跨資源驗證、序號指派、統計更新。
- Service 不碰 HTTP：不看 Request、不回 Response、不決定狀態碼
  （狀態碼由 `studio.core.errors` 的錯誤型別決定）。
"""

from __future__ import annotations

from typing import Any, TypeVar

from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError

from studio.core.errors import ConflictError, NotFoundError

T = TypeVar("T")


def ensure_found(entity: T | None, *, resource: str, entity_id: Any) -> T:
    """確認資源存在，否則拋出 404。

    集中處理讓每個 route 不必重複寫 `if not x: raise ...`，
    也保證錯誤訊息格式一致。
    """

    if entity is None:
        raise NotFoundError(f"{resource} 不存在：{entity_id}", details={"resource": resource, "id": str(entity_id)})
    return entity


def apply_updates(entity: Any, payload: BaseModel, *, allowed: set[str] | None = None) -> Any:
    """把 Pydantic 更新 payload 套用到 ORM 物件。

    使用 `exclude_unset=True`：**未傳入的欄位保留原值**。
    這是 PATCH 語義的關鍵 —— 若改用 `exclude_none`，前端就無法把欄位設為 null。

    Args:
        entity: 目標 ORM 物件。
        payload: 更新用的 Pydantic 模型。
        allowed: 若提供，只有名單內的欄位會被套用（用於保護不可直接寫入的欄位）。

    Returns:
        同一個 entity（便於鏈式使用）。
    """

    data = payload.model_dump(exclude_unset=True)
    for field, value in data.items():
        if allowed is not None and field not in allowed:
            continue
        if hasattr(entity, field):
            setattr(entity, field, value)
    return entity


def translate_integrity_error(exc: IntegrityError, *, resource: str) -> ConflictError:
    """把資料庫完整性錯誤翻譯成語意明確的 409。

    直接把 SQLAlchemy 的原始訊息回給前端會洩漏資料表與約束名稱，
    也無法在地化；這裡統一轉成可讀訊息。
    """

    message = str(getattr(exc, "orig", exc)).lower()

    if "unique" in message or "duplicate" in message:
        return ConflictError(f"{resource} 已存在或違反唯一性限制", details={"resource": resource})
    if "foreign key" in message:
        return ConflictError(f"{resource} 參照了不存在的資源", details={"resource": resource})
    return ConflictError(f"{resource} 違反資料完整性限制", details={"resource": resource})
