"""car_crm 商業邏輯測試（不啟動 HTTP，直接測 service 層）。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

CRM_DIR = Path(__file__).resolve().parent.parent / "car_crm"
if str(CRM_DIR) not in sys.path:
    sys.path.insert(0, str(CRM_DIR))

import db as crm_db          # noqa: E402
import service as svc        # noqa: E402


@pytest.fixture
def con(tmp_path):
    """每個測試一個乾淨的暫存 DB，不碰正式的 car_crm.db。"""
    path = str(tmp_path / "test.db")
    crm_db.init_db(path)
    connection = crm_db.connect(path)
    yield connection
    connection.close()


def _customer(con, **kwargs) -> int:
    data = {"name": "王小明", "phone": "0912345678"}
    data.update(kwargs)
    return svc.create_customer(con, data)


# ── 客戶 ─────────────────────────────────────────────────────────────────────

def test_create_and_get_customer(con):
    cid = _customer(con, want_model="Altis", budget_max="800000")
    row = svc.get_customer(con, cid)
    assert row["name"] == "王小明"
    assert row["want_model"] == "Altis"
    assert row["budget_max"] == 800000.0
    assert row["stage"] == "NEW"


def test_create_customer_requires_name(con):
    with pytest.raises(ValueError):
        svc.create_customer(con, {"name": "   "})


def test_create_customer_rejects_unknown_stage(con):
    cid = svc.create_customer(con, {"name": "測試", "stage": "BOGUS"})
    assert svc.get_customer(con, cid)["stage"] == "NEW"


def test_invalid_budget_becomes_none_not_crash(con):
    """表單可能送來空字串或亂填的文字，不得讓整筆建立失敗。"""
    cid = _customer(con, budget_min="", budget_max="不知道")
    row = svc.get_customer(con, cid)
    assert row["budget_min"] is None
    assert row["budget_max"] is None


def test_get_missing_customer_returns_none(con):
    assert svc.get_customer(con, 9999) is None


def test_update_customer(con):
    cid = _customer(con)
    assert svc.update_customer(con, cid, {"phone": "0900000000"}) is True
    assert svc.get_customer(con, cid)["phone"] == "0900000000"


def test_update_missing_customer_returns_false(con):
    assert svc.update_customer(con, 9999, {"phone": "1"}) is False


def test_update_cannot_blank_out_name(con):
    cid = _customer(con)
    svc.update_customer(con, cid, {"name": "  "})
    assert svc.get_customer(con, cid)["name"] == "王小明"


def test_list_customers_filters_by_stage(con):
    _customer(con, name="A")
    b = _customer(con, name="B")
    svc.update_customer(con, b, {"stage": "WON"})
    assert [r["name"] for r in svc.list_customers(con, stage="WON")] == ["B"]


def test_list_customers_keyword_matches_name_phone_model(con):
    _customer(con, name="陳大文", phone="0987654321", want_model="RAV4")
    assert len(svc.list_customers(con, keyword="大文")) == 1
    assert len(svc.list_customers(con, keyword="0987")) == 1
    assert len(svc.list_customers(con, keyword="RAV")) == 1
    assert len(svc.list_customers(con, keyword="不存在")) == 0


# ── 跟進待辦 ─────────────────────────────────────────────────────────────────

def test_add_follow_up_requires_content(con):
    cid = _customer(con)
    with pytest.raises(ValueError):
        svc.add_follow_up(con, cid, "2026-01-01", "  ")


def test_blank_due_date_defaults_to_today(con):
    cid = _customer(con)
    fid = svc.add_follow_up(con, cid, "", "回電")
    row = con.execute("SELECT due_date FROM follow_ups WHERE id = ?", (fid,)).fetchone()
    assert row["due_date"] == crm_db.today_str()


def test_due_follow_ups_splits_today_and_overdue(con):
    cid = _customer(con)
    svc.add_follow_up(con, cid, "2000-01-01", "很久以前就該打")
    svc.add_follow_up(con, cid, crm_db.today_str(), "今天要打")
    svc.add_follow_up(con, cid, "2999-12-31", "很久以後")

    due = svc.due_follow_ups(con)
    assert [f["content"] for f in due["overdue"]] == ["很久以前就該打"]
    assert [f["content"] for f in due["today"]] == ["今天要打"]


def test_completed_follow_up_leaves_due_list(con):
    cid = _customer(con)
    fid = svc.add_follow_up(con, cid, crm_db.today_str(), "今天要打")
    assert len(svc.due_follow_ups(con)["today"]) == 1
    assert svc.complete_follow_up(con, fid) is True
    assert len(svc.due_follow_ups(con)["today"]) == 0


def test_complete_follow_up_is_idempotent(con):
    cid = _customer(con)
    fid = svc.add_follow_up(con, cid, crm_db.today_str(), "x")
    assert svc.complete_follow_up(con, fid) is True
    assert svc.complete_follow_up(con, fid) is False      # 第二次不再變更


def test_upcoming_excludes_today_and_overdue(con):
    cid = _customer(con)
    svc.add_follow_up(con, cid, "2000-01-01", "逾期")
    svc.add_follow_up(con, cid, crm_db.today_str(), "今天")
    svc.add_follow_up(con, cid, "2999-12-31", "未來")
    assert [f["content"] for f in svc.upcoming_follow_ups(con)] == ["未來"]


# ── 試乘 ─────────────────────────────────────────────────────────────────────

def test_add_test_drive_advances_stage(con):
    cid = _customer(con)
    svc.add_test_drive(con, cid, {"model": "Altis", "rating": "5"})
    assert svc.get_customer(con, cid)["stage"] == "TEST_DRIVE"


def test_test_drive_rating_is_clamped(con):
    cid = _customer(con)
    svc.add_test_drive(con, cid, {"rating": "99"})
    svc.add_test_drive(con, cid, {"rating": "-3"})
    ratings = [r["rating"] for r in svc.customer_test_drives(con, cid)]
    assert set(ratings) == {5, 1}


def test_test_drive_invalid_rating_becomes_none(con):
    cid = _customer(con)
    svc.add_test_drive(con, cid, {"rating": "很好"})
    assert svc.customer_test_drives(con, cid)[0]["rating"] is None


def test_test_drive_does_not_regress_won_customer(con):
    """已成交客戶不得因為補登試乘紀錄而被打回試乘階段。"""
    cid = _customer(con)
    svc.update_customer(con, cid, {"stage": "WON"})
    svc.add_test_drive(con, cid, {})
    assert svc.get_customer(con, cid)["stage"] == "WON"


# ── 報價 ─────────────────────────────────────────────────────────────────────

def test_quote_versions_increment_per_customer(con):
    a = _customer(con, name="A")
    b = _customer(con, name="B")
    svc.add_quote(con, a, {"list_price": "800000"})
    svc.add_quote(con, a, {"list_price": "790000"})
    svc.add_quote(con, b, {"list_price": "600000"})

    assert [q["version"] for q in svc.customer_quotes(con, a)] == [2, 1]
    assert [q["version"] for q in svc.customer_quotes(con, b)] == [1]


def test_quote_history_is_preserved_not_overwritten(con):
    cid = _customer(con)
    svc.add_quote(con, cid, {"list_price": "800000", "note": "第一版"})
    svc.add_quote(con, cid, {"list_price": "780000", "note": "第二版"})
    quotes = svc.customer_quotes(con, cid)
    assert len(quotes) == 2
    assert {q["note"] for q in quotes} == {"第一版", "第二版"}


def test_final_price_is_auto_computed_when_blank(con):
    cid = _customer(con)
    svc.add_quote(con, cid, {"list_price": "800000", "discount": "50000",
                             "accessories": "20000", "final_price": ""})
    assert svc.customer_quotes(con, cid)[0]["final_price"] == 770000.0


def test_explicit_final_price_overrides_calculation(con):
    cid = _customer(con)
    svc.add_quote(con, cid, {"list_price": "800000", "discount": "50000",
                             "final_price": "735000"})
    assert svc.customer_quotes(con, cid)[0]["final_price"] == 735000.0


def test_quote_advances_stage(con):
    cid = _customer(con)
    svc.add_quote(con, cid, {"list_price": "800000"})
    assert svc.get_customer(con, cid)["stage"] == "QUOTED"


# ── 舊車估價 ─────────────────────────────────────────────────────────────────

def test_add_trade_in(con):
    cid = _customer(con)
    svc.add_trade_in(con, cid, {"brand": "Toyota", "model": "Altis",
                                "year": "2018", "mileage": "85000",
                                "condition": "良", "estimated": "300000"})
    row = svc.customer_trade_ins(con, cid)[0]
    assert row["brand"] == "Toyota"
    assert row["year"] == 2018
    assert row["estimated"] == 300000.0


def test_trade_in_invalid_numbers_do_not_crash(con):
    cid = _customer(con)
    svc.add_trade_in(con, cid, {"year": "民國100年", "mileage": "", "estimated": "談"})
    row = svc.customer_trade_ins(con, cid)[0]
    assert row["year"] is None and row["mileage"] is None
    assert row["estimated"] == 0.0


# ── 儀表板 ───────────────────────────────────────────────────────────────────

def test_dashboard_on_empty_database(con):
    d = svc.dashboard(con)
    assert d["total_customers"] == 0
    assert d["overdue"] == [] and d["due_today"] == []
    assert d["hot"] == []
    assert d["win_rate"] is None          # 無結案客戶時不得顯示 0%


def test_win_rate_counts_only_closed_customers(con):
    for name in ("A", "B", "C", "D"):
        _customer(con, name=name)
    rows = svc.list_customers(con)
    svc.update_customer(con, rows[0]["id"], {"stage": "WON"})
    svc.update_customer(con, rows[1]["id"], {"stage": "LOST"})
    # 另外兩位仍在進行中，不列入分母
    d = svc.dashboard(con)
    assert d["won_count"] == 1 and d["lost_count"] == 1
    assert d["win_rate"] == 50.0


def test_hot_customers_exclude_closed_deals(con):
    won = _customer(con, name="已成交")
    svc.update_customer(con, won, {"stage": "WON"})
    lost = _customer(con, name="已戰敗")
    svc.update_customer(con, lost, {"stage": "LOST"})
    _customer(con, name="進行中")

    names = [h["customer"]["name"] for h in svc.hot_customers(con)]
    assert names == ["進行中"]


def test_hot_score_ranks_engaged_customer_higher(con):
    cold = _customer(con, name="只留資料")
    warm = _customer(con, name="試乘又報價", buy_timing="一個月內")
    svc.add_test_drive(con, warm, {})
    svc.add_quote(con, warm, {"list_price": "800000"})

    ranked = svc.hot_customers(con)
    assert ranked[0]["customer"]["name"] == "試乘又報價"
    scores = {h["customer"]["id"]: h["score"] for h in ranked}
    assert scores[warm] > scores[cold]


def test_hot_score_is_bounded(con):
    cid = _customer(con, buy_timing="一個月內")
    svc.update_customer(con, cid, {"stage": "NEGOTIATING"})
    for _ in range(5):
        svc.add_test_drive(con, cid, {})
        svc.add_quote(con, cid, {"list_price": "1"})
    svc.update_customer(con, cid, {"stage": "NEGOTIATING"})
    assert all(0 <= h["score"] <= 100 for h in svc.hot_customers(con))


def test_stage_funnel_covers_all_stages(con):
    _customer(con)
    funnel = svc.stage_funnel(con)
    assert [f["key"] for f in funnel] == crm_db.STAGE_KEYS
    assert sum(f["count"] for f in funnel) == 1


def test_dashboard_today_matches_taipei_date(con):
    assert svc.dashboard(con)["today"] == crm_db.today_str()
