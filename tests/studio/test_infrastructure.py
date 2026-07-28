"""Phase 1 基礎架構測試。

驗證設定、儲存抽象、健康檢查與 Flask 併存機制真的可用 —— 不是靠 mock 假裝通過。
"""

from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient

from studio.config import Settings, StorageBackend, TaskExecutionMode, resolve_secret, secret_is_configured
from studio.core.errors import ProviderRateLimitError, StudioError, ValidationError
from studio.core.ids import new_id
from studio.core.storage import LocalObjectStorage
from studio.main import create_app
from studio.schemas.common import ApiResponse, Page

# ── 設定 ──────────────────────────────────────────────────────────────────────


def test_defaults_are_dev_friendly() -> None:
    """預設值必須讓開發者不需外部服務就能啟動。"""

    settings = Settings()
    assert settings.is_sqlite
    assert settings.storage_backend is StorageBackend.local
    assert settings.task_execution_mode is TaskExecutionMode.inline


def test_cors_origins_parsed_from_csv() -> None:
    """CORS 來源以逗號分隔並去除空白。"""

    settings = Settings(cors_origins="http://a.test, http://b.test ,")
    assert settings.cors_origin_list == ["http://a.test", "http://b.test"]


def test_celery_urls_fall_back_to_redis_url() -> None:
    """未單獨設定 broker/backend 時沿用 redis_url。"""

    settings = Settings(redis_url="redis://example:6379/3")
    assert settings.effective_celery_broker == "redis://example:6379/3"
    assert settings.effective_celery_backend == "redis://example:6379/3"


def test_explicit_celery_broker_wins() -> None:
    """明確設定的 broker 不應被 redis_url 覆蓋。"""

    settings = Settings(redis_url="redis://a:6379/0", celery_broker_url="redis://b:6379/1")
    assert settings.effective_celery_broker == "redis://b:6379/1"


# ── 密鑰解析 ──────────────────────────────────────────────────────────────────


def test_resolve_secret_reads_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """密鑰由環境變數名稱解析，而非硬編碼。"""

    monkeypatch.setenv("SCOTT_TEST_SECRET", "s3cr3t")
    assert resolve_secret("SCOTT_TEST_SECRET") == "s3cr3t"
    assert secret_is_configured("SCOTT_TEST_SECRET") is True


def test_resolve_secret_missing_is_empty_not_error() -> None:
    """未設定的密鑰回傳空字串，由呼叫端決定是否為錯誤。"""

    assert resolve_secret("SCOTT_DEFINITELY_UNSET_VAR") == ""
    assert secret_is_configured("SCOTT_DEFINITELY_UNSET_VAR") is False
    assert resolve_secret("") == ""


# ── 識別碼 ────────────────────────────────────────────────────────────────────


def test_new_id_has_prefix_and_is_unique() -> None:
    """ID 帶前綴且不重複。"""

    first = new_id("prj")
    second = new_id("prj")
    assert first.startswith("prj_")
    assert first != second


# ── 本機儲存 ──────────────────────────────────────────────────────────────────


def test_local_storage_roundtrip(tmp_path) -> None:
    """寫入、讀取、存在性檢查與刪除都應正確運作。"""

    storage = LocalObjectStorage(tmp_path)
    storage.put_bytes("a/b/c.txt", b"hello")

    assert storage.exists("a/b/c.txt")
    assert storage.get_bytes("a/b/c.txt") == b"hello"

    storage.delete("a/b/c.txt")
    assert not storage.exists("a/b/c.txt")
    # 刪除不存在的物件應為冪等，不拋錯。
    storage.delete("a/b/c.txt")


def test_local_storage_put_stream(tmp_path) -> None:
    """串流寫入避免大檔佔滿記憶體。"""

    storage = LocalObjectStorage(tmp_path)
    storage.put_stream("s/data.bin", io.BytesIO(b"streamed"))
    assert storage.get_bytes("s/data.bin") == b"streamed"


def test_local_storage_rejects_path_traversal(tmp_path) -> None:
    """`../` 穿越必須被擋下，否則可寫入儲存根目錄之外。"""

    storage = LocalObjectStorage(tmp_path)
    with pytest.raises(StudioError):
        storage.put_bytes("../escaped.txt", b"nope")
    with pytest.raises(StudioError):
        storage.put_bytes("   ", b"nope")


def test_local_storage_missing_object_raises(tmp_path) -> None:
    """讀取不存在物件應拋出 404 語意的錯誤。"""

    storage = LocalObjectStorage(tmp_path)
    with pytest.raises(StudioError) as exc:
        storage.get_bytes("missing.txt")
    assert exc.value.status_code == 404


def test_local_storage_public_url_prefers_configured_base(tmp_path) -> None:
    """設定公開前綴時應直接指向該前綴。"""

    with_base = LocalObjectStorage(tmp_path, "https://cdn.test/assets/")
    assert with_base.public_url("x.png") == "https://cdn.test/assets/x.png"

    without_base = LocalObjectStorage(tmp_path)
    assert without_base.public_url("x.png").startswith("/api/studio/v1/files/content/")


# ── 錯誤型別 ──────────────────────────────────────────────────────────────────


