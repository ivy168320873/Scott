"""sell_engine：賣出決策的優先序、邊界條件與異常輸入測試。"""
from __future__ import annotations

import pytest
import sell_engine
from conftest import INF, NAN, flat_series, make_ohlcv


PRIORITY = ["STOP_LOSS", "SELL", "ROTATE", "TRIM", "WATCH", "NONE"]


# ── 無效輸入 ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("cost", [0, -1, -100.0])
def test_nonpositive_cost_returns_not_ok(cost):
    result = sell_engine.calc_sell_decision(make_ohlcv(flat_series(30)), cost=cost)
    assert result["ok"] is False
    assert result["decision"] == "NONE"


@pytest.mark.parametrize("cost", [NAN, INF, -INF])
def test_non_finite_cost_returns_not_ok(cost):
    """回歸測試：NaN <= 0 為 False，曾讓 NaN 成本通過檢查並回報 ok=True。"""
    result = sell_engine.calc_sell_decision(make_ohlcv(flat_series(30)), cost=cost)
    assert result["ok"] is False


@pytest.mark.parametrize("cost", [None, "abc", [], {}])
def test_non_numeric_cost_returns_not_ok(cost):
    result = sell_engine.calc_sell_decision(make_ohlcv(flat_series(30)), cost=cost)
    assert result["ok"] is False


def test_numeric_string_cost_is_accepted():
    result = sell_engine.calc_sell_decision(make_ohlcv(flat_series(30)), cost="100")
    assert result["ok"] is True


def test_too_few_closes_returns_not_ok():
    result = sell_engine.calc_sell_decision(make_ohlcv([10.0] * 4), cost=10.0)
    assert result["ok"] is False


def test_exactly_five_closes_is_enough():
    result = sell_engine.calc_sell_decision(make_ohlcv([10.0] * 5), cost=10.0)
    assert result["ok"] is True


def test_empty_ohlcv_returns_not_ok(empty_ohlcv):
    assert sell_engine.calc_sell_decision(empty_ohlcv, cost=10.0)["ok"] is False


# ── 決策邏輯 ──────────────────────────────────────────────────────────────────

def test_flat_at_cost_triggers_no_action():
    result = sell_engine.calc_sell_decision(make_ohlcv(flat_series(30, 100.0)), cost=100.0)
    assert result["decision"] == "NONE"


def test_stop_loss_takes_top_priority():
    """跌破停損價時，即使其他條件也成立，仍必須回 STOP_LOSS。"""
    closes = flat_series(30, 100.0)[:-1] + [85.0]   # 成本 100，-15% < -8% 停損
    result = sell_engine.calc_sell_decision(make_ohlcv(closes), cost=100.0)
    assert result["decision"] == "STOP_LOSS"
    assert result["score"] == 100
    assert "停損觸發" in result["warning_flags"]


def test_stop_loss_boundary_exactly_at_stop_price():
    """現價恰等於停損價應觸發（條件為 <=）。"""
    closes = flat_series(30, 100.0)[:-1] + [92.0]   # 100 * (1 - 8%) = 92
    result = sell_engine.calc_sell_decision(make_ohlcv(closes), cost=100.0, stop_pct=8.0)
    assert result["decision"] == "STOP_LOSS"


def test_just_above_stop_price_does_not_trigger_stop_loss():
    closes = flat_series(30, 100.0)[:-1] + [92.5]
    result = sell_engine.calc_sell_decision(make_ohlcv(closes), cost=100.0, stop_pct=8.0)
    assert result["decision"] != "STOP_LOSS"


def test_trailing_stop_triggers_sell_after_gains():
    closes = flat_series(20, 100.0) + [150.0] * 5 + [120.0]   # 高點 150，回落 20%
    result = sell_engine.calc_sell_decision(make_ohlcv(closes), cost=100.0)
    assert result["decision"] in ("SELL", "STOP_LOSS")


def test_profit_target_trims_when_sustained():
    """連續 >= 2 日站上獲利目標 → TRIM（分批獲利了結）。"""
    closes = flat_series(25, 100.0) + [125.0] * 5
    result = sell_engine.calc_sell_decision(make_ohlcv(closes), cost=100.0)
    assert result["decision"] == "TRIM"


def test_profit_target_only_one_day_above_is_watch_not_trim():
    """僅 1 日站上目標 → WATCH。與上一個測試成對，把 days_above >= 2 這條分支釘住。"""
    closes = flat_series(29, 100.0) + [125.0]
    result = sell_engine.calc_sell_decision(make_ohlcv(closes), cost=100.0)
    assert result["decision"] == "WATCH"


def test_time_stop_rotates_when_long_held_and_losing():
    closes = flat_series(30, 95.0)
    result = sell_engine.calc_sell_decision(make_ohlcv(closes), cost=100.0, holding_days=120)
    assert result["ok"] is True
    assert result["decision"] == "ROTATE"
    assert "長期套牢" in result["warning_flags"]


def test_decision_always_in_known_set():
    scenarios = [
        (flat_series(30, 100.0), 100.0, 0),
        (flat_series(30, 80.0), 100.0, 200),
        (flat_series(30, 130.0), 100.0, 10),
        (list(range(50, 110)), 100.0, 75),
    ]
    for closes, cost, days in scenarios:
        result = sell_engine.calc_sell_decision(
            make_ohlcv([float(c) for c in closes]), cost=cost, holding_days=days)
        assert result["decision"] in PRIORITY


def test_result_contract():
    result = sell_engine.calc_sell_decision(make_ohlcv(flat_series(30)), cost=100.0)
    for key in ("ok", "engine", "score", "level", "reasons", "suggested_action",
                "warning_flags", "decision", "decision_label", "decision_color", "detail"):
        assert key in result, f"缺少欄位 {key}"
    assert result["engine"] == "sell_decision"
    assert isinstance(result["reasons"], list) and result["reasons"]


def test_pnl_pct_is_accurate():
    result = sell_engine.calc_sell_decision(make_ohlcv(flat_series(30, 110.0)), cost=100.0)
    assert result["detail"]["pnl_pct"] == pytest.approx(10.0)


def test_zero_holding_days_does_not_trigger_time_stop():
    result = sell_engine.calc_sell_decision(
        make_ohlcv(flat_series(30, 100.0)), cost=100.0, holding_days=0)
    assert "長期套牢" not in result["warning_flags"]


def test_negative_holding_days_does_not_trigger_time_stop():
    result = sell_engine.calc_sell_decision(
        make_ohlcv(flat_series(30, 100.0)), cost=100.0, holding_days=-10)
    assert result["ok"] is True
    assert "長期套牢" not in result["warning_flags"]
    assert "時間成本偏高" not in result["warning_flags"]
