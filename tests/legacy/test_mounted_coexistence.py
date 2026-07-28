"""併存模式回歸測試：真實股票 app 掛載於 Studio ASGI 之下。

`studio_server.py` 讓 FastAPI 作為 ASGI 主體、把既有 Flask app 經 WSGI
middleware 掛在 `/`。本檔驗證掛載**沒有改變**股票系統的任何對外行為：
路徑、狀態碼、session cookie、認證轉址、JSON 401 皆須與獨立執行時相同。

這是最容易出問題的地方 —— WSGI 橋接若處理不當，最常見的症狀就是
session cookie 遺失或路徑前綴錯亂。
"""

from __future__ import annotations

import hashlib
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module", name="legacy")
def _legacy():
    """匯入真實股票 app。"""

    sys.path.insert(0, str(REPO_ROOT))
    import app as legacy_app

    return legacy_app


@pytest.fixture(name="mounted")
def _mounted(legacy):
    """建立「Studio + 真實股票 app」的併存 ASGI application。"""

    from a2wsgi import WSGIMiddleware

    from studio.main import create_app

    api = create_app()
    api.mount("/", WSGIMiddleware(legacy.app))
    return TestClient(api)


# ── 既有路徑在掛載後不變 ─────────────────────────────────────────────────────


def test_home_served_through_mount(mounted) -> None:
    """掛載後 GET / 仍必須由股票系統服務。"""

    response = mounted.get("/")
    assert response.status_code == 200
    assert "__SCOTT_STUDIO_ENV__" not in response.text


def test_login_page_served_through_mount(mounted) -> None:
    """掛載後 GET /login 仍必須正常。"""

    response = mounted.get("/login")
    assert response.status_code == 200
    assert "<form" in response.text.lower()


def test_stock_api_served_through_mount(mounted) -> None:
    """掛載後既有股票 API 仍必須正常回應 JSON。"""

    response = mounted.get("/api/health")
    assert response.status_code == 200
    assert response.json() is not None


def test_user_data_api_served_through_mount(mounted) -> None:
    """掛載後使用者資料 API（讀既有 SQLite）仍必須正常。"""

    assert mounted.get("/api/user/data").status_code == 200


# ── Studio 與股票系統同時可用 ────────────────────────────────────────────────


def test_studio_api_available_alongside_stock_app(mounted) -> None:
    """Studio API 必須與股票系統同時可用，且位於 /api/v1/studio。"""

    response = mounted.get("/api/v1/studio/health")
    assert response.status_code == 200
    assert response.json()["success"] is True


def test_studio_docs_live_under_studio_prefix(mounted) -> None:
    """Studio 的所有頁面資源都必須收斂在 /studio 之下。"""

    assert mounted.get("/studio/openapi.json").status_code == 200


def test_studio_paths_do_not_shadow_stock_api(mounted) -> None:
    """Studio 不得攔截既有 /api/* 路徑。"""

    # 既有股票 API 與 Studio API 前綴不同，兩者都必須各自回應
    assert mounted.get("/api/health").status_code == 200
    assert mounted.get("/api/v1/studio/health").status_code == 200


# ── Session / Cookie 穿透 WSGI 橋接 ──────────────────────────────────────────


def test_session_cookie_survives_mount(legacy, monkeypatch) -> None:
    """登入 session 必須能穿過 WSGI 橋接正確設定與帶回。

    這是掛載模式最關鍵的回歸點：cookie 若在橋接層遺失，
    使用者會表現為「登入後立刻被登出」。
    """

    from a2wsgi import WSGIMiddleware

    from studio.main import create_app

    monkeypatch.setattr(legacy, "_ACCESS_CODE", "mount-code-999")
    legacy._login_attempts.clear()

    api = create_app()
    api.mount("/", WSGIMiddleware(legacy.app))
    client = TestClient(api)

    # 未登入 → 轉址到登入頁
    unauth = client.get("/", follow_redirects=False)
    assert unauth.status_code == 302
    assert "/login" in unauth.headers["location"]

    # 登入 → 應設定 session cookie
    login = client.post("/login", data={"code": "mount-code-999"}, follow_redirects=False)
    assert login.status_code == 302
    assert "session" in login.cookies or "session" in client.cookies

    # 帶著 cookie 再次存取 → 應可進入首頁
    authed = client.get("/", follow_redirects=False)
    assert authed.status_code == 200, "session cookie 未能穿過 WSGI 橋接"

    legacy._login_attempts.clear()


def test_unauthenticated_api_still_returns_json_401_when_mounted(legacy, monkeypatch) -> None:
    """掛載後未登入的股票 API 仍須回 JSON 401，而非轉址或 500。"""

    from a2wsgi import WSGIMiddleware

    from studio.main import create_app

    monkeypatch.setattr(legacy, "_ACCESS_CODE", "mount-code-888")
    legacy._login_attempts.clear()

    api = create_app()
    api.mount("/", WSGIMiddleware(legacy.app))

    response = TestClient(api).get("/api/health", follow_redirects=False)
    assert response.status_code == 401
    assert response.json()["ok"] is False

    legacy._login_attempts.clear()


def test_login_failure_behaviour_unchanged_when_mounted(legacy, monkeypatch) -> None:
    """掛載後錯誤認識碼仍須重新渲染登入頁並顯示錯誤。"""

    from a2wsgi import WSGIMiddleware

    from studio.main import create_app

    monkeypatch.setattr(legacy, "_ACCESS_CODE", "mount-code-777")
    legacy._login_attempts.clear()

    api = create_app()
    api.mount("/", WSGIMiddleware(legacy.app))

    response = TestClient(api).post("/login", data={"code": "nope"}, follow_redirects=False)
    assert response.status_code == 200
    assert "錯誤" in response.text

    legacy._login_attempts.clear()


def test_auth_hash_scheme_unchanged(legacy) -> None:
    """session 中儲存的認證值仍須為認識碼的 SHA-256，語義未變。"""

    assert legacy._hash("abc") == hashlib.sha256(b"abc").hexdigest()


# ── 背景排程不受影響 ─────────────────────────────────────────────────────────


def test_legacy_background_threads_started(legacy) -> None:
    """既有背景排程執行緒必須仍會啟動（掛載不影響 APScheduler／執行緒）。"""

    import threading

    names = {thread.name for thread in threading.enumerate()}
    expected_fragments = ("price_alert", "decision_alert", "daily-report")

    found = [frag for frag in expected_fragments if any(frag in name for name in names)]
    assert found, f"既有背景執行緒未啟動，目前執行緒：{names}"


def test_studio_does_not_register_flask_before_request(legacy) -> None:
    """Studio 不得在 Flask app 上註冊任何額外的 before_request hook。"""

    hooks = legacy.app.before_request_funcs.get(None, [])
    modules = {getattr(fn, "__module__", "") for fn in hooks}

    assert modules <= {"app"}, f"Flask before_request 被外部模組污染：{modules}"
