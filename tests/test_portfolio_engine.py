"""portfolio_engine：資金效率評分的邊界條件與異常輸入測試。"""
from __future__ import annotations

from datetime import date, timedelta

import portfolio_engine
import pytest
from conftest import INF, NAN, flat_series, make_ohlcv

LEVELS = {"ADD", "HOLD", "WATCH", "TRIM", "ROTATE", "STOP_LOSS"}


def _holding(**kwargs) -> dict:
    base = {"symbol": "TEST", "cost": 100.0, "qty": 10.0,
            "buy_date": (date.today() - timedelta(days=60)).isoformat()}
    base.update(kwargs)
    return base


# ── 無效輸入 ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("cost", [0, -10, None, "abc", NAN, INF])
def test_invalid_cost_returns_not_ok(cost):
    result = portfolio_engine.calc_capital_efficiency(
        _holding(cost=cost), make_ohlcv(flat_series(25)))
    assert result["ok"] is False
    assert result["score"] is None


def test_empty_closes_returns_not_ok(empty_ohlcv):
    result = portfolio_engine.calc_capital_efficiency(_holding(), empty_ohlcv)
    assert result["ok"] is False


def test_non_dict_holding_returns_not_ok():
    assert portfolio_engine.calc_capital_efficiency(None, make_ohlcv(flat_series(25)))["ok"] is False


def test_non_dict_ohlcv_returns_not_ok():
    assert portfolio_engine.calc_capital_efficiency(_holding(), None)["ok"] is False


def test_invalid_qty_does_not_raise():
    result = portfolio_engine.calc_capital_efficiency(
        _holding(qty="abc"), make_ohlcv(flat_series(25)))
    assert result["ok"] is True
    assert result["detail"]["position_value"] == 0.0


# ── buy_date 處理 ─────────────────────────────────────────────────────────────

def test_malformed_buy_date_reports_unknown_not_fabricated():
    """無法解析時回報 None，不得假造天數（會直接顯示給使用者）。"""
    result = portfolio_engine.calc_capital_efficiency(
        _holding(buy_date="not-a-date"), make_ohlcv(flat_series(25)))
    assert result["ok"] is True
    assert result["detail"]["holding_days"] is None
    assert result["detail"]["annual_return"] is None


def test_missing_buy_date_reports_unknown():
    holding = _holding()
    del holding["buy_date"]
    result = portfolio_engine.calc_capital_efficiency(holding, make_ohlcv(flat_series(25)))
    assert result["detail"]["holding_days"] is None
    assert result["detail"]["annual_return"] is None


def test_future_buy_date_is_flagged_as_data_error():
    """未來買進日期是資料錯誤，應標記而非與『解析失敗』混為一談。"""
    future = (date.today() + timedelta(days=365)).isoformat()
    result = portfolio_engine.calc_capital_efficiency(
        _holding(buy_date=future), make_ohlcv(flat_series(25, 110.0)))
    assert result["detail"]["holding_days"] == 0
    assert result["detail"]["annual_return"] is None
    assert any("買進日期異常" in flag for flag in result["warning_flags"])


def test_same_day_purchase_reports_zero_days_not_thirty():
    """回歸測試：當日買進是合法資料，曾被假造成『已持有 30 天』。"""
    result = portfolio_engine.calc_capital_efficiency(
        _holding(buy_date=date.today().isoformat()), make_ohlcv(flat_series(25)))
    assert result["detail"]["holding_days"] == 0


@pytest.mark.parametrize("days", [0, 1, 3, 6])
def test_short_holding_skips_annualisation(days):
    """回歸測試：持有 1 天的 +10% 曾被年化成 3650%，直接餵進評分。"""
    buy = (date.today() - timedelta(days=days)).isoformat()
    result = portfolio_engine.calc_capital_efficiency(
        _holding(buy_date=buy), make_ohlcv(flat_series(25, 110.0)))
    assert result["detail"]["holding_days"] == days
    assert result["detail"]["annual_return"] is None
    assert any("年化報酬" in reason for reason in result["reasons"])


