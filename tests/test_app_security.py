"""Flask authentication, redirect and secret-storage regression tests."""

import importlib


def _load_app(monkeypatch, tmp_path):
    monkeypatch.setenv("ACCESS_CODE", "correct-horse-battery-staple")
    monkeypatch.setenv("SECRET_KEY", "unit-test-secret-key")
    monkeypatch.setenv("USER_DATA_DB", str(tmp_path / "user_data.db"))
    monkeypatch.setenv("BACKGROUND_WORKERS_ENABLE", "false")
    monkeypatch.setenv("SCHEDULER_ENABLE", "false")
    monkeypatch.setenv("ALLOW_DEMO_DATA", "false")
    module = importlib.import_module("app")
    module.app.config.update(TESTING=True)
    return module


def test_admin_and_api_require_login(monkeypatch, tmp_path):
    module = _load_app(monkeypatch, tmp_path)
    client = module.app.test_client()
    admin = client.get("/admin")
    assert admin.status_code == 302
    assert "/login" in admin.headers["Location"]
    api = client.get("/api/trade/status")
    assert api.status_code == 401


def test_login_rejects_external_next_redirect(monkeypatch, tmp_path):
    module = _load_app(monkeypatch, tmp_path)
    client = module.app.test_client()
    response = client.post(
        "/login?next=//evil.example/path",
        data={"code": "correct-horse-battery-staple"},
    )
    assert response.status_code == 302
    assert response.headers["Location"] == "/"


def test_security_headers_and_line_secret_scrubbing(monkeypatch, tmp_path):
    module = _load_app(monkeypatch, tmp_path)
    assert module._strip_line_secrets({"email": "a@b.test", "lineToken": "secret"}) == {
        "email": "a@b.test"
    }
    response = module.app.test_client().get("/healthz")
    assert response.headers["X-Frame-Options"] == "DENY"
    assert "object-src 'none'" in response.headers["Content-Security-Policy"]


def test_persistent_engines_and_financial_input_validation(monkeypatch, tmp_path):
    module = _load_app(monkeypatch, tmp_path)
    assert module._obe._DB_PATH == module._USER_DATA_DB
    assert module._normalise_symbols(["aapl", "2330.tw"]) == ["AAPL", "2330.TW"]
    assert module._normalise_symbols(["<script>"]) is None
    assert module._normalise_holdings([
        {"symbol": "AAPL", "cost": 100, "qty": 2, "buy_date": "2025-01-02"}
    ]) == [{"symbol": "AAPL", "cost": 100.0, "qty": 2.0, "buy_date": "2025-01-02"}]
    assert module._normalise_holdings([
        {"symbol": "AAPL", "cost": float("nan"), "qty": 2}
    ]) is None
