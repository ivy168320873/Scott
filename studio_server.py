"""Scott 統一 ASGI 進入點：Studio API + 既有股市分析 app。

架構：
- FastAPI（Studio）為 ASGI 主體，處理 `/api/v1/studio/*` 與 `/studio/*`。
- 既有 Flask app（股市分析）以 WSGI middleware 掛在 `/`，路徑完全不變。

因此：
- 既有所有 Flask route（`/`、`/login`、`/admin`、`/agent` …）行為不受影響。
- `python app.py` 這個原始啟動方式仍然完全可用，不依賴本檔。

啟動：
    uvicorn studio_server:application --host 0.0.0.0 --port 8000

Flask app 匯入失敗時（例如缺少 pandas / yfinance 等股市相依），
Studio 仍會正常啟動，只是 `/` 會回報股市模組不可用 —— 兩套系統彼此隔離。
"""

from __future__ import annotations

import logging
import os

from a2wsgi import WSGIMiddleware
from starlette.responses import JSONResponse, Response
from starlette.types import Receive, Scope, Send

from studio.main import app as studio_app

logger = logging.getLogger("studio.server")


def _load_legacy_flask_app():
    """嘗試匯入既有 Flask app。

    Returns:
        Flask app 物件；匯入失敗時回傳 None（不讓 Studio 一起掛掉）。
    """

    try:
        from app import app as flask_app  # 既有 Scott 股市分析 app

        return flask_app
    except Exception as exc:  # noqa: BLE001 — 任何匯入問題都只降級，不中斷 Studio
        logger.warning("legacy Flask app unavailable, serving Studio only: %s", exc)
        return None


async def _legacy_unavailable(scope: Scope, receive: Receive, send: Send) -> None:
    """Flask app 不可用時的替代處理器。"""

    response = JSONResponse(
        status_code=503,
        content={
            "success": False,
            "data": None,
            "error": {
                "code": "legacy_app_unavailable",
                "message": "股市分析模組目前不可用；Studio API 仍可正常使用（/api/v1/studio）。",
            },
        },
    )
    await response(scope, receive, send)


def _mount_frontend(app) -> bool:
    """把已建置的 Studio 前端掛在 /studio。

    只有 `frontend/dist` 存在時才掛載（正式部署通常由 nginx 服務前端）。
    SPA 需要 fallback：任何 /studio/* 路徑都回 index.html，
    由前端路由決定顯示哪個頁面。

    Returns:
        是否成功掛載。
    """

    from pathlib import Path

    dist = Path(__file__).resolve().parent / "frontend" / "dist"
    index = dist / "index.html"
    if not index.is_file():
        return False

    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles

    app.mount("/studio/assets", StaticFiles(directory=dist / "assets"), name="studio-assets")

    @app.get("/studio/env.js", include_in_schema=False)
    async def _studio_env() -> Response:
        """執行期注入後端位址；同源部署時為空字串。"""

        base = os.environ.get("STUDIO_API_BASE_URL", "")
        return Response(
            content=f'window.__SCOTT_STUDIO_ENV__ = {{ API_BASE_URL: "{base}" }};',
            media_type="application/javascript",
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/studio", include_in_schema=False)
    @app.get("/studio/{path:path}", include_in_schema=False)
    async def _studio_spa(path: str = "") -> FileResponse:
        """SPA fallback。"""

        return FileResponse(index)

    logger.info("studio frontend mounted at /studio")
    return True


def build_application():
    """組出對外服務的 ASGI application。"""

    # 前端必須先掛：Flask 掛在 `/` 會攔截所有未匹配路徑。
    _mount_frontend(studio_app)

    flask_app = _load_legacy_flask_app()
    if flask_app is not None:
        studio_app.mount("/", WSGIMiddleware(flask_app))
        logger.info("legacy Flask app mounted at /")
    else:
        studio_app.mount("/", _legacy_unavailable)
    return studio_app


application = build_application()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "studio_server:application",
        host=os.environ.get("STUDIO_HOST", "0.0.0.0"),
        port=int(os.environ.get("STUDIO_PORT", "8000")),
        reload=os.environ.get("STUDIO_RELOAD", "").lower() in {"1", "true", "yes"},
    )