def test_sufficient_holding_computes_annualisation():
    buy = (date.today() - timedelta(days=365)).isoformat()
    result = portfolio_engine.calc_capital_efficiency(
        _holding(buy_date=buy), make_ohlcv(flat_series(25, 110.0)))
    assert result["detail"]["holding_days"] == 365
    assert result["detail"]["annual_return"] == pytest.approx(10.0, abs=0.1)


# ── 評分契約 ──────────────────────────────────────────────────────────────────

def test_score_within_bounds_across_scenarios():
    scenarios = [
        (100.0, flat_series(25, 200.0), None),
        (100.0, flat_series(25, 10.0), None),
        (100.0, flat_series(25, 100.0), 50.0),
        (100.0, flat_series(25, 100.0), -50.0),
    ]
    for cost, closes, bench in scenarios:
        result = portfolio_engine.calc_capital_efficiency(
            _holding(cost=cost), make_ohlcv(closes), benchmark_return=bench)
        assert 0 <= result["score"] <= 100
        assert result["level"] in LEVELS


def test_benchmark_none_uses_absolute_return_fallback():
    result = portfolio_engine.calc_capital_efficiency(
        _holding(), make_ohlcv(flat_series(25, 130.0)), benchmark_return=None)
    assert result["ok"] is True
    assert result["detail"]["benchmark_return"] is None


def test_outperforming_benchmark_scores_higher_than_underperforming():
    ohlcv = make_ohlcv(flat_series(25, 130.0))
    win = portfolio_engine.calc_capital_efficiency(_holding(), ohlcv, benchmark_return=-20.0)
    lose = portfolio_engine.calc_capital_efficiency(_holding(), ohlcv, benchmark_return=80.0)
    assert win["score"] > lose["score"]


def test_losing_position_flags_warning():
    result = portfolio_engine.calc_capital_efficiency(
        _holding(), make_ohlcv(flat_series(25, 70.0)))
    assert result["warning_flags"]


def test_result_contract():
    result = portfolio_engine.calc_capital_efficiency(_holding(), make_ohlcv(flat_series(25)))
    for key in ("ok", "engine", "symbol", "score", "level", "level_label", "reasons",
                "suggested_action", "warning_flags", "efficiency_level", "detail"):
        assert key in result, f"缺少欄位 {key}"
    assert result["engine"] == "capital_efficiency"


def test_current_price_override_is_used():
    result = portfolio_engine.calc_capital_efficiency(
        _holding(current_price=150.0), make_ohlcv(flat_series(25, 100.0)))
    assert result["detail"]["current_price"] == pytest.approx(150.0)
    assert result["detail"]["pnl_pct"] == pytest.approx(50.0)


@pytest.mark.parametrize("dirty", [[None] * 25, [NAN] * 25, [0.0] * 25, ["abc"] * 25])
def test_dirty_closes_with_valid_current_price_reports_insufficient(dirty):
    """回歸測試：有合法 current_price 但零根有效 K 線時，
    曾產出 ok=True 與「收在 MA20 之上，趨勢尚可」；字串更會拋 TypeError。"""
    result = portfolio_engine.calc_capital_efficiency(
        _holding(current_price=120.0), make_ohlcv(dirty))
    assert result["ok"] is False
    assert result["score"] is None
    assert not any("MA20" in reason for reason in result["reasons"])


def test_partial_dirty_closes_still_evaluates():
    """對照組：仍有有效 K 棒時應正常評估。"""
    closes = [None if i % 2 else 110.0 for i in range(50)]
    result = portfolio_engine.calc_capital_efficiency(
        _holding(current_price=120.0), make_ohlcv(closes))
    assert result["ok"] is True


def test_invalid_current_price_falls_back_to_last_close():
    result = portfolio_engine.calc_capital_efficiency(
        _holding(current_price=NAN), make_ohlcv(flat_series(25, 120.0)))
    assert result["detail"]["current_price"] == pytest.approx(120.0)


def test_short_history_skips_momentum_block():
    """不足 20 天時跳過動能評分，但仍應回傳有效結果。"""
    result = portfolio_engine.calc_capital_efficiency(_holding(), make_ohlcv(flat_series(5)))
    assert result["ok"] is True
    assert 0 <= result["score"] <= 100