def test_error_payload_shape() -> None:
    """錯誤序列化為穩定的 code/message/details 結構。"""

    err = ValidationError("欄位錯誤", details={"field": "title"})
    payload = err.to_payload()
    assert payload["code"] == "validation_error"
    assert payload["details"]["field"] == "title"
    assert err.status_code == 422


def test_provider_rate_limit_is_retryable() -> None:
    """限流錯誤必須標記為可重試，任務層才會安排重試。"""

    err = ProviderRateLimitError("too many requests", provider="openai")
    assert err.retryable is True
    assert err.details["provider"] == "openai"
    assert err.status_code == 429


# ── 回應信封 ──────────────────────────────────────────────────────────────────


def test_api_response_ok_and_fail() -> None:
    """成功與失敗信封的形狀必須固定。"""

    ok = ApiResponse[dict].ok({"a": 1})
    assert ok.success is True and ok.error is None

    fail = ApiResponse[dict].fail("not_found", "找不到")
    assert fail.success is False and fail.data is None
    assert fail.error is not None and fail.error.code == "not_found"


def test_page_computes_total_pages() -> None:
    """分頁頁數需無條件進位。"""

    page = Page[int].build([1, 2, 3], page=1, page_size=10, total=25)
    assert page.meta.total_pages == 3


# ── API ───────────────────────────────────────────────────────────────────────


@pytest.fixture(name="client")
def _client() -> TestClient:
    """提供 Studio API 測試 client。"""

    return TestClient(create_app())


def test_health_returns_component_status(client: TestClient) -> None:
    """健康檢查應回報各元件狀態，而非只回 200。"""

    response = client.get("/api/studio/v1/health")
    assert response.status_code == 200

    body = response.json()
    assert body["success"] is True

    names = {c["name"] for c in body["data"]["components"]}
    assert names == {"database", "redis", "storage"}


def test_liveness_ignores_dependencies(client: TestClient) -> None:
    """存活探針不檢查外部相依，外部服務異常不應造成容器重啟。"""

    body = client.get("/api/studio/v1/health/live").json()
    assert body["data"]["status"] == "ok"
    assert body["data"]["components"] == []


def test_openapi_spec_is_generated(client: TestClient) -> None:
    """OpenAPI 為前端型別的唯一來源，必須可產生。"""

    spec = client.get("/studio/openapi.json").json()
    assert "/api/studio/v1/health" in spec["paths"]


def test_unmatched_route_uses_standard_envelope(client: TestClient) -> None:
    """404 也要走統一信封，前端才能以單一 helper 解析所有回應。"""

    response = client.get("/api/studio/v1/does-not-exist")
    assert response.status_code == 404

    body = response.json()
    assert body["success"] is False
    assert body["data"] is None
    assert body["error"]["code"] == "not_found"


def test_method_not_allowed_uses_standard_envelope(client: TestClient) -> None:
    """405 同樣需為標準信封，並帶穩定錯誤碼。"""

    body = client.post("/api/studio/v1/health").json()
    assert body["success"] is False
    assert body["error"]["code"] == "method_not_allowed"


def test_studio_error_maps_to_envelope_and_status() -> None:
    """Service 層錯誤應自動轉為對應 HTTP 狀態與標準信封。"""

    api = create_app()

    @api.get("/api/studio/v1/_boom")
    async def _boom() -> None:
        raise ValidationError("欄位不合法", details={"field": "title"})

    body = TestClient(api).get("/api/studio/v1/_boom")
    assert body.status_code == 422

    payload = body.json()
    assert payload["success"] is False
    assert payload["error"]["code"] == "validation_error"
    assert payload["error"]["details"]["field"] == "title"


def test_request_validation_error_uses_standard_envelope() -> None:
    """FastAPI 欄位驗證錯誤也要走統一信封。"""

    api = create_app()

    @api.get("/api/studio/v1/_needs-int")
    async def _needs_int(value: int) -> dict[str, int]:
        return {"value": value}

    response = TestClient(api).get("/api/studio/v1/_needs-int", params={"value": "abc"})
    assert response.status_code == 422

    payload = response.json()
    assert payload["success"] is False
    assert payload["error"]["code"] == "request_validation_error"
    assert payload["error"]["details"]["errors"]


# ── Flask 併存 ────────────────────────────────────────────────────────────────


def test_legacy_flask_app_can_be_mounted() -> None:
    """既有 Flask route 掛載後路徑必須完全不變。

    這是「不破壞 Scott 既有功能」的核心保證。
    """

    flask = pytest.importorskip("flask")
    from a2wsgi import WSGIMiddleware

    legacy = flask.Flask("legacy")

    @legacy.route("/")
    def _home() -> str:
        return "scott stock home"

    @legacy.route("/login")
    def _login() -> str:
        return "scott login"

    api = create_app()
    api.mount("/", WSGIMiddleware(legacy))
    client = TestClient(api)

    # 既有 Flask 路徑不變
    assert client.get("/").text == "scott stock home"
    assert client.get("/login").text == "scott login"

    # Studio 路徑同時可用
    assert client.get("/api/studio/v1/health").status_code == 200
