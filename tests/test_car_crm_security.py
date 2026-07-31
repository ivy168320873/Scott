"""car_crm 安全性與資料完整性回歸測試。

對應 code-reviewer 找出的 4 個 High 與數個 Medium，全部以實際行為斷言。
"""
from __future__ import annotations

import sqlite3
import sys
import threading
from pathlib import Path

import pytest

CRM_DIR = Path(__file__).resolve().parent.parent / "car_crm"
if str(CRM_DIR) not in sys.path:
    sys.path.insert(0, str(CRM_DIR))

import app as crm_app        # noqa: E402
import db as crm_db          # noqa: E402
import service as svc        # noqa: E402


# ── High #1：非有限數值不得造成 500 ──────────────────────────────────────────

@pytest.fixture
def con(tmp_path):
    path = str(tmp_path / "sec.db")
    crm_db.init_db(path)
    connection = crm_db.connect(path)
    yield connection
    connection.close()


@pytest.mark.parametrize("bad", ["inf", "-inf", "Infinity", "1e400", "nan", "NaN"])
def test_non_finite_numbers_become_none_not_overflow(con, bad):
    """回歸測試：int(float('inf')) 會拋 OverflowError 讓請求 500。"""
    cid = svc.create_customer(con, {"name": "測試", "budget_max": bad})
    assert svc.get_customer(con, cid)["budget_max"] is None

    svc.add_trade_in(con, cid, {"year": bad, "mileage": bad, "estimated": bad})
    row = svc.customer_trade_ins(con, cid)[0]
    assert row["year"] is None and row["mileage"] is None
    assert row["estimated"] == 0.0

    svc.add_test_drive(con, cid, {"rating": bad})
    assert svc.customer_test_drives(con, cid)[0]["rating"] is None


# ── High #2：報價版本並行安全 ────────────────────────────────────────────────

def test_quote_versions_unique_under_concurrency(tmp_path):
    """回歸測試：SELECT MAX+1 再 INSERT 曾在並行下產生四筆 v1，
    使「最新」標籤貼到錯誤版本，業務會報出錯誤價格。"""
    path = str(tmp_path / "race.db")
    crm_db.init_db(path)
    setup = crm_db.connect(path)
    cid = svc.create_customer(setup, {"name": "並行測試"})
    setup.close()

    errors: list[Exception] = []
    barrier = threading.Barrier(4)

    def worker():
        connection = crm_db.connect(path)
        connection.execute("PRAGMA busy_timeout = 5000")
        try:
            barrier.wait(timeout=5)
            svc.add_quote(connection, cid, {"list_price": "800000"})
        except Exception as exc:            # noqa: BLE001 - 收集後於主執行緒斷言
            errors.append(exc)
        finally:
            connection.close()

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert not errors, f"並行新增報價發生例外：{errors}"
    check = crm_db.connect(path)
    versions = [q["version"] for q in svc.customer_quotes(check, cid)]
    check.close()
    assert sorted(versions) == [1, 2, 3, 4], f"版本號重複或遺漏：{versions}"


def test_quote_unique_constraint_exists(con):
    """DB 層必須有唯一約束，不能只靠應用層邏輯。"""
    cid = svc.create_customer(con, {"name": "約束測試"})
    svc.add_quote(con, cid, {"list_price": "1"})
    with pytest.raises(sqlite3.IntegrityError):
        con.execute(
            """INSERT INTO quotes (customer_id, version, model, list_price,
                                   discount, accessories, final_price, note, created_at)
               VALUES (?,1,'',0,0,0,0,'','2026-01-01')""",
            (cid,),
        )


# ── High #3：待辦日期格式驗證 ────────────────────────────────────────────────

