"""共用 API schema：統一回應信封與分頁。

所有 Studio API 都回傳相同外層結構，讓前端能以單一 helper 處理成功／失敗，
並且 OpenAPI 產生的 TypeScript 型別具有一致形狀。

成功：
    {"success": true, "data": {...}, "error": null}
失敗：
    {"success": false, "data": null, "error": {"code": "...", "message": "...", "details": {...}}}
"""

from __future__ import annotations

from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


class ErrorBody(BaseModel):
    """錯誤內容。"""

    code: str = Field(description="機器可讀錯誤碼")
    message: str = Field(description="人類可讀錯誤訊息")
    details: dict[str, Any] | None = Field(default=None, description="額外結構化資訊")


class ApiResponse(BaseModel, Generic[T]):
    """統一回應信封。"""

    success: bool = Field(default=True, description="是否成功")
    data: T | None = Field(default=None, description="回應資料")
    error: ErrorBody | None = Field(default=None, description="錯誤內容；成功時為 null")

    @classmethod
    def ok(cls, data: T | None = None) -> ApiResponse[T]:
        """建立成功回應。"""

        return cls(success=True, data=data, error=None)

    @classmethod
    def fail(cls, code: str, message: str, details: dict[str, Any] | None = None) -> ApiResponse[T]:
        """建立失敗回應。"""

        return cls(
            success=False,
            data=None,
            error=ErrorBody(code=code, message=message, details=details),
        )


class PageMeta(BaseModel):
    """分頁中繼資訊。"""

    page: int = Field(ge=1, description="目前頁碼，從 1 開始")
    page_size: int = Field(ge=1, description="每頁筆數")
    total: int = Field(ge=0, description="總筆數")
    total_pages: int = Field(ge=0, description="總頁數")


class Page(BaseModel, Generic[T]):
    """分頁結果。"""

    items: list[T] = Field(default_factory=list, description="本頁項目")
    meta: PageMeta = Field(description="分頁中繼資訊")

    @classmethod
    def build(cls, items: list[T], *, page: int, page_size: int, total: int) -> Page[T]:
        """由查詢結果組出分頁物件。"""

        total_pages = (total + page_size - 1) // page_size if page_size else 0
        return cls(
            items=items,
            meta=PageMeta(page=page, page_size=page_size, total=total, total_pages=total_pages),
        )


class OkData(BaseModel):
    """僅表示操作成功、無額外資料時使用。"""

    ok: bool = Field(default=True, description="操作是否成功")


class HealthComponent(BaseModel):
    """單一相依元件的健康狀態。"""

    name: str = Field(description="元件名稱")
    healthy: bool = Field(description="是否健康")
    detail: str = Field(default="", description="補充說明（例如降級原因）")


class HealthData(BaseModel):
    """health endpoint 回應。"""

    status: str = Field(description="整體狀態：ok / degraded")
    app: str = Field(description="應用名稱")
    task_mode: str = Field(description="目前任務執行模式")
    storage_backend: str = Field(description="目前物件儲存後端")
    components: list[HealthComponent] = Field(default_factory=list, description="各相依元件狀態")
