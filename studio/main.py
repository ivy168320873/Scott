"""Scott Studio FastAPI 應用組裝。

此模組只負責組裝：建立 app、掛 middleware、註冊 router、註冊例外處理器。
業務邏輯一律不放這裡。

注意：本 app 不含既有 Scott 股市分析系統。若要一併服務兩者，
請使用 `studio_server.py`（會把 Flask app 掛在 `/`）。
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from studio.api.v1.router import api_router
from studio.config import get_settings
from studio.core.db import dispose_engine
from studio.core.errors import StudioError
from studio.core.redis_client import close_redis
from studio.schemas.common import ApiResponse

logger = logging.getLogger("studio")

# 框架層 HTTP 狀態碼 → 穩定錯誤碼，讓前端不必依賴數字狀態碼分支。
_HTTP_ERROR_CODES: dict[int, str] = {
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    413: "payload_too_large",
    429: "rate_limited",
}


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """應用生命週期。

    啟動時不主動建立資料表（交由 Alembic migration 負責），
    僅在關閉時釋放連線資源，避免容器重啟時留下懸掛連線。
    """

    settings = get_settings()
    logger.info(
        "starting %s (task_mode=%s, storage=%s)",
        settings.app_name,
        settings.task_execution_mode.value,
        settings.storage_backend.value,
    )
    try:
        yield
    finally:
        await close_redis()
        await dispose_engine()
        logger.info("%s stopped", settings.app_name)


def create_app() -> FastAPI:
    """建立並設定 FastAPI 應用。

    以工廠函式而非模組層級單例建立，讓測試可以用不同設定建立多個實例。
    """

    settings = get_settings()

    app = FastAPI(
        title=f"{settings.app_name} API",
        description=(
            "Scott Studio — AI 影片／短劇生成工作平台。\n\n"
            "本 API 為前端的唯一規格來源；前端型別與 client 由 `/openapi.json` 自動產生。"
        ),
        version="0.1.0",
        debug=settings.debug,
        lifespan=lifespan,
        docs_url="/studio/docs",
        redoc_url="/studio/redoc",
        openapi_url="/studio/openapi.json",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(api_router, prefix=settings.api_v1_prefix)

    _register_exception_handlers(app)
    return app


def _register_exception_handlers(app: FastAPI) -> None:
    """註冊例外處理器，確保錯誤回應與成功回應使用相同信封。"""

    @app.exception_handler(StudioError)
    async def _studio_error_handler(_request: Request, exc: StudioError) -> JSONResponse:
        """把 Service 層錯誤轉為標準錯誤信封。"""

        return JSONResponse(
            status_code=exc.status_code,
            content=ApiResponse.fail(exc.code, exc.message, exc.details).model_dump(mode="json"),
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http_error_handler(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
        """把框架層 HTTP 錯誤（404、405 等）轉為標準錯誤信封。

        沒有這個處理器時，未匹配路由會回傳 FastAPI 預設的 `{"detail": ...}`，
        與其他端點形狀不一致，前端就得寫兩套解析邏輯。
        """

        code = _HTTP_ERROR_CODES.get(exc.status_code, "http_error")
        return JSONResponse(
            status_code=exc.status_code,
            content=ApiResponse.fail(code, str(exc.detail)).model_dump(mode="json"),
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_error_handler(_request: Request, exc: RequestValidationError) -> JSONResponse:
        """把 FastAPI 的欄位驗證錯誤轉為標準錯誤信封。"""

        return JSONResponse(
            status_code=422,
            content=ApiResponse.fail(
                "request_validation_error",
                "請求參數驗證失敗",
                {"errors": exc.errors()},
            ).model_dump(mode="json"),
        )

    @app.exception_handler(Exception)
    async def _unhandled_error_handler(_request: Request, exc: Exception) -> JSONResponse:
        """兜底處理未預期例外。

        不把內部細節回傳給前端（避免洩漏堆疊或連線字串），但完整記錄到日誌。
        """

        logger.exception("unhandled error: %s", exc)
        return JSONResponse(
            status_code=500,
            content=ApiResponse.fail("internal_error", "伺服器內部錯誤").model_dump(mode="json"),
        )


app = create_app()