@pytest.mark.parametrize("bad_date", ["明天下午", "2026-13-45", "26/07/30", "下週一", "abc"])
def test_invalid_due_date_is_rejected(con, bad_date):
    """回歸測試：非法日期會被寫入，然後永遠不出現在今日／逾期清單，
    待辦被靜默吞掉——這使核心需求「今日跟進與逾期待辦」失效。"""
    cid = svc.create_customer(con, {"name": "測試"})
    with pytest.raises(ValueError) as exc:
        svc.add_follow_up(con, cid, bad_date, "回電")
    assert "YYYY-MM-DD" in str(exc.value)


def test_invalid_drive_date_is_rejected(con):
    cid = svc.create_customer(con, {"name": "測試"})
    with pytest.raises(ValueError):
        svc.add_test_drive(con, cid, {"drive_date": "上週三"})


def test_valid_date_is_normalised(con):
    cid = svc.create_customer(con, {"name": "測試"})
    svc.add_follow_up(con, cid, "  2026-03-05  ", "回電")
    row = con.execute("SELECT due_date FROM follow_ups").fetchone()
    assert row["due_date"] == "2026-03-05"


def test_blank_date_still_defaults_to_today(con):
    cid = svc.create_customer(con, {"name": "測試"})
    svc.add_follow_up(con, cid, "", "回電")
    row = con.execute("SELECT due_date FROM follow_ups").fetchone()
    assert row["due_date"] == crm_db.today_str()


# ── LIKE 萬用字元跳脫 ────────────────────────────────────────────────────────

def test_like_wildcards_are_escaped(con):
    svc.create_customer(con, {"name": "甲客"})
    svc.create_customer(con, {"name": "乙客"})
    assert len(svc.list_customers(con, keyword="%")) == 0
    assert len(svc.list_customers(con, keyword="_")) == 0
    assert len(svc.list_customers(con, keyword="甲")) == 1


# ── 完成待辦推進到「已聯繫」 ─────────────────────────────────────────────────

def test_completing_follow_up_advances_to_contacted(con):
    cid = svc.create_customer(con, {"name": "測試"})
    fid = svc.add_follow_up(con, cid, crm_db.today_str(), "回電")
    svc.complete_follow_up(con, fid)
    assert svc.get_customer(con, cid)["stage"] == "CONTACTED"


def test_completing_follow_up_does_not_regress_advanced_customer(con):
    cid = svc.create_customer(con, {"name": "測試"})
    svc.update_customer(con, cid, {"stage": "NEGOTIATING"})
    fid = svc.add_follow_up(con, cid, crm_db.today_str(), "回電")
    svc.complete_follow_up(con, fid)
    assert svc.get_customer(con, cid)["stage"] == "NEGOTIATING"


# ── High #4 / Medium：HTTP 層安全 ────────────────────────────────────────────

@pytest.fixture
def secure_client(tmp_path):
    """啟用存取碼的 app。"""
    flask_app = crm_app.create_app(str(tmp_path / "auth.db"), access_code="s3cret")
    flask_app.config.update(TESTING=True)
    with flask_app.test_client() as client:
        yield client


@pytest.fixture
def open_client(tmp_path):
    flask_app = crm_app.create_app(str(tmp_path / "open.db"), access_code="")
    flask_app.config.update(TESTING=True)
    with flask_app.test_client() as client:
        yield client


def _csrf(client) -> str:
    client.get("/login")
    with client.session_transaction() as sess:
        return sess["csrf"]


def test_protected_pages_redirect_to_login(secure_client):
    for path in ("/", "/customers", "/customers/new"):
        response = secure_client.get(path)
        assert response.status_code == 302
        assert "/login" in response.headers["Location"]


def test_login_with_correct_code_grants_access(secure_client):
    secure_client.post("/login", data={"code": "s3cret", "_csrf": _csrf(secure_client)})
    assert secure_client.get("/").status_code == 200


def test_login_with_wrong_code_is_rejected(secure_client):
    response = secure_client.post(
        "/login", data={"code": "wrong", "_csrf": _csrf(secure_client)})
    assert response.status_code == 401
    assert secure_client.get("/").status_code == 302


