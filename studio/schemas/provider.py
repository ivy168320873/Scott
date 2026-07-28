"""供應商、模型與提示詞模板 schema。

**安全關鍵檔案。**

`ProviderRead` 只允許回傳下列欄位，任何金鑰內容都不得出現：
`id`、`name`、`provider_type`、`base_url`、`enabled`、`api_key_configured`、
`created_at`、`updated_at`（外加非敏感的營運欄位）。

金鑰本身從不進入資料庫 —— `Provider.api_key_env` 只存放環境變數名稱。
`api_key_configured` 由 `secret_is_configured()` 計算，只透露「有沒有設定」，
不透露值。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from studio.models.types import ModelCategory, PromptCategory, ProviderKind, ProviderStatus


class ProviderCreate(BaseModel):
    """建立供應商。"""

    name: str = Field(min_length=1, max_length=255, description="供應商顯示名稱")
    provider_type: ProviderKind = Field(description="協定類型，決定使用哪個 Adapter")
    api_key_env: str = Field(
        default="",
        max_length=128,
        description="API 金鑰所在的**環境變數名稱**；不要填入金鑰本身",
    )
    base_url: str = Field(default="", max_length=1024, description="文字 API base URL")
    image_base_url: str = Field(default="", max_length=1024, description="圖片 API base URL")
    video_base_url: str = Field(default="", max_length=1024, description="影片 API base URL")
    enabled: bool = Field(default=False, description="是否啟用")
    description: str = Field(default="", description="說明")
    timeout_seconds: int = Field(default=120, ge=1, le=3600, description="請求逾時（秒）")
    max_retries: int = Field(default=2, ge=0, le=10, description="失敗重試次數")
    extra_config: dict[str, Any] = Field(default_factory=dict, description="供應商專屬設定")

    @field_validator("api_key_env")
    @classmethod
    def _reject_key_material(cls, value: str) -> str:
        """擋下誤把金鑰本身填進來的情況。

        環境變數名稱不會含空白，也幾乎不會超過 128 字元或以 `sk-` 開頭；
        這些特徵幾乎必然代表使用者貼上了真正的金鑰。直接拒絕，
        避免金鑰被寫入資料庫。
        """

        cleaned = value.strip()
        if not cleaned:
            return cleaned
        looks_like_secret = (
            " " in cleaned
            or cleaned.lower().startswith(("sk-", "sk_", "xoxb-", "ghp_", "bearer "))
            or len(cleaned) > 128
        )
        if looks_like_secret:
            raise ValueError("api_key_env 必須是環境變數『名稱』（例如 ANTHROPIC_API_KEY），不可填入金鑰本身")
        return cleaned


class ProviderUpdate(BaseModel):
    """更新供應商。

    所有欄位皆為選填：**未傳入的欄位保留原值**。
    特別是 `api_key_env` —— 只有明確傳入新值才會更新，
    因此前端送出表單時不需要（也不應該）回填既有設定。
    """

    name: str | None = Field(default=None, min_length=1, max_length=255)
    provider_type: ProviderKind | None = None
    api_key_env: str | None = Field(
        default=None,
        max_length=128,
        description="留空或不傳表示保留原設定",
    )
    base_url: str | None = Field(default=None, max_length=1024)
    image_base_url: str | None = Field(default=None, max_length=1024)
    video_base_url: str | None = Field(default=None, max_length=1024)
    enabled: bool | None = None
    description: str | None = None
    timeout_seconds: int | None = Field(default=None, ge=1, le=3600)
    max_retries: int | None = Field(default=None, ge=0, le=10)
    extra_config: dict[str, Any] | None = None

    _reject_key_material = field_validator("api_key_env")(ProviderCreate._reject_key_material.__func__)  # type: ignore[attr-defined]


class ProviderRead(BaseModel):
    """供應商對外表示。

    刻意 **不含** `api_key`、`api_secret`、`token` 等任何金鑰欄位。
    `api_key_env` 只是環境變數名稱，不是機密；
    `api_key_configured` 只表示該變數是否已設定。
    """

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(description="供應商 ID")
    name: str = Field(description="供應商名稱")
    provider_type: ProviderKind = Field(description="協定類型")
    base_url: str = Field(description="文字 API base URL")
    image_base_url: str = Field(description="圖片 API base URL")
    video_base_url: str = Field(description="影片 API base URL")
    enabled: bool = Field(description="是否啟用")
    api_key_env: str = Field(description="金鑰所在的環境變數名稱（非機密）")
    api_key_configured: bool = Field(description="該環境變數目前是否已設定；不透露金鑰內容")
    description: str = Field(description="說明")
    timeout_seconds: int = Field(description="請求逾時（秒）")
    max_retries: int = Field(description="失敗重試次數")
    created_at: datetime = Field(description="建立時間")
    updated_at: datetime = Field(description="更新時間")


class ProviderTestResult(BaseModel):
    """供應商連線測試結果。"""

    provider_id: str = Field(description="供應商 ID")
    ok: bool = Field(description="設定是否就緒")
    detail: str = Field(description="說明；失敗時指出缺少什麼，但不含金鑰內容")


# ── 模型 ──────────────────────────────────────────────────────────────────────


class ModelCreate(BaseModel):
    """建立模型設定。"""

    provider_id: str = Field(min_length=1, max_length=64, description="所屬供應商 ID")
    name: str = Field(min_length=1, max_length=255, description="顯示名稱")
    model_id: str = Field(min_length=1, max_length=255, description="供應商端的實際模型識別碼")
    category: ModelCategory = Field(description="模型類別")
    enabled: bool = Field(default=True, description="是否啟用")
    params: dict[str, Any] = Field(default_factory=dict, description="預設呼叫參數")
    description: str = Field(default="", description="說明")


class ModelUpdate(BaseModel):
    """更新模型設定；未傳入的欄位保留原值。"""

    name: str | None = Field(default=None, min_length=1, max_length=255)
    model_id: str | None = Field(default=None, min_length=1, max_length=255)
    category: ModelCategory | None = None
    enabled: bool | None = None
    params: dict[str, Any] | None = None
    description: str | None = None


class ModelRead(BaseModel):
    """模型設定對外表示。"""

    model_config = ConfigDict(from_attributes=True, protected_namespaces=())

    id: str = Field(description="模型設定 ID")
    provider_id: str = Field(description="所屬供應商 ID")
    provider_name: str = Field(default="", description="供應商名稱（便於前端顯示）")
    name: str = Field(description="顯示名稱")
    model_id: str = Field(description="供應商端模型識別碼")
    category: ModelCategory = Field(description="模型類別")
    enabled: bool = Field(description="是否啟用")
    params: dict[str, Any] = Field(description="預設呼叫參數")
    description: str = Field(description="說明")
    created_at: datetime = Field(description="建立時間")
    updated_at: datetime = Field(description="更新時間")


# ── 提示詞模板 ────────────────────────────────────────────────────────────────


class PromptTemplateCreate(BaseModel):
    """建立提示詞模板。"""

    category: PromptCategory = Field(description="模板類別")
    name: str = Field(min_length=1, max_length=255, description="模板名稱")
    content: str = Field(default="", description="模板內容，含 {變數} 佔位")
    description: str = Field(default="", description="說明")
    is_default: bool = Field(default=False, description="是否為該類別的預設模板")
    project_id: str | None = Field(default=None, description="所屬專案；留空為全域模板")
    variables: list[str] = Field(default_factory=list, description="可用變數名稱")


class PromptTemplateUpdate(BaseModel):
    """更新提示詞模板；未傳入的欄位保留原值。"""

    category: PromptCategory | None = None
    name: str | None = Field(default=None, min_length=1, max_length=255)
    content: str | None = None
    description: str | None = None
    is_default: bool | None = None
    variables: list[str] | None = None


class PromptTemplateRead(BaseModel):
    """提示詞模板對外表示。"""

    model_config = ConfigDict(from_attributes=True)

    id: str = Field(description="模板 ID")
    category: PromptCategory = Field(description="模板類別")
    name: str = Field(description="模板名稱")
    content: str = Field(description="模板內容")
    description: str = Field(description="說明")
    is_default: bool = Field(description="是否為預設模板")
    project_id: str | None = Field(description="所屬專案 ID；null 為全域")
    variables: list[str] = Field(description="可用變數名稱")
    created_at: datetime = Field(description="建立時間")
    updated_at: datetime = Field(description="更新時間")


__all__ = [
    "ProviderCreate",
    "ProviderUpdate",
    "ProviderRead",
    "ProviderTestResult",
    "ProviderStatus",
    "ModelCreate",
    "ModelUpdate",
    "ModelRead",
    "PromptTemplateCreate",
    "PromptTemplateUpdate",
    "PromptTemplateRead",
]
