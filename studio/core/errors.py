"""Studio 統一錯誤型別與標準化。

目的：
- Service 層拋出語意明確的錯誤，API 層不需要知道底層細節就能轉成正確 HTTP 狀態。
- 供應商（OpenAI / Anthropic / Gemini / …）各自的例外在 integrations 層被翻譯成
  `ProviderError`，業務邏輯因此不會耦合任何特定 SDK 的例外型別。
"""

from __future__ import annotations

from typing import Any


class StudioError(Exception):
    """所有 Studio 錯誤的基底。

    Attributes:
        message: 給人看的錯誤訊息。
        code: 穩定的機器可讀錯誤碼，前端可據此做在地化或分支處理。
        status_code: 對應的 HTTP 狀態碼。
        details: 額外結構化資訊（欄位錯誤、供應商回應摘要等）。
    """

    code: str = "studio_error"
    status_code: int = 500

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        status_code: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        if status_code is not None:
            self.status_code = status_code
        self.details = details or {}

    def to_payload(self) -> dict[str, Any]:
        """轉為 API 錯誤回應的 body。"""

        payload: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.details:
            payload["details"] = self.details
        return payload


class NotFoundError(StudioError):
    """請求的資源不存在。"""

    code = "not_found"
    status_code = 404


class ValidationError(StudioError):
    """輸入不合法（欄位層級驗證由 FastAPI 處理，此處為業務規則驗證）。"""

    code = "validation_error"
    status_code = 422


class ConflictError(StudioError):
    """與現有資料衝突，例如唯一鍵重複、狀態不允許此操作。"""

    code = "conflict"
    status_code = 409


class StateTransitionError(ConflictError):
    """不合法的狀態流轉，例如對已完成的任務再次取消。"""

    code = "invalid_state_transition"


class ConfigurationError(StudioError):
    """設定缺失或錯誤，例如供應商未設定 API 金鑰環境變數。"""

    code = "configuration_error"
    status_code = 503


class ProviderError(StudioError):
    """供應商呼叫失敗的標準化錯誤。

    integrations 層必須把所有供應商 SDK 例外翻譯成本型別，
    讓 service 與 task 層有一致的錯誤處理方式。
    """

    code = "provider_error"
    status_code = 502

    def __init__(
        self,
        message: str,
        *,
        provider: str = "",
        retryable: bool = False,
        status_code: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        merged: dict[str, Any] = {"provider": provider, "retryable": retryable}
        merged.update(details or {})
        super().__init__(message, status_code=status_code, details=merged)
        self.provider = provider
        self.retryable = retryable


class ProviderTimeoutError(ProviderError):
    """供應商呼叫逾時（預設視為可重試）。"""

    code = "provider_timeout"
    status_code = 504

    def __init__(self, message: str, *, provider: str = "", details: dict[str, Any] | None = None) -> None:
        super().__init__(message, provider=provider, retryable=True, details=details)


class ProviderRateLimitError(ProviderError):
    """供應商限流（可重試）。"""

    code = "provider_rate_limited"
    status_code = 429

    def __init__(self, message: str, *, provider: str = "", details: dict[str, Any] | None = None) -> None:
        super().__init__(message, provider=provider, retryable=True, details=details)


class TaskCancelledError(StudioError):
    """任務在執行過程中被要求取消。

    executor 偵測到 `cancel_requested` 時拋出，由任務框架轉為 `cancelled` 狀態，
    不視為失敗。
    """

    code = "task_cancelled"
    status_code = 409
