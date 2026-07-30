"""rotation_engine 與 portfolio_engine 之間的型別契約測試。

`calc_capital_efficiency` 的 `detail.holding_days` 在買進日期缺漏時為 None。
`dict.get(key, default)` 在「鍵存在但值為 None」時**不會**套用 default，
因此下游若直接拿去做 `>= 7` 比較會拋 TypeError——這是實際發生過的回歸，
且曾同時存在於 rotation_engine 與 daily_report_engine 兩處。

修法是讓 `calc_drag_score` 自己接受 `int | None`，因此測試直接針對
「傳入 None」與「實際輸出值」驗證，而不是驗證 CPython 的 dict 語意。
"""
from __future__ import annotations

import portfolio_engine
import pytest
import rotation_engine
from conftest import flat_series, make_ohlcv

NO_DRAG = "無明顯拖累資金因素"


def _drag(holding_days, *, pnl_pct=-5.0, benchmark_return=2.0,
          ce_score=50, momentum_score=50, sell_signal="HOLD"):
    return rotation_engine.calc_drag_score(
        ce_score=ce_score,
        holding_days=holding_days,
        pnl_pct=pnl_pct,
        benchmark_return=benchmark_return,
        sector_trend=None,
        sell_signal=sell_signal,
        momentum_score=momentum_score,
    )


# ── calc_drag_score 的輸出契約 ────────────────────────────────────────────────

def test_drag_score_returns_expected_keys():
    result = _drag(30)
    assert sorted(result) == ["drag_level", "drag_reasons", "drag_score"]


@pytest.mark.parametrize("holding_days", [None, 0, 1, 6, 7, 10, 100])
def test_drag_score_is_bounded_and_level_is_known(holding_days):
    result = _drag(holding_days)
    assert 0 <= result["drag_score"] <= 100
    assert result["drag_level"] in rotation_engine.DRAG_LEVEL


def test_none_holding_days_scores_same_as_zero():
    """None 應被歸零處理，輸出必須與 holding_days=0 完全一致。"""
    assert _drag(None) == _drag(0)


def test_none_holding_days_does_not_raise():
    """回歸測試：None >= 7 曾拋 TypeError。"""
    result = _drag(None)
    assert result["drag_score"] == 0
    assert result["drag_reasons"] == [NO_DRAG]


@pytest.mark.parametrize("holding_days", [None, 0, 6])
def test_short_or_unknown_holding_skips_day_based_penalties(holding_days):
    """天數未知或未達 7 天：不得出現任何以持有天數為條件的扣分理由。"""
    result = _drag(holding_days, pnl_pct=-20.0, benchmark_return=10.0)
    assert not any("持有" in reason for reason in result["drag_reasons"])
    assert result["drag_score"] == 0


def test_seven_days_underperforming_benchmark_adds_penalty():
    """對照組：達 7 天且跑輸基準 > 5% → +20 分並產生對應理由。"""
    result = _drag(7, pnl_pct=-10.0, benchmark_return=2.0)
    assert result["drag_score"] == 20
    assert any("持有 7 天，跑輸基準" in reason for reason in result["drag_reasons"])


def test_ten_days_losing_adds_loss_penalty():
    """達 10 天且虧損 → 追加虧損扣分，分數高於 7 天情境。"""
    seven = _drag(7, pnl_pct=-10.0, benchmark_return=2.0)
    ten = _drag(10, pnl_pct=-10.0, benchmark_return=2.0)
    assert ten["drag_score"] > seven["drag_score"]
    assert any("仍虧損" in reason for reason in ten["drag_reasons"])


def test_drag_reasons_never_empty():
    result = _drag(0, pnl_pct=5.0, benchmark_return=None)
    assert result["drag_reasons"] == [NO_DRAG]


# ── 與 portfolio_engine 的實際資料流 ──────────────────────────────────────────

def test_capital_efficiency_holding_days_is_none_when_date_unparseable():
    """釘住契約來源：買進日期無法解析時 detail.holding_days 為 None。"""
    ce = portfolio_engine.calc_capital_efficiency(
        {"symbol": "X", "cost": 100.0, "buy_date": "not-a-date"},
        make_ohlcv(flat_series(25, 110.0)),
    )
    assert ce["ok"] is True
    assert ce["detail"]["holding_days"] is None


def test_capital_efficiency_output_flows_into_drag_score_without_error():
    """端對端：portfolio_engine 的輸出直接餵給 calc_drag_score 不得崩潰。

    這是實際回歸路徑（rotation_engine:418 / daily_report_engine:314 的寫法），
    比單獨驗證 dict.get 語意有意義得多。
    """
    ce = portfolio_engine.calc_capital_efficiency(
        {"symbol": "X", "cost": 100.0, "buy_date": ""},
        make_ohlcv(flat_series(25, 90.0)),
    )
    detail = ce["detail"]
    result = rotation_engine.calc_drag_score(
        ce_score=ce["score"],
        holding_days=detail["holding_days"],      # None
        pnl_pct=detail["pnl_pct"],
        benchmark_return=None,
        sector_trend=None,
        sell_signal=ce["level"],
        momentum_score=50,
    )
    assert 0 <= result["drag_score"] <= 100
    assert not any("持有" in reason for reason in result["drag_reasons"])


def test_valid_buy_date_still_produces_day_based_reasoning():
    """對照組：日期正常時，天數確實會流進 drag_reasons。"""
    from datetime import date, timedelta
    buy = (date.today() - timedelta(days=30)).isoformat()
    ce = portfolio_engine.calc_capital_efficiency(
        {"symbol": "X", "cost": 100.0, "buy_date": buy},
        make_ohlcv(flat_series(25, 90.0)),
    )
    assert ce["detail"]["holding_days"] == 30
    result = rotation_engine.calc_drag_score(
        ce_score=ce["score"], holding_days=ce["detail"]["holding_days"],
        pnl_pct=ce["detail"]["pnl_pct"], benchmark_return=0.0,
        sector_trend=None, sell_signal=ce["level"], momentum_score=50,
    )
    assert any("持有 30 天" in reason for reason in result["drag_reasons"])


# ── sell_engine 也必須容忍 None（stress_test_engine 會傳入）──────────────────

def test_sell_decision_accepts_none_holding_days():
    import sell_engine
    result = sell_engine.calc_sell_decision(
        make_ohlcv(flat_series(30, 100.0)), cost=100.0, holding_days=None)
    assert result["ok"] is True
    assert "長期套牢" not in result["warning_flags"]
