"""Phase 3 API 測試。

以真實 FastAPI app + 真實 SQLite 資料庫執行，不使用假的 service 或
永遠成功的 mock。每個測試取得獨立資料庫，確保彼此隔離。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from studio.core.db import Base
from studio.core.deps import get_db
from studio.main import create_app

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(name="client")
def _client(tmp_path, monkeypatch) -> Iterator[TestClient]:
    """提供接上獨立資料庫的 Studio API client（認證關閉）。"""

    monkeypatch.delenv("ACCESS_CODE", raising=False)
    monkeypatch.setenv("STUDIO_STORAGE_LOCAL_DIR", str(tmp_path / "storage"))

    from studio.core.storage import reset_storage

    reset_storage()

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path/'api.db'}")

    @event.listens_for(engine.sync_engine, "connect")
    def _fk_on(dbapi_connection, _record):  # type: ignore[no-untyped-def]
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    factory = async_sessionmaker(engine, expire_on_commit=False)

    app = create_app()

    async def _override_db():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        session = factory()
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

    app.dependency_overrides[get_db] = _override_db

    with TestClient(app) as test_client:
        yield test_client

    reset_storage()


def _data(response) -> dict:
    """取出成功回應的 data 區塊，順便斷言信封形狀。"""

    body = response.json()
    assert body["success"] is True, body
    return body["data"]


def _make_project(client: TestClient, name: str = "測試短劇") -> dict:
    """建立一個專案並回傳其資料。"""

    return _data(client.post("/api/v1/studio/projects", json={"name": name}))


def _make_chapter(client: TestClient, project_id: str, title: str = "第一集") -> dict:
    """建立一個章節並回傳其資料。"""

    return _data(client.post(f"/api/v1/studio/projects/{project_id}/chapters", json={"title": title}))


def _make_shot(client: TestClient, chapter_id: str, title: str = "鏡頭一") -> dict:
    """建立一個分鏡並回傳其資料。"""

    return _data(client.post(f"/api/v1/studio/chapters/{chapter_id}/shots", json={"title": title}))


# ── Health ────────────────────────────────────────────────────────────────────


def test_health_ok(client: TestClient) -> None:
    """健康檢查在 API 掛載後仍正常。"""

    assert client.get("/api/v1/studio/health").status_code == 200


def test_api_is_under_v1_studio_prefix(client: TestClient) -> None:
    """所有 Studio API 都必須在 /api/v1/studio 之下。"""

    spec = client.get("/studio/openapi.json").json()
    offenders = [p for p in spec["paths"] if not p.startswith("/api/v1/studio")]
    assert offenders == [], f"路徑未收斂於 /api/v1/studio：{offenders}"


# ── 認證 ──────────────────────────────────────────────────────────────────────


@pytest.fixture(name="secured_client")
def _secured_client(client: TestClient, monkeypatch) -> TestClient:
    """開啟認證的 client（未帶 session cookie）。"""

    monkeypatch.setenv("ACCESS_CODE", "studio-code-123")
    monkeypatch.setenv("SECRET_KEY", "studio-secret-key")
    return client


def test_unauthenticated_returns_json_401_not_html(secured_client: TestClient) -> None:
    """未登入必須回 JSON 401，絕不可回 HTML 登入頁。"""

    response = secured_client.get("/api/v1/studio/projects")

    assert response.status_code == 401
    assert response.headers["content-type"].startswith("application/json")

    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == "unauthorized"
    assert "<html" not in response.text.lower()


def test_authenticated_via_flask_session_cookie(secured_client: TestClient) -> None:
    """帶著股票系統簽發的 session cookie 即可存取 Studio API。

    這證明兩套系統共用同一個登入，不需要第二次登入。
    """

    from itsdangerous import URLSafeTimedSerializer

    serializer = URLSafeTimedSerializer(
        "studio-secret-key",
        salt="cookie-session",
        signer_kwargs={"key_derivation": "hmac", "digest_method": hashlib.sha1},
    )
    cookie = serializer.dumps({"auth": hashlib.sha256(b"studio-code-123").hexdigest()})

    secured_client.cookies.set("session", cookie)
    response = secured_client.get("/api/v1/studio/projects")

    assert response.status_code == 200


def test_tampered_session_cookie_rejected(secured_client: TestClient) -> None:
    """偽造的 session cookie 必須被拒絕。"""

    secured_client.cookies.set("session", "not-a-valid-signed-cookie")
    assert secured_client.get("/api/v1/studio/projects").status_code == 401


def test_wrong_secret_key_rejected(secured_client: TestClient, monkeypatch) -> None:
    """以不同金鑰簽出的 cookie 必須被拒絕。"""

    from itsdangerous import URLSafeTimedSerializer

    serializer = URLSafeTimedSerializer(
        "a-different-secret",
        salt="cookie-session",
        signer_kwargs={"key_derivation": "hmac", "digest_method": hashlib.sha1},
    )
    cookie = serializer.dumps({"auth": hashlib.sha256(b"studio-code-123").hexdigest()})

    secured_client.cookies.set("session", cookie)
    assert secured_client.get("/api/v1/studio/projects").status_code == 401


def test_auth_disabled_when_access_code_unset(client: TestClient) -> None:
    """未設定 ACCESS_CODE 時不需登入，與股票系統行為一致。"""

    assert client.get("/api/v1/studio/projects").status_code == 200


# ── Project CRUD ──────────────────────────────────────────────────────────────


def test_project_crud_roundtrip(client: TestClient) -> None:
    """專案的建立、讀取、更新、刪除全流程。"""

    created = _data(client.post("/api/v1/studio/projects", json={"name": "都市甜寵", "genre": "都市"}))
    project_id = created["id"]
    assert created["name"] == "都市甜寵"
    assert created["default_video_ratio"] == "9:16"

    fetched = _data(client.get(f"/api/v1/studio/projects/{project_id}"))
    assert fetched["id"] == project_id

    updated = _data(client.patch(f"/api/v1/studio/projects/{project_id}", json={"description": "改過了"}))
    assert updated["description"] == "改過了"
    assert updated["name"] == "都市甜寵", "未傳入的欄位必須保留原值"

    assert client.delete(f"/api/v1/studio/projects/{project_id}").status_code == 200
    assert client.get(f"/api/v1/studio/projects/{project_id}").status_code == 404


def test_project_archive_is_safe_alternative_to_delete(client: TestClient) -> None:
    """封存後資料仍在，只是狀態改變。"""

    project = _make_project(client)
    archived = _data(client.post(f"/api/v1/studio/projects/{project['id']}/archive"))

    assert archived["status"] == "archived"
    assert client.get(f"/api/v1/studio/projects/{project['id']}").status_code == 200


def test_project_not_found_returns_404_envelope(client: TestClient) -> None:
    """不存在的專案回 404 且使用統一信封。"""

    response = client.get("/api/v1/studio/projects/prj_nope")

    assert response.status_code == 404
    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == "not_found"


def test_project_validation_returns_422(client: TestClient) -> None:
    """空名稱違反驗證規則，回 422 且使用統一信封。"""

    response = client.post("/api/v1/studio/projects", json={"name": ""})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "request_validation_error"


# ── 分頁與搜尋 ────────────────────────────────────────────────────────────────


def test_pagination_meta_is_correct(client: TestClient) -> None:
    """分頁中繼資訊必須反映過濾後的總數。"""

    for i in range(7):
        _make_project(client, f"專案{i}")

    body = _data(client.get("/api/v1/studio/projects", params={"page": 1, "page_size": 3}))

    assert len(body["items"]) == 3
    assert body["meta"]["total"] == 7
    assert body["meta"]["total_pages"] == 3
    assert body["meta"]["page"] == 1


def test_pagination_second_page_returns_remainder(client: TestClient) -> None:
    """最後一頁只回傳剩餘筆數。"""

    for i in range(5):
        _make_project(client, f"專案{i}")

    body = _data(client.get("/api/v1/studio/projects", params={"page": 2, "page_size": 3}))
    assert len(body["items"]) == 2


def test_search_filters_results(client: TestClient) -> None:
    """搜尋必須實際過濾，而非回傳全部。"""

    _make_project(client, "古裝仙俠")
    _make_project(client, "都市職場")

    body = _data(client.get("/api/v1/studio/projects", params={"search": "仙俠"}))

    assert body["meta"]["total"] == 1
    assert body["items"][0]["name"] == "古裝仙俠"


def test_invalid_page_size_returns_422(client: TestClient) -> None:
    """超出上限的 page_size 應被拒絕。"""

    assert client.get("/api/v1/studio/projects", params={"page_size": 9999}).status_code == 422


# ── Chapter CRUD 與關聯 ───────────────────────────────────────────────────────


def test_chapter_crud_and_auto_index(client: TestClient) -> None:
    """章節序號未指定時自動接續。"""

    project = _make_project(client)

    first = _make_chapter(client, project["id"], "第一集")
    second = _make_chapter(client, project["id"], "第二集")

    assert first["index"] == 1
    assert second["index"] == 2
    assert second["project_id"] == project["id"]


def test_duplicate_chapter_index_returns_409(client: TestClient) -> None:
    """同專案內重複章節序號回 409。"""

    project = _make_project(client)
    _make_chapter(client, project["id"])

    response = client.post(
        f"/api/v1/studio/projects/{project['id']}/chapters",
        json={"title": "重複", "index": 1},
    )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "conflict"


def test_chapters_of_missing_project_returns_404(client: TestClient) -> None:
    """列出不存在專案的章節回 404，而非空清單。"""

    assert client.get("/api/v1/studio/projects/prj_nope/chapters").status_code == 404


def test_deleting_project_cascades_to_chapters(client: TestClient) -> None:
    """刪除專案時章節一併消失。"""

    project = _make_project(client)
    chapter = _make_chapter(client, project["id"])

    client.delete(f"/api/v1/studio/projects/{project['id']}")

    assert client.get(f"/api/v1/studio/chapters/{chapter['id']}").status_code == 404


# ── 腳本 ──────────────────────────────────────────────────────────────────────


def test_script_update_and_read(client: TestClient) -> None:
    """腳本可寫入與讀回。"""

    project = _make_project(client)
    chapter = _make_chapter(client, project["id"])

    written = _data(
        client.put(f"/api/v1/studio/chapters/{chapter['id']}/script", json={"raw_text": "第一場 夜 街道"})
    )
    assert written["raw_text"] == "第一場 夜 街道"
    assert written["raw_length"] == len("第一場 夜 街道")

    read_back = _data(client.get(f"/api/v1/studio/chapters/{chapter['id']}/script"))
    assert read_back["raw_text"] == "第一場 夜 街道"


def test_changing_script_clears_condensed_text(client: TestClient) -> None:
    """改寫原文會清空精簡稿，避免兩者失去對應關係。"""

    project = _make_project(client)
    chapter = _make_chapter(client, project["id"])

    client.put(f"/api/v1/studio/chapters/{chapter['id']}/script", json={"raw_text": "原始內容"})
    updated = _data(
        client.put(f"/api/v1/studio/chapters/{chapter['id']}/script", json={"raw_text": "換了內容"})
    )

    assert updated["condensed_text"] == ""


# ── Shot CRUD 與狀態語意 ──────────────────────────────────────────────────────


def test_shot_crud_and_auto_index(client: TestClient) -> None:
    """分鏡序號自動接續，且建立時一併產生細節。"""

    project = _make_project(client)
    chapter = _make_chapter(client, project["id"])

    first = _make_shot(client, chapter["id"], "鏡頭一")
    second = _make_shot(client, chapter["id"], "鏡頭二")

    assert first["index"] == 1
    assert second["index"] == 2
    assert first["status"] == "pending"


def test_shot_status_cannot_be_set_directly(client: TestClient) -> None:
    """`status` 不在可寫欄位中；直接傳入不應生效。"""

    project = _make_project(client)
    chapter = _make_chapter(client, project["id"])
    shot = _make_shot(client, chapter["id"])

    updated = _data(client.patch(f"/api/v1/studio/shots/{shot['id']}", json={"status": "ready"}))
    assert updated["status"] == "pending", "status 必須由系統推導，不可外部寫入"


def test_skip_extraction_makes_shot_ready(client: TestClient) -> None:
    """明確跳過提取後，分鏡狀態應變為 ready。"""

    project = _make_project(client)
    chapter = _make_chapter(client, project["id"])
    shot = _make_shot(client, chapter["id"])

    updated = _data(client.patch(f"/api/v1/studio/shots/{shot['id']}", json={"skip_extraction": True}))
    assert updated["status"] == "ready"


def test_shot_detail_update_preserves_untouched_fields(client: TestClient) -> None:
    """更新細節時未傳入的欄位保留原值。"""

    project = _make_project(client)
    chapter = _make_chapter(client, project["id"])
    shot = _make_shot(client, chapter["id"])

    client.patch(
        f"/api/v1/studio/shots/{shot['id']}",
        json={"detail": {"description": "夜晚的街道", "duration_seconds": 8}},
    )
    updated = _data(client.patch(f"/api/v1/studio/shots/{shot['id']}", json={"detail": {"atmosphere": "冷冽"}}))

    assert updated["detail"]["description"] == "夜晚的街道"
    assert updated["detail"]["duration_seconds"] == 8
    assert updated["detail"]["atmosphere"] == "冷冽"


def test_readiness_separates_confirmation_from_video_ready(client: TestClient) -> None:
    """準備度必須把「已確認」與「可生成影片」分開回報。"""

    project = _make_project(client)
    chapter = _make_chapter(client, project["id"])
    shot = _make_shot(client, chapter["id"])

    client.patch(f"/api/v1/studio/shots/{shot['id']}", json={"skip_extraction": True})
    readiness = _data(client.get(f"/api/v1/studio/shots/{shot['id']}/readiness"))

    assert readiness["status"] == "ready"
    assert readiness["video_ready"] is False, "status=ready 不等於可生成影片"
    assert readiness["blocking_reasons"], "應說明缺少什麼"


def test_duplicate_shot_index_returns_409(client: TestClient) -> None:
    """同章節內重複鏡頭序號回 409。"""

    project = _make_project(client)
    chapter = _make_chapter(client, project["id"])
    _make_shot(client, chapter["id"])

    response = client.post(
        f"/api/v1/studio/chapters/{chapter['id']}/shots",
        json={"title": "重複", "index": 1},
    )
    assert response.status_code == 409


# ── 實體關聯 ──────────────────────────────────────────────────────────────────


def test_character_crud_and_project_scope(client: TestClient) -> None:
    """角色建立於專案範圍內。"""

    project = _make_project(client)

    character = _data(
        client.post(f"/api/v1/studio/projects/{project['id']}/characters", json={"name": "林小雨"})
    )
    assert character["project_id"] == project["id"]

    listed = _data(client.get(f"/api/v1/studio/projects/{project['id']}/characters"))
    assert listed["meta"]["total"] == 1


def test_duplicate_character_name_in_project_returns_409(client: TestClient) -> None:
    """同專案內角色同名回 409，避免資產庫出現分身。"""

    project = _make_project(client)
    client.post(f"/api/v1/studio/projects/{project['id']}/characters", json={"name": "林小雨"})

    response = client.post(f"/api/v1/studio/projects/{project['id']}/characters", json={"name": "林小雨"})
    assert response.status_code == 409


def test_character_name_exists_check(client: TestClient) -> None:
    """名稱查重端點應正確回報。"""

    project = _make_project(client)
    client.post(f"/api/v1/studio/projects/{project['id']}/characters", json={"name": "林小雨"})

    endpoint = "/api/v1/studio/characters/exists/check"
    hit = _data(client.get(endpoint, params={"name": "林小雨", "project_id": project["id"]}))
    miss = _data(client.get(endpoint, params={"name": "不存在", "project_id": project["id"]}))

    assert hit["exists"] is True
    assert miss["exists"] is False


def test_actor_is_not_project_scoped(client: TestClient) -> None:
    """演員可跨專案重複使用，因此不綁定專案。"""

    actor = _data(client.post("/api/v1/studio/actors", json={"name": "演員甲"}))
    assert "project_id" not in actor

    listed = _data(client.get("/api/v1/studio/actors"))
    assert listed["meta"]["total"] == 1


def test_shot_asset_link_roundtrip(client: TestClient) -> None:
    """分鏡可關聯角色、場景、道具、服裝並可解除。"""

    project = _make_project(client)
    chapter = _make_chapter(client, project["id"])
    shot = _make_shot(client, chapter["id"])
    character = _data(
        client.post(f"/api/v1/studio/projects/{project['id']}/characters", json={"name": "林小雨"})
    )

    link = _data(
        client.post(
            f"/api/v1/studio/shots/{shot['id']}/assets",
            json={"asset_kind": "character", "asset_id": character["id"]},
        )
    )
    assert link["asset_id"] == character["id"]

    listed = _data(client.get(f"/api/v1/studio/shots/{shot['id']}/assets"))
    assert len(listed) == 1

    assert (
        client.delete(f"/api/v1/studio/shots/{shot['id']}/assets/character/{character['id']}").status_code == 200
    )
    assert _data(client.get(f"/api/v1/studio/shots/{shot['id']}/assets")) == []


def test_duplicate_shot_asset_link_returns_409(client: TestClient) -> None:
    """重複關聯同一資產回 409。"""

    project = _make_project(client)
    chapter = _make_chapter(client, project["id"])
    shot = _make_shot(client, chapter["id"])
    scene = _data(client.post(f"/api/v1/studio/projects/{project['id']}/scenes", json={"name": "街道"}))

    payload = {"asset_kind": "scene", "asset_id": scene["id"]}
    client.post(f"/api/v1/studio/shots/{shot['id']}/assets", json=payload)

    assert client.post(f"/api/v1/studio/shots/{shot['id']}/assets", json=payload).status_code == 409


def test_unlink_missing_asset_returns_404(client: TestClient) -> None:
    """解除不存在的關聯回 404。"""

    project = _make_project(client)
    chapter = _make_chapter(client, project["id"])
    shot = _make_shot(client, chapter["id"])

    response = client.delete(f"/api/v1/studio/shots/{shot['id']}/assets/prop/prop_nope")
    assert response.status_code == 404


# ── 對白與關鍵幀 ──────────────────────────────────────────────────────────────


def test_dialogue_crud(client: TestClient) -> None:
    """對白可新增、列出、更新與刪除。"""

    project = _make_project(client)
    chapter = _make_chapter(client, project["id"])
    shot = _make_shot(client, chapter["id"])

    dialogue = _data(
        client.post(f"/api/v1/studio/shots/{shot['id']}/dialogues", json={"content": "你來了。"})
    )
    assert dialogue["content"] == "你來了。"

    updated = _data(client.patch(f"/api/v1/studio/dialogues/{dialogue['id']}", json={"emotion": "驚訝"}))
    assert updated["emotion"] == "驚訝"
    assert updated["content"] == "你來了。"

    assert client.delete(f"/api/v1/studio/dialogues/{dialogue['id']}").status_code == 200


def test_frame_crud_and_uniqueness(client: TestClient) -> None:
    """關鍵幀可新增，且同類型同序號重複時回 409。"""

    project = _make_project(client)
    chapter = _make_chapter(client, project["id"])
    shot = _make_shot(client, chapter["id"])

    frame = _data(
        client.post(f"/api/v1/studio/shots/{shot['id']}/frames", json={"frame_type": "first", "index": 0})
    )
    assert frame["frame_type"] == "first"

    duplicate = client.post(
        f"/api/v1/studio/shots/{shot['id']}/frames", json={"frame_type": "first", "index": 0}
    )
    assert duplicate.status_code == 409


# ── Provider 金鑰安全（最高優先）─────────────────────────────────────────────


def test_provider_read_never_exposes_key(client: TestClient, monkeypatch) -> None:
    """ProviderRead 絕不得回傳任何金鑰內容。"""

    monkeypatch.setenv("TEST_PROVIDER_KEY", "sk-super-secret-value-abcdefghijklmnop")

    created = _data(
        client.post(
            "/api/v1/studio/providers",
            json={
                "name": "Anthropic",
                "provider_type": "anthropic",
                "api_key_env": "TEST_PROVIDER_KEY",
                "base_url": "https://api.anthropic.com",
            },
        )
    )

    # 只透露「有設定」，不透露值
    assert created["api_key_configured"] is True
    assert created["api_key_env"] == "TEST_PROVIDER_KEY"

    serialised = json.dumps(created)
    assert "sk-super-secret-value" not in serialised
    for forbidden in ("api_key", "api_secret", "secret", "token", "password"):
        assert forbidden not in created, f"回應不得含 {forbidden} 欄位"


def test_provider_key_absent_reports_not_configured(client: TestClient, monkeypatch) -> None:
    """環境變數未設定時應回報未就緒，而非報錯。"""

    monkeypatch.delenv("MISSING_PROVIDER_KEY", raising=False)

    created = _data(
        client.post(
            "/api/v1/studio/providers",
            json={"name": "OpenAI", "provider_type": "openai", "api_key_env": "MISSING_PROVIDER_KEY"},
        )
    )
    assert created["api_key_configured"] is False


def test_provider_rejects_actual_key_material(client: TestClient) -> None:
    """把真正的金鑰填進 api_key_env 必須被拒絕，避免金鑰寫入資料庫。"""

    response = client.post(
        "/api/v1/studio/providers",
        json={
            "name": "Bad",
            "provider_type": "openai",
            "api_key_env": "sk-this-is-an-actual-secret-key-value",
        },
    )
    assert response.status_code == 422


def test_provider_update_preserves_key_when_omitted(client: TestClient, monkeypatch) -> None:
    """未傳入 api_key_env 時必須保留原值。"""

    monkeypatch.setenv("KEEP_ME", "value")

    created = _data(
        client.post(
            "/api/v1/studio/providers",
            json={"name": "Keeper", "provider_type": "gemini", "api_key_env": "KEEP_ME"},
        )
    )

    updated = _data(client.patch(f"/api/v1/studio/providers/{created['id']}", json={"description": "改說明"}))

    assert updated["api_key_env"] == "KEEP_ME", "未傳入時必須保留原本的金鑰環境變數設定"
    assert updated["description"] == "改說明"


def test_provider_update_changes_key_env_when_explicit(client: TestClient, monkeypatch) -> None:
    """明確傳入新值時才更新。"""

    monkeypatch.setenv("OLD_KEY", "a")
    monkeypatch.setenv("NEW_KEY", "b")

    created = _data(
        client.post(
            "/api/v1/studio/providers",
            json={"name": "Switcher", "provider_type": "openai", "api_key_env": "OLD_KEY"},
        )
    )
    updated = _data(
        client.patch(f"/api/v1/studio/providers/{created['id']}", json={"api_key_env": "NEW_KEY"})
    )

    assert updated["api_key_env"] == "NEW_KEY"


def test_provider_test_endpoint_reports_without_leaking(client: TestClient, monkeypatch) -> None:
    """連線測試只說明缺少什麼，不含金鑰內容。"""

    monkeypatch.delenv("UNSET_KEY", raising=False)

    provider = _data(
        client.post(
            "/api/v1/studio/providers",
            json={"name": "Untested", "provider_type": "openai", "api_key_env": "UNSET_KEY"},
        )
    )
    result = _data(client.post(f"/api/v1/studio/providers/{provider['id']}/test"))

    assert result["ok"] is False
    assert "UNSET_KEY" in result["detail"]


def test_openapi_contains_no_secret_values(client: TestClient, monkeypatch) -> None:
    """OpenAPI 規格不得含任何金鑰值。"""

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-should-never-appear-in-spec")

    spec = json.dumps(client.get("/studio/openapi.json").json())
    assert "sk-ant-should-never-appear-in-spec" not in spec


# ── Model ↔ Provider 關聯 ────────────────────────────────────────────────────


def test_model_requires_existing_provider(client: TestClient) -> None:
    """指定不存在的供應商時回 404，而非資料庫外鍵錯誤。"""

    response = client.post(
        "/api/v1/studio/models",
        json={"provider_id": "prov_nope", "name": "X", "model_id": "x", "category": "text"},
    )
    assert response.status_code == 404


def test_model_provider_relationship(client: TestClient) -> None:
    """模型建立後可依供應商過濾，且刪除供應商會級聯刪除模型。"""

    provider = _data(
        client.post("/api/v1/studio/providers", json={"name": "Anthropic", "provider_type": "anthropic"})
    )
    _data(
        client.post(
            "/api/v1/studio/models",
            json={
                "provider_id": provider["id"],
                "name": "Opus",
                "model_id": "claude-opus-5",
                "category": "text",
            },
        )
    )

    listed = _data(client.get("/api/v1/studio/models", params={"provider_id": provider["id"]}))
    assert listed["meta"]["total"] == 1

    client.delete(f"/api/v1/studio/providers/{provider['id']}")

    after = _data(client.get("/api/v1/studio/models"))
    assert after["meta"]["total"] == 0, "刪除供應商應級聯刪除其模型"


def test_duplicate_model_returns_409(client: TestClient) -> None:
    """同供應商下相同 model_id + category 回 409。"""

    provider = _data(client.post("/api/v1/studio/providers", json={"name": "P", "provider_type": "openai"}))
    payload = {"provider_id": provider["id"], "name": "M", "model_id": "gpt-x", "category": "text"}

    client.post("/api/v1/studio/models", json=payload)
    assert client.post("/api/v1/studio/models", json=payload).status_code == 409


# ── Prompt Template ───────────────────────────────────────────────────────────


def test_prompt_template_default_is_unique_per_category(client: TestClient) -> None:
    """同類別只能有一個預設模板。"""

    first = _data(
        client.post(
            "/api/v1/studio/prompt-templates",
            json={"category": "video_prompt", "name": "A", "is_default": True},
        )
    )
    second = _data(
        client.post(
            "/api/v1/studio/prompt-templates",
            json={"category": "video_prompt", "name": "B", "is_default": True},
        )
    )

    assert second["is_default"] is True
    refreshed = _data(client.get(f"/api/v1/studio/prompt-templates/{first['id']}"))
    assert refreshed["is_default"] is False, "設定新預設時應取消舊預設"


# ── Media ─────────────────────────────────────────────────────────────────────


def test_media_crud_and_url(client: TestClient) -> None:
    """媒體登記後應回傳可讀取的 URL。"""

    created = _data(
        client.post(
            "/api/v1/studio/media",
            json={"file_type": "image", "storage_key": "img/a.png", "filename": "a.png"},
        )
    )

    assert created["url"], "必須回傳可讀取的 URL"
    assert client.get(f"/api/v1/studio/media/{created['id']}").status_code == 200


def test_media_usage_tracking(client: TestClient) -> None:
    """媒體用途可登記與查詢，供刪除前提示。"""

    media = _data(
        client.post("/api/v1/studio/media", json={"file_type": "image", "storage_key": "img/b.png"})
    )
    _data(
        client.post(
            f"/api/v1/studio/media/{media['id']}/usages",
            json={"usage_kind": "shot_frame", "owner_type": "shot", "owner_id": "shot_1"},
        )
    )

    usages = _data(client.get(f"/api/v1/studio/media/{media['id']}/usages"))
    assert len(usages) == 1


def test_media_with_missing_project_returns_404(client: TestClient) -> None:
    """指定不存在的專案時回 404。"""

    response = client.post(
        "/api/v1/studio/media",
        json={"file_type": "image", "storage_key": "img/c.png", "project_id": "prj_nope"},
    )
    assert response.status_code == 404


# ── Generation Task ───────────────────────────────────────────────────────────


def test_task_crud_and_relations(client: TestClient) -> None:
    """任務可建立並帶上業務關聯。"""

    project = _make_project(client)
    chapter = _make_chapter(client, project["id"])
    shot = _make_shot(client, chapter["id"])

    task = _data(
        client.post(
            "/api/v1/studio/tasks",
            json={
                "task_kind": "video_generation",
                "project_id": project["id"],
                "chapter_id": chapter["id"],
                "shot_id": shot["id"],
            },
        )
    )

    assert task["status"] == "pending"
    assert task["progress"] == 0
    assert task["is_cancellable"] is True
    assert task["elapsed_seconds"] is None


def test_task_with_missing_relation_returns_404(client: TestClient) -> None:
    """關聯資源不存在時回 404。"""

    response = client.post(
        "/api/v1/studio/tasks",
        json={"task_kind": "video_generation", "shot_id": "shot_nope"},
    )
    assert response.status_code == 404


def test_task_cancel_is_idempotent(client: TestClient) -> None:
    """重複取消請求視為冪等成功。"""

    task = _data(client.post("/api/v1/studio/tasks", json={"task_kind": "script_divide"}))

    first = _data(client.post(f"/api/v1/studio/tasks/{task['id']}/cancel", json={"reason": "使用者取消"}))
    assert first["cancel_requested"] is True

    second = client.post(f"/api/v1/studio/tasks/{task['id']}/cancel", json={})
    assert second.status_code == 200


def test_deleting_active_task_returns_409(client: TestClient) -> None:
    """進行中的任務不可直接刪除。"""

    task = _data(client.post("/api/v1/studio/tasks", json={"task_kind": "script_divide"}))

    response = client.delete(f"/api/v1/studio/tasks/{task['id']}")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "invalid_state_transition"


def test_task_link_lifecycle(client: TestClient) -> None:
    """任務產物關聯可建立並更新採用狀態。"""

    task = _data(client.post("/api/v1/studio/tasks", json={"task_kind": "frame_image_generation"}))

    link = _data(
        client.post(
            f"/api/v1/studio/tasks/{task['id']}/links",
            json={"resource_type": "image", "relation_type": "shot_frame", "relation_entity_id": "frm_1"},
        )
    )
    assert link["status"] == "todo"

    updated = _data(client.patch(f"/api/v1/studio/task-links/{link['id']}", json={"status": "accepted"}))
    assert updated["status"] == "accepted"


def test_active_tasks_endpoint(client: TestClient) -> None:
    """進行中任務端點只回傳未結束的任務。"""

    client.post("/api/v1/studio/tasks", json={"task_kind": "script_divide"})

    active = _data(client.get("/api/v1/studio/tasks/active"))
    assert len(active) == 1


# ── OpenAPI ───────────────────────────────────────────────────────────────────


def test_openapi_operation_ids_are_unique(client: TestClient) -> None:
    """operationId 必須唯一，否則前端 client 會產生名稱衝突。"""

    spec = client.get("/studio/openapi.json").json()
    ids = [
        detail["operationId"]
        for item in spec["paths"].values()
        for detail in item.values()
        if isinstance(detail, dict) and "operationId" in detail
    ]

    assert len(ids) == len(set(ids)), f"重複的 operationId：{sorted({i for i in ids if ids.count(i) > 1})}"


def test_openapi_has_expected_tags(client: TestClient) -> None:
    """所有主要資源都必須有對應的 tag，前端 client 才會分檔。"""

    spec = client.get("/studio/openapi.json").json()
    tags = {
        tag
        for item in spec["paths"].values()
        for detail in item.values()
        if isinstance(detail, dict)
        for tag in detail.get("tags", [])
    }

    expected = {
        "projects",
        "shots",
        "characters",
        "actors",
        "scenes",
        "props",
        "costumes",
        "media",
        "providers",
        "models",
        "prompt-templates",
        "tasks",
        "health",
    }
    assert expected <= tags, f"缺少 tag：{expected - tags}"


def test_openapi_defines_pagination_and_error_schemas(client: TestClient) -> None:
    """分頁與錯誤 schema 必須出現在規格中。"""

    spec = client.get("/studio/openapi.json").json()
    schemas = spec["components"]["schemas"]

    assert "PageMeta" in schemas
    assert "ErrorBody" in schemas
    assert any(name.startswith("Page_") for name in schemas), "缺少分頁 schema"
    assert any(name.startswith("ApiResponse_") for name in schemas), "缺少回應信封 schema"


def test_exported_openapi_file_matches_app(client: TestClient) -> None:
    """已匯出的 `frontend/openapi.json` 必須與目前 API 一致。

    避免 API 改了但忘記重新產生前端型別 —— 那會讓前端與後端悄悄失去同步。
    """

    exported_path = REPO_ROOT / "frontend" / "openapi.json"
    assert exported_path.exists(), "尚未匯出 OpenAPI，請執行 python -m studio.scripts.export_openapi"

    exported = json.loads(exported_path.read_text(encoding="utf-8"))
    live = client.get("/studio/openapi.json").json()

    assert set(exported["paths"]) == set(live["paths"]), "匯出的 OpenAPI 與目前 API 不一致，請重新匯出"


def test_generated_client_exists() -> None:
    """前端 generated client 必須存在且涵蓋主要資源。"""

    generated = REPO_ROOT / "frontend" / "src" / "services" / "generated"
    assert generated.is_dir(), "尚未產生前端 client"

    services = {path.name for path in (generated / "services").glob("*.ts")}
    for expected in ("ProjectsService.ts", "ShotsService.ts", "ProvidersService.ts", "TasksService.ts"):
        assert expected in services, f"缺少 {expected}"


def test_generated_client_contains_no_secrets() -> None:
    """產生的前端程式碼不得含任何金鑰。

    比對實際的金鑰樣式而非「sk-」子字串 —— 後者會誤判路徑中的
    `task-links` 之類的正常內容。
    """

    import re

    patterns = [
        re.compile(r"sk-[A-Za-z0-9_\-]{16,}"),
        re.compile(r"xoxb-[A-Za-z0-9_\-]{10,}"),
        re.compile(r"ghp_[A-Za-z0-9]{20,}"),
        re.compile(r"AKIA[0-9A-Z]{16}"),
        re.compile(r"AIza[0-9A-Za-z_\-]{30,}"),
    ]

    generated = REPO_ROOT / "frontend" / "src" / "services" / "generated"
    for path in generated.rglob("*.ts"):
        content = path.read_text(encoding="utf-8")
        for pattern in patterns:
            assert not pattern.search(content), f"{path} 疑似含金鑰（樣式 {pattern.pattern}）"
