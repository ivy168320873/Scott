"""car_crm HTTP 路由測試（Flask test client，不開實際連接埠）。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

CRM_DIR = Path(__file__).resolve().parent.parent / "car_crm"
if str(CRM_DIR) not in sys.path:
    sys.path.insert(0, str(CRM_DIR))

import app as crm_app        # noqa: E402
import db as crm_db          # noqa: E402


@pytest.fixture
def client(tmp_path):
    """未設定存取碼＝單機模式，不強制登入（登入流程另由專屬測試涵蓋）。"""
    flask_app = crm_app.create_app(str(tmp_path / "route_test.db"), access_code="")
    flask_app.config.update(TESTING=True)
    with flask_app.test_client() as test_client:
        yield test_client


def _csrf(client) -> str:
    """取得本 session 的 CSRF token。

    不停用 CSRF——測試必須走與真實瀏覽器相同的路徑，
    否則會掩蓋「表單忘了帶 token」這類缺陷。
    """
    client.get("/")                       # 觸發 token 產生
    with client.session_transaction() as sess:
        return sess["csrf"]


def _post(client, path, data=None, **kwargs):
    payload = dict(data or {})
    payload.setdefault("_csrf", _csrf(client))
    return client.post(path, data=payload, **kwargs)


def _create(client, name="王小明", **extra):
    data = {"name": name, "phone": "0912345678"}
    data.update(extra)
    response = _post(client, "/customers/new", data, follow_redirects=False)
    assert response.status_code == 302
    return int(response.headers["Location"].rstrip("/").split("/")[-1])


def _text(response) -> str:
    return response.get_data(as_text=True)


# ── 首頁 ─────────────────────────────────────────────────────────────────────

def test_dashboard_loads_on_empty_db(client):
    response = client.get("/")
    assert response.status_code == 200
    body = _text(response)
    assert "今天該聯絡誰" in body
    assert "成交進度" in body


def test_dashboard_is_traditional_chinese_and_mobile_ready(client):
    body = _text(client.get("/"))
    assert 'lang="zh-TW"' in body
    assert "width=device-width" in body
    assert "apple-mobile-web-app-capable" in body


def test_dashboard_lists_today_follow_up(client):
    cid = _create(client)
    _post(client, f"/customers/{cid}/follow-ups",
                data={"content": "回電確認交車", "due_date": crm_db.today_str()})
    body = _text(client.get("/"))
    assert "回電確認交車" in body
    assert "王小明" in body


def test_dashboard_marks_overdue_follow_up(client):
    cid = _create(client)
    _post(client, f"/customers/{cid}/follow-ups",
                data={"content": "早該聯絡", "due_date": "2000-01-01"})
    body = _text(client.get("/"))
    assert "逾期未跟進" in body
    assert "早該聯絡" in body


def test_dashboard_shows_tel_link_for_phone(client):
    cid = _create(client)
    _post(client, f"/customers/{cid}/follow-ups",
                data={"content": "打電話", "due_date": crm_db.today_str()})
    assert 'href="tel:0912345678"' in _text(client.get("/"))


# ── 客戶 CRUD ────────────────────────────────────────────────────────────────

def test_create_customer_and_view_detail(client):
    cid = _create(client, want_model="Altis", budget_max="800000")
    body = _text(client.get(f"/customers/{cid}"))
    assert "王小明" in body
    assert "Altis" in body


def test_create_customer_without_name_returns_400(client):
    response = _post(client, "/customers/new", data={"name": ""})
    assert response.status_code == 400
    assert "客戶姓名為必填" in _text(response)


def test_customer_list_and_search(client):
    _create(client, name="陳大文", want_model="RAV4")
    _create(client, name="林小華", want_model="Altis")

    assert "陳大文" in _text(client.get("/customers"))
    hit = _text(client.get("/customers?q=RAV4"))
    assert "陳大文" in hit and "林小華" not in hit


def test_customer_list_filter_by_stage(client):
    # 名字刻意避開介面用語（「新客戶」「已成交」等篩選標籤會造成子字串誤判）
    cid = _create(client, name="阿甲")
    _post(client, f"/customers/{cid}/stage", data={"stage": "WON"})
    _create(client, name="阿乙")

    won = _text(client.get("/customers?stage=WON"))
    assert "阿甲" in won and "阿乙" not in won


def test_edit_customer(client):
    cid = _create(client)
    _post(client, f"/customers/{cid}/edit",
                data={"name": "王小明", "phone": "0900111222"})
    assert "0900111222" in _text(client.get(f"/customers/{cid}"))


def test_update_stage(client):
    cid = _create(client)
    _post(client, f"/customers/{cid}/stage", data={"stage": "NEGOTIATING"})
    assert "議價中" in _text(client.get(f"/customers/{cid}"))


def test_missing_customer_returns_404(client):
    assert client.get("/customers/99999").status_code == 404


# ── 子紀錄 ───────────────────────────────────────────────────────────────────

def test_add_follow_up_and_complete(client):
    cid = _create(client)
    _post(client, f"/customers/{cid}/follow-ups",
                data={"content": "回電", "due_date": crm_db.today_str()})
    body = _text(client.get(f"/customers/{cid}"))
    assert "回電" in body

    follow_up_id = 1
    _post(client, f"/follow-ups/{follow_up_id}/done",
                data={"next": f"/customers/{cid}"})
    assert "已完成" in _text(client.get(f"/customers/{cid}"))


def test_add_test_drive_shows_and_advances_stage(client):
    cid = _create(client)
    _post(client, f"/customers/{cid}/test-drives",
                data={"model": "Altis", "rating": "4", "feedback": "喜歡動力"})
    body = _text(client.get(f"/customers/{cid}"))
    assert "喜歡動力" in body
    assert "已試乘" in body


def test_add_quote_creates_versions(client):
    cid = _create(client)
    _post(client, f"/customers/{cid}/quotes",
                data={"model": "Altis", "list_price": "800000", "discount": "50000"})
    _post(client, f"/customers/{cid}/quotes",
                data={"model": "Altis", "list_price": "800000", "discount": "70000"})
    body = _text(client.get(f"/customers/{cid}"))
    assert "v2" in body and "v1" in body
    assert "730,000" in body          # 800000 - 70000 自動計算


def test_add_trade_in(client):
    cid = _create(client)
    _post(client, f"/customers/{cid}/trade-ins",
                data={"brand": "Toyota", "model": "Altis", "year": "2018",
                      "mileage": "85000", "condition": "良", "estimated": "300000"})
    body = _text(client.get(f"/customers/{cid}"))
    assert "Toyota" in body and "300,000" in body


@pytest.mark.parametrize("path", [
    "/customers/99999/follow-ups",
    "/customers/99999/test-drives",
    "/customers/99999/quotes",
    "/customers/99999/trade-ins",
])
def test_sub_records_reject_unknown_customer(client, path):
    """不存在的客戶 ID 必須回 404，不得建立孤兒資料。"""
    assert _post(client, path, data={"content": "x"}).status_code == 404


# ── PWA ──────────────────────────────────────────────────────────────────────

def test_manifest_is_served_with_correct_fields(client):
    response = client.get("/manifest.json")
    try:
        assert response.status_code == 200
        payload = response.get_json(force=True)
        assert payload["display"] == "standalone"
        assert payload["start_url"] == "/"
        assert payload["lang"] == "zh-TW"
    finally:
        response.close()          # send_from_directory 會開檔，需明確關閉


def test_service_worker_served_from_root_scope(client):
    """SW 必須由根路徑提供，否則作用範圍會被限制在 /static/。"""
    response = client.get("/sw.js")
    try:
        assert response.status_code == 200
        assert response.headers.get("Service-Worker-Allowed") == "/"
    finally:
        response.close()


def test_icon_is_available(client):
    response = client.get("/static/icon.svg")
    try:
        assert response.status_code == 200
    finally:
        response.close()


def test_pages_register_service_worker(client):
    assert "serviceWorker" in _text(client.get("/"))
