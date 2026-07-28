"""Scott Studio 設定層。

所有設定一律由環境變數注入，程式碼中不得出現任何金鑰、密碼或 Token。
供應商 API 金鑰不存於此，也不存於資料庫；資料庫只保存「環境變數名稱」
（見 `studio.models.provider.Provider.api_key_env`），實際值由本模組的
`resolve_secret()` 於執行期讀取。

設計取捨：
- 使用 pydantic-settings 讓型別轉換與驗證集中在一處，避免各模組各自 `os.environ.get`。
- 提供 dev fallback（SQLite / 本機檔案儲存 / inline 任務），讓開發者不必先起
  PostgreSQL、Redis、MinIO 就能跑起來；production 再切換為完整組態。
"""

from __future__ import annotations

import os
from enum import Enum
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class StorageBackend(str, Enum):
    """物件儲存後端。

    - `s3`：S3 相容服務（MinIO / AWS S3 / RustFS 等），production 預設。
    - `local`：寫入本機目錄，供開發環境在沒有 S3 服務時使用。
    """

    s3 = "s3"
    local = "local"


class TaskExecutionMode(str, Enum):
    """任務執行模式。

    - `celery`：送往 Celery worker，長時間生成任務不佔用 web request。
    - `inline`：在呼叫端的背景執行緒直接執行，供單機／測試環境使用。

    注意：`inline` 僅為降級方案。它仍會把任務狀態寫入資料庫（因此仍可查詢、
    取消與恢復），但不具備跨 process 擴展能力。
    """

    celery = "celery"
    inline = "inline"


class Settings(BaseSettings):
    """Studio 執行期設定。"""

    model_config = SettingsConfigDict(
        env_prefix="STUDIO_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ── 應用 ──────────────────────────────────────────────────────────────────
    app_name: str = Field(default="Scott Studio", description="平台顯示名稱")
    api_v1_prefix: str = Field(default="/api/v1/studio", description="Studio API 前綴")
    debug: bool = Field(default=False, description="是否開啟除錯模式")
    cors_origins: str = Field(
        default="http://localhost:5173,http://127.0.0.1:5173",
        description="允許的前端來源，逗號分隔",
    )

    # ── 資料庫 ────────────────────────────────────────────────────────────────
    database_url: str = Field(
        default="sqlite+aiosqlite:///./studio.db",
        description="SQLAlchemy async 連線字串；production 應指向 PostgreSQL",
    )
    db_echo: bool = Field(default=False, description="是否輸出 SQL log")
    db_pool_size: int = Field(default=10, ge=1, description="連線池大小（SQLite 忽略）")
    db_max_overflow: int = Field(default=20, ge=0, description="連線池溢位上限（SQLite 忽略）")

    # ── Redis ─────────────────────────────────────────────────────────────────
    redis_url: str = Field(default="redis://localhost:6379/0", description="Redis 連線字串")

    # ── 任務 ──────────────────────────────────────────────────────────────────
    task_execution_mode: TaskExecutionMode = Field(
        default=TaskExecutionMode.inline,
        description="任務執行模式：celery / inline",
    )
    celery_broker_url: str = Field(default="", description="Celery broker；留空則沿用 redis_url")
    celery_result_backend: str = Field(default="", description="Celery result backend；留空則沿用 redis_url")
    task_default_timeout: int = Field(default=1800, ge=1, description="任務預設逾時（秒）")
    task_max_retries: int = Field(default=2, ge=0, description="任務預設重試次數")

    # ── 物件儲存 ──────────────────────────────────────────────────────────────
    storage_backend: StorageBackend = Field(
        default=StorageBackend.local,
        description="物件儲存後端：s3 / local",
    )
    storage_local_dir: str = Field(default="./studio_storage", description="local 後端寫入目錄")
    storage_public_base_url: str = Field(
        default="",
        description="對外可讀取的資產 URL 前綴；留空則由 API 代為串流",
    )
    s3_endpoint_url: str = Field(default="", description="S3 相容服務 endpoint")
    s3_region_name: str = Field(default="us-east-1", description="S3 region")
    s3_bucket_name: str = Field(default="scott-studio-assets", description="S3 bucket")
    s3_access_key_env: str = Field(
        default="STUDIO_S3_ACCESS_KEY_ID",
        description="S3 access key 所在的環境變數名稱",
    )
    s3_secret_key_env: str = Field(
        default="STUDIO_S3_SECRET_ACCESS_KEY",
        description="S3 secret key 所在的環境變數名稱",
    )

    # ── 上傳限制 ──────────────────────────────────────────────────────────────
    upload_max_bytes: int = Field(
        default=200 * 1024 * 1024,
        ge=1,
        description="單檔上傳大小上限（bytes）",
    )

    @field_validator("celery_broker_url", "celery_result_backend", mode="after")
    @classmethod
    def _blank_is_default(cls, value: str) -> str:
        """空字串視為「未設定」，由 property 決定 fallback。"""

        return value.strip()

    @property
    def cors_origin_list(self) -> list[str]:
        """把逗號分隔的來源字串轉為 list。"""

        return [item.strip() for item in self.cors_origins.split(",") if item.strip()]

    @property
    def effective_celery_broker(self) -> str:
        """Celery broker：未單獨設定時沿用 redis_url。"""

        return self.celery_broker_url or self.redis_url

    @property
    def effective_celery_backend(self) -> str:
        """Celery result backend：未單獨設定時沿用 redis_url。"""

        return self.celery_result_backend or self.redis_url

    @property
    def is_sqlite(self) -> bool:
        """目前是否使用 SQLite（影響連線池與部分 DDL 行為）。"""

        return self.database_url.startswith("sqlite")

    @property
    def local_storage_path(self) -> Path:
        """local 後端的絕對路徑。"""

        return Path(self.storage_local_dir).expanduser().resolve()


def resolve_secret(env_var_name: str) -> str:
    """依環境變數名稱取出密鑰值。

    這是整個 Studio 取用密鑰的**唯一入口**。資料庫中的 Provider 只記錄
    環境變數名稱，實際值永遠不落地到資料庫或版本控制。

    Args:
        env_var_name: 環境變數名稱，例如 `ANTHROPIC_API_KEY`。

    Returns:
        環境變數的值；未設定時回傳空字串（由呼叫端決定是否視為錯誤）。
    """

    if not env_var_name:
        return ""
    return os.environ.get(env_var_name.strip(), "")


def secret_is_configured(env_var_name: str) -> bool:
    """判斷某個密鑰環境變數是否已設定。

    供 Provider 列表頁顯示「金鑰是否就緒」，且不會洩漏金鑰內容本身。
    """

    return bool(resolve_secret(env_var_name))


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """取得單例設定物件。

    以 `lru_cache` 確保整個 process 只解析一次環境變數；
    測試需要覆寫時可呼叫 `get_settings.cache_clear()`。
    """

    return Settings()