def test_pwa_assets_stay_public(secure_client):
    """manifest 與 sw 不需登入，否則加入主畫面會失敗。"""
    for path in ("/manifest.json", "/sw.js"):
        response = secure_client.get(path)
        try:
            assert response.status_code == 200
        finally:
            response.close()


def test_post_without_csrf_token_is_rejected(open_client):
    response = open_client.post("/customers/new", data={"name": "無 token"})
    assert response.status_code == 403


def test_post_with_wrong_csrf_token_is_rejected(open_client):
    open_client.get("/")
    response = open_client.post("/customers/new",
                                data={"name": "壞 token", "_csrf": "bogus"})
    assert response.status_code == 403


def test_open_redirect_is_blocked(open_client):
    """回歸測試：next 參數未驗證，可導向外部釣魚站。"""
    open_client.get("/")
    with open_client.session_transaction() as sess:
        token = sess["csrf"]
    response = open_client.post("/follow-ups/1/done",
                                data={"next": "https://evil.example.com/phish",
                                      "_csrf": token})
    assert response.status_code == 302
    assert response.headers["Location"] in ("/", "http://localhost/")
    assert "evil.example.com" not in response.headers["Location"]


def test_invalid_stage_reports_failure_not_success(open_client):
    """回歸測試：無效 stage 被 service 靜默跳過，卻回報「已更新」。"""
    open_client.get("/")
    with open_client.session_transaction() as sess:
        token = sess["csrf"]
    created = open_client.post("/customers/new", data={"name": "測試", "_csrf": token})
    cid = int(created.headers["Location"].rstrip("/").split("/")[-1])

    open_client.post(f"/customers/{cid}/stage", data={"stage": "BOGUS", "_csrf": token})
    body = open_client.get(f"/customers/{cid}").get_data(as_text=True)
    assert "未變更" in body
    assert "新客戶" in body


def test_completing_missing_follow_up_reports_failure(open_client):
    open_client.get("/")
    with open_client.session_transaction() as sess:
        token = sess["csrf"]
    open_client.post("/follow-ups/99999/done", data={"_csrf": token})
    assert "找不到這筆待辦" in open_client.get("/").get_data(as_text=True)


def test_stage_update_on_missing_customer_returns_404(open_client):
    open_client.get("/")
    with open_client.session_transaction() as sess:
        token = sess["csrf"]
    response = open_client.post("/customers/99999/stage",
                                data={"stage": "WON", "_csrf": token})
    assert response.status_code == 404


def test_invalid_due_date_via_http_shows_error_not_500(open_client):
    open_client.get("/")
    with open_client.session_transaction() as sess:
        token = sess["csrf"]
    created = open_client.post("/customers/new", data={"name": "測試", "_csrf": token})
    cid = int(created.headers["Location"].rstrip("/").split("/")[-1])

    response = open_client.post(f"/customers/{cid}/follow-ups",
                                data={"content": "回電", "due_date": "明天",
                                      "_csrf": token}, follow_redirects=True)
    assert response.status_code == 200
    assert "YYYY-MM-DD" in response.get_data(as_text=True)


def test_infinite_number_via_http_does_not_500(open_client):
    open_client.get("/")
    with open_client.session_transaction() as sess:
        token = sess["csrf"]
    created = open_client.post("/customers/new", data={"name": "測試", "_csrf": token})
    cid = int(created.headers["Location"].rstrip("/").split("/")[-1])

    response = open_client.post(f"/customers/{cid}/trade-ins",
                                data={"year": "inf", "mileage": "1e400",
                                      "estimated": "inf", "_csrf": token})
    assert response.status_code == 302          # 正常導回，不是 500


def test_customer_pages_are_not_cached(open_client):
    """含姓名手機的頁面不得被瀏覽器寫入磁碟快取。"""
    response = open_client.get("/")
    assert response.headers.get("Cache-Control") == "no-store"
    assert response.headers.get("X-Frame-Options") == "DENY"
    assert response.headers.get("Referrer-Policy") == "same-origin"
