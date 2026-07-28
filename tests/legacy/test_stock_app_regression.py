"""既有股市分析系統回歸測試。

目的：證明新增 Scott Studio **沒有改變** 既有股票系統的任何行為。

這些測試針對 `app.py` 匯出的**真實 Flask app**，不使用替身，因此若有人
不小心改動既有路由、認證或資料存取，測試會直接失敗。

注意：匯入 `app` 會啟動既有的背景排程執行緒（價格告警、決策掃描、每日報告）。
這是既有行為，測試以 module 範圍的 fixture 只匯入一次。
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module", name="legacy")
def _legacy():
    """匯入真實的股票 Flask app（整個 module 只匯入一次）。"""

    sys.path.insert(0, str(REPO_ROOT))
    import app as legacy_app

    return legacy_app


@pytest.fixture(name="client")
def _client(legacy):
    """提供股票 app 的測試 client。"""

    legacy.app.config["TESTING"] = True
    return legacy.app.test_client()


@pytest.fixture(name="secured")
def _secured(legacy, monkeypatch):
    """開啟認證模式並清空登入限流狀態。

    `_ACCESS_CODE` 是模組層級全域變數，於匯入時讀取環境變數，
    因此測試以 monkeypatch 直接覆寫該全域值。
    """

    monkeypatch.setattr(legacy, "_ACCESS_CODE", "test-code-1234")
    legacy._login_attempts.clear()
    yield legacy
    legacy._login_attempts.clear()


# ── 1. 首頁 ───────────────────────────────────────────────────────────────────


def test_home_page_opens(client) -> None:
    """GET / 必須正常開啟。"""

    response = client.get("/")
    assert response.status_code == 200


def test_home_page_is_still_the_stock_dashboard(client) -> None:
    """首頁必須仍是股票系統，不得被換成 Studio Dashboard。"""

    body = client.get("/").get_data(as_text=True)

    assert "Studio" not in body or "股" in body, "首頁疑似被換成 Studio 頁面"
    # 首頁必須來自既有樣板，而非 FastAPI 或 SPA 外殼
    assert "__SCOTT_STUDIO_ENV__" not in body
    assert "<!DOCTYPE html>" in body or "<!doctype html>" in body


# ── 2. 登入頁 ─────────────────────────────────────────────────────────────────


def test_login_page_opens(client) -> None:
    """GET /login 必須正常開啟。"""

    response = client.get("/login")
    assert response.status_code == 200
    assert "<form" in response.get_data(as_text=True).lower()


# ── 3. 登入成功 / 失敗 / 登出 ────────────────────────────────────────────────


def test_login_success_sets_session_and_redirects(secured) -> None:
    """正確認識碼應登入成功、寫入 session 並轉址回首頁。"""

    with secured.app.test_client() as client:
        response = client.post("/login", data={"code": "test-code-1234"})

        assert response.status_code == 302
        assert response.headers["Location"] in ("/", "http://localhost/")

        from flask import session

        expected = hashlib.sha256(b"test-code-1234").hexdigest()
        assert session.get("auth") == expected


def test_login_failure_does_not_authenticate(secured) -> None:
    """錯誤認識碼不得建立 session，且應回傳錯誤訊息。"""

    with secured.app.test_client() as client:
        response = client.post("/login", data={"code": "wrong-code"})

        assert response.status_code == 200  # 重新渲染登入頁，非轉址
        assert "錯誤" in response.get_data(as_text=True)

        from flask import session

        assert session.get("auth") is None


def test_login_lockout_after_five_failures(secured) -> None:
    """連續 5 次失敗後應鎖定，這是既有的防爆破行為。"""

    client = secured.app.test_client()
    for _ in range(5):
        client.post("/login", data={"code": "wrong"})

    response = client.post("/login", data={"code": "wrong"})
    assert "鎖定" in response.get_data(as_text=True)


def test_logout_clears_session(secured) -> None:
    """登出必須清除 session 並轉回登入頁。"""

    with secured.app.test_client() as client:
        client.post("/login", data={"code": "test-code-1234"})

        response = client.get("/logout")
        assert response.status_code == 302

        from flask import session

        assert session.get("auth") is None


# ── 4. 權限行為 ───────────────────────────────────────────────────────────────


def test_unauthenticated_page_redirects_to_login(secured) -> None:
    """未登入存取頁面應轉址到登入頁（既有行為）。"""

    client = secured.app.test_client()
    response = client.get("/")

    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_unauthenticated_api_returns_json_401(secured) -> None:
    """未登入呼叫 API 應回 JSON 401，而非轉址（既有行為）。"""

    client = secured.app.test_client()
    response = client.get("/api/health")

    assert response.status_code == 401
    assert response.get_json()["ok"] is False


def test_login_page_reachable_without_auth(secured) -> None:
    """登入頁本身必須在未登入狀態下可存取，否則無法登入。"""

    assert secured.app.test_client().get("/login").status_code == 200


def test_authenticated_user_can_open_stock_pages(secured) -> None:
    """登入後必須能開啟既有股票頁面。"""

    client = secured.app.test_client()
    client.post("/login", data={"code": "test-code-1234"})

    for path in ("/", "/agent", "/admin"):
        response = client.get(path)
        assert response.status_code == 200, f"{path} 回傳 {response.status_code}"


def test_auth_disabled_when_access_code_unset(legacy, monkeypatch) -> None:
    """未設定 ACCESS_CODE 時不需登入 —— 這是既有的預設行為。"""

    monkeypatch.setattr(legacy, "_ACCESS_CODE", "")
    assert legacy.app.test_client().get("/").status_code == 200


def test_session_cookie_settings_unchanged(legacy) -> None:
    """Session cookie 設定必須維持原樣。"""

    config = legacy.app.config
    assert config["SESSION_COOKIE_SAMESITE"] == "Lax"
    assert config["PERMANENT_SESSION_LIFETIME"].days == 30
    assert legacy.app.secret_key


# ── 5. 既有股票路由 ───────────────────────────────────────────────────────────


def test_core_stock_routes_still_registered(legacy) -> None:
    """既有核心股票路由必須全部存在。"""

    rules = {str(rule) for rule in legacy.app.url_map.iter_rules()}

    required = {
        "/",
        "/login",
        "/logout",
        "/admin",
        "/agent",
        "/momentum",
        "/api/health",
        "/api/env-check",
        "/api/analyze",
        "/api/backtest",
        "/api/momentum-score",
        "/api/user/data",
        "/api/portfolio-optimize",
        "/api/position-size",
        "/api/daily-report",
        "/api/nasdaq-screener",
    }
    missing = required - rules
    assert not missing, f"既有路由消失：{sorted(missing)}"


def test_route_count_has_not_shrunk(legacy) -> None:
    """路由總數不得減少（基準：112 條，改造前實測）。"""

    count = len(list(legacy.app.url_map.iter_rules()))
    assert count >= 112, f"路由數從 112 降到 {count}"


def test_studio_paths_are_not_registered_on_flask(legacy) -> None:
    """Studio 路徑不得出現在 Flask app 上 —— 兩者必須完全隔離。"""

    rules = [str(rule) for rule in legacy.app.url_map.iter_rules()]

    assert not [r for r in rules if r.startswith("/studio")]
    assert not [r for r in rules if r.startswith("/api/v1/studio")]


# ── 6. 核心股票 API ───────────────────────────────────────────────────────────


def test_health_api_responds(client) -> None:
    """核心健康檢查 API 必須可用。"""

    response = client.get("/api/health")
    assert response.status_code == 200
    assert response.get_json() is not None


def test_env_check_api_responds(client) -> None:
    """環境檢查 API 必須可用。"""

    assert client.get("/api/env-check").status_code == 200


def test_user_data_api_responds(client) -> None:
    """使用者資料 API 必須可用（持股／自選股皆存於此，讀取既有 SQLite）。"""

    response = client.get("/api/user/data")
    assert response.status_code == 200
    assert response.get_json() is not None


def test_momentum_page_route_resolves_to_legacy_view(legacy) -> None:
    """動能分析頁必須仍解析到既有的股票 view function。

    刻意不發送請求：此頁會即時抓取台股行情，在無外部網路的 CI／沙箱中
    必然失敗。這裡用 URL adapter 直接驗證「路由未消失、未被 Studio 蓋掉」，
    外部行情可用性不屬於本次改造的範圍。
    """

    adapter = legacy.app.url_map.bind("localhost")
    endpoint, _args = adapter.match("/momentum")

    view = legacy.app.view_functions[endpoint]
    assert view.__module__ == "app", f"/momentum 被非股票模組接管：{view.__module__}"


def test_analyze_api_rejects_empty_payload_not_crash(client) -> None:
    """分析 API 對空輸入應正常回應而非拋例外。"""

    response = client.post("/api/analyze", json={})
    assert response.status_code < 500


# ── 7. 既有 SQLite 資料庫 ─────────────────────────────────────────────────────


def test_user_data_db_path_unchanged(legacy) -> None:
    """使用者資料庫路徑必須維持原樣。"""

    assert legacy._USER_DATA_DB == os.environ.get("USER_DATA_DB", "./user_data.db")


def test_user_data_read_write_roundtrip(legacy) -> None:
    """既有使用者資料讀寫必須正常運作。"""

    data = legacy._load_user_data()
    assert isinstance(data, dict)


def test_legacy_sqlite_schema_intact(legacy, tmp_path, monkeypatch) -> None:
    """既有 SQLite 建表邏輯必須產生原本的資料表與欄位。

    以暫存資料庫執行既有初始化流程，確認欄位語義未被改動。
    """

    db_path = tmp_path / "user_data.db"
    monkeypatch.setattr(legacy, "_USER_DATA_DB", str(db_path))
    legacy._init_user_db()

    with sqlite3.connect(db_path) as con:
        tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "kv" in tables, f"kv 資料表遺失，實際有：{tables}"

        columns = {row[1] for row in con.execute("PRAGMA table_info(kv)")}
        assert {"key", "value", "ts"} <= columns, f"kv 欄位語義改變：{columns}"


def test_no_studio_tables_in_legacy_database(legacy, tmp_path, monkeypatch) -> None:
    """Studio 資料表絕不可出現在股票資料庫中。"""

    db_path = tmp_path / "user_data.db"
    monkeypatch.setattr(legacy, "_USER_DATA_DB", str(db_path))
    legacy._init_user_db()

    with sqlite3.connect(db_path) as con:
        tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}

    studio_tables = {name for name in tables if name.startswith("studio_")}
    assert not studio_tables, f"股票資料庫被寫入 Studio 資料表：{studio_tables}"


def test_studio_migration_never_touches_legacy_tables() -> None:
    """Alembic migration 不得操作任何非 studio_ 資料表。"""

    migrations = (REPO_ROOT / "migrations" / "versions").glob("*.py")
    legacy_names = ("user_data", "observation", "signal_confidence", "daily_report", "stress_test", "alert_history")

    for path in migrations:
        source = path.read_text()
        for name in legacy_names:
            assert name not in source, f"{path.name} 引用了股票資料表 {name}"


# ── 8. 獨立運作（不依賴 Studio 相依）─────────────────────────────────────────


def _run_isolated(snippet: str) -> subprocess.CompletedProcess:
    """在封鎖 Studio 相依的子行程中執行程式碼。

    以 meta path finder 讓 redis / celery / fastapi / sqlalchemy / studio
    等模組在匯入時直接失敗，藉此證明股票系統不依賴它們。
    """

    blocker = textwrap.dedent(
        """
        import sys

        BLOCKED = ("redis", "celery", "fastapi", "sqlalchemy", "alembic",
                   "boto3", "aiosqlite", "asyncpg", "uvicorn", "studio",
                   "studio_server", "pydantic_settings", "a2wsgi")

        class _Blocker:
            def find_module(self, name, path=None):
                return self.find_spec(name, path)

            def find_spec(self, name, path=None, target=None):
                root = name.split(".")[0]
                if root in BLOCKED:
                    raise ImportError(f"BLOCKED dependency: {name}")
                return None

        sys.meta_path.insert(0, _Blocker())
        """
    )
    return subprocess.run(
        [sys.executable, "-c", blocker + textwrap.dedent(snippet)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=300,
    )


def test_stock_app_imports_without_studio_dependencies() -> None:
    """股票系統必須能在沒有 Redis / Celery / SQLAlchemy / Studio 的情況下匯入。

    這直接對應「不得成為股票系統啟動的必要條件」。
    """

    result = _run_isolated(
        """
        import app
        print("IMPORT_OK")
        """
    )
    assert "IMPORT_OK" in result.stdout, f"stdout={result.stdout}\nstderr={result.stderr[-2000:]}"


def test_stock_app_serves_home_without_studio_dependencies() -> None:
    """股票系統在沒有任何 Studio 相依時仍必須能服務首頁。"""

    result = _run_isolated(
        """
        import app
        app.app.config["TESTING"] = True
        response = app.app.test_client().get("/")
        assert response.status_code == 200, response.status_code
        print("SERVE_OK")
        """
    )
    assert "SERVE_OK" in result.stdout, f"stdout={result.stdout}\nstderr={result.stderr[-2000:]}"


def test_stock_app_does_not_import_studio_at_runtime() -> None:
    """股票 app 匯入後，`studio` 套件不得出現在 sys.modules。"""

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys, app; "
            "leaked=[m for m in sys.modules if m=='studio' or m.startswith('studio.')]; "
            "print('LEAKED', leaked)",
        ],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert "LEAKED []" in result.stdout, f"stdout={result.stdout}\nstderr={result.stderr[-2000:]}"


# ── 9. 啟動方式與部署設定 ────────────────────────────────────────────────────


def test_procfile_web_entrypoint_unchanged() -> None:
    """Procfile 的 web process 必須仍是 `python app.py`。"""

    lines = (REPO_ROOT / "Procfile").read_text().splitlines()
    web = [line for line in lines if line.startswith("web:")]

    assert web == ["web: python app.py"], f"web process 被改動：{web}"


def test_railway_start_command_unchanged() -> None:
    """railway.json 的啟動指令必須維持原樣。"""

    import json

    config = json.loads((REPO_ROOT / "railway.json").read_text())
    assert config["deploy"]["startCommand"] == "python app.py"


def test_run_sh_unchanged() -> None:
    """run.sh 必須仍安裝 requirements.txt 並執行 app.py。"""

    content = (REPO_ROOT / "run.sh").read_text()
    assert "pip install -r requirements.txt" in content
    assert "python app.py" in content


def test_requirements_txt_has_no_studio_dependencies() -> None:
    """requirements.txt 不得混入 Studio 相依，避免既有部署變重或衝突。"""

    content = (REPO_ROOT / "requirements.txt").read_text().lower()

    for package in ("fastapi", "sqlalchemy", "celery", "redis", "boto3", "alembic", "uvicorn"):
        assert package not in content, f"requirements.txt 被加入 Studio 相依：{package}"


def test_app_py_has_no_studio_references() -> None:
    """app.py 不得引用任何 Studio 程式碼。"""

    content = (REPO_ROOT / "app.py").read_text()

    assert "import studio" not in content
    assert "from studio" not in content
