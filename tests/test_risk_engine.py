"""risk_engine：追價風險評分的邊界條件與契約測試。"""
from __future__ import annotations

import pytest
import risk_engine
from conftest import NAN, flat_series, make_ohlcv, rising_series


# ── _sma ──────────────────────────────────────────────────────────────────────

def test_sma_basic():
    assert risk_engine._sma([1.0, 2.0, 3.0], 3) == pytest.approx(2.0)


def test_sma_uses_last_n_only():
    assert risk_engine._sma([100.0, 1.0, 1.0], 2) == pytest.approx(1.0)


def test_sma_empty_returns_zero_not_zerodivision():
    """除以零保護：沒有有效值時回 0.0 而非拋 ZeroDivisionError。"""
    assert risk_engine._sma([], 5) == 0.0


def test_sma_filters_none_and_nonpositive():
    assert risk_engine._sma([None, 0, -1, 4.0], 4) == pytest.approx(4.0)


def test_sma_all_invalid_returns_zero():
    assert risk_engine._sma([None, 0, -3], 3) == 0.0


# ── _rsi_simple ───────────────────────────────────────────────────────────────

def test_rsi_returns_none_when_insufficient_data():
    assert risk_engine._rsi_simple([1.0] * 10) is None


def test_rsi_flat_series_has_no_losses():
    """全平盤時 avg_loss=0，必須走除以零保護回 50.0（無方向）。"""
    assert risk_engine._rsi_simple(flat_series(30)) == 50.0


def test_rsi_monotonic_rise_is_100():
    assert risk_engine._rsi_simple(rising_series(30)) == 100.0


def test_rsi_within_bounds():
    closes = [100 + (i % 7) - 3 for i in range(40)]
    rsi = risk_engine._rsi_simple(closes)
    assert rsi is not None and 0.0 <= rsi <= 100.0


# ── calc_chase_risk ───────────────────────────────────────────────────────────

def test_chase_risk_insufficient_data_returns_not_ok(empty_ohlcv):
    result = risk_engine.calc_chase_risk(empty_ohlcv)
    assert result["ok"] is False
    assert result["level"] == "UNKNOWN"
    assert result["score"] is None


def test_chase_risk_missing_keys_returns_not_ok():
    assert risk_engine.calc_chase_risk({})["ok"] is False


def test_chase_risk_just_below_threshold_is_insufficient():
    """19 天資料不足，20 天才夠——邊界值。"""
    assert risk_engine.calc_chase_risk(make_ohlcv(flat_series(19)))["ok"] is False


def test_chase_risk_at_threshold_is_ok():
    assert risk_engine.calc_chase_risk(make_ohlcv(flat_series(20)))["ok"] is True


def test_chase_risk_flat_series_is_low_risk(flat_ohlcv):
    result = risk_engine.calc_chase_risk(flat_ohlcv)
    assert result["ok"] is True
    assert result["level"] == "LOW"
    assert result["score"] == 0


def test_chase_risk_score_always_within_bounds():
    for series in (flat_series(60), rising_series(60), rising_series(60, step=20.0)):
        result = risk_engine.calc_chase_risk(make_ohlcv(series))
        assert 0 <= result["score"] <= 100


def test_chase_risk_sharp_rise_scores_higher_than_flat():
    flat = risk_engine.calc_chase_risk(make_ohlcv(flat_series(60)))
    spike = risk_engine.calc_chase_risk(make_ohlcv(rising_series(60, step=8.0)))
    assert spike["score"] > flat["score"]


def test_chase_risk_zero_prices_report_insufficient_data():
    """全 0 價必須回報資料不足，不得算出「低追價風險，可介入」。

    回歸測試：0 價會讓 ma20=0、所有比較都不成立，於是回 score=0／level=LOW，
    等於把「沒有資料」講成「風險可控，可依訊號介入」。
    """
    result = risk_engine.calc_chase_risk(make_ohlcv([0.0] * 30))
    assert result["ok"] is False
    assert result["level"] == "UNKNOWN"
    assert "介入" not in result["suggested_action"]


def test_chase_risk_result_contract():
    result = risk_engine.calc_chase_risk(make_ohlcv(flat_series(30)))
    for key in ("ok", "engine", "score", "level", "level_label", "level_color",
                "reasons", "suggested_action", "warning_flags", "risk_level", "detail"):
        assert key in result, f"缺少欄位 {key}"
    assert result["engine"] == "chase_risk"
    assert isinstance(result["reasons"], list) and result["reasons"]
    assert result["level"] in ("LOW", "MEDIUM", "HIGH", "EXTREME")


def test_chase_risk_ma60_none_when_short_history():
    result = risk_engine.calc_chase_risk(make_ohlcv(flat_series(30)))
    assert result["detail"]["ma60_dev_pct"] is None


def test_chase_risk_ma60_present_when_long_history():
    result = risk_engine.calc_chase_risk(make_ohlcv(flat_series(70)))
    assert result["detail"]["ma60_dev_pct"] is not None


def test_chase_risk_mismatched_volume_series_is_ignored_not_misaligned():
    """長度不符的 volumes 整條忽略，不可截短 closes 造成錯配。"""
    ohlcv = make_ohlcv(flat_series(30))
    ohlcv["volumes"] = [1000.0] * 10
    result = risk_engine.calc_chase_risk(ohlcv)
    assert result["ok"] is True               # closes 有 30 根，仍可評估
    assert result["detail"]["close"] == pytest.approx(100.0)
    assert any("缺成交量資料" in reason for reason in result["reasons"])


def test_chase_risk_missing_volume_skips_volume_signal_honestly():
    """回歸測試：缺量曾被當成 0 量，讓「爆量長上影」出貨訊號靜默消失。"""
    ohlcv = {"closes": list(flat_series(30))}
    result = risk_engine.calc_chase_risk(ohlcv)
    assert result["ok"] is True
    assert "爆量長上影" not in result["warning_flags"]
    assert any("缺成交量資料" in reason for reason in result["reasons"])


def test_chase_risk_real_volume_spike_still_flags_upper_shadow():
    """對照組：有真實量資料時，爆量長上影仍必須被偵測到。"""
    closes = flat_series(29, 100.0) + [100.0]
    ohlcv = make_ohlcv(closes)
    ohlcv["opens"] = [100.0] * 30
    ohlcv["closes"] = [100.0] * 29 + [102.0]
    ohlcv["opens"][-1] = 100.0
    ohlcv["highs"] = [101.0] * 29 + [120.0]      # 長上影
    ohlcv["lows"] = [99.0] * 30
    ohlcv["volumes"] = [1_000_000.0] * 29 + [5_000_000.0]   # 爆量
    result = risk_engine.calc_chase_risk(ohlcv)
    assert "爆量長上影" in result["warning_flags"]


def test_chase_risk_too_few_valid_bars_after_cleaning():
    """清洗後有效根數不足 20 必須回報資料不足。"""
    closes = [NAN if i % 2 else 100.0 for i in range(30)]   # 剩 15 根
    assert risk_engine.calc_chase_risk(make_ohlcv(closes))["ok"] is False


def test_chase_risk_nan_prices_report_insufficient_data():
    """全 NaN 必須回報資料不足。

    回歸測試：NaN 會靜默通過所有比較（`NaN > x` 恆為 False），
    使每個加分區塊都不觸發而得到 score=0／level=LOW／ok=True。
    """
    result = risk_engine.calc_chase_risk(make_ohlcv([NAN] * 30))
    assert result["ok"] is False
    assert result["level"] == "UNKNOWN"
    assert result["score"] is None


def test_chase_risk_none_prices_report_insufficient_data():
    result = risk_engine.calc_chase_risk(make_ohlcv([None] * 30))
    assert result["ok"] is False
    assert result["level"] == "UNKNOWN"


def test_chase_risk_partial_nan_keeps_valid_bars():
    """髒資料只丟該根 K 棒；剩餘有效根數足夠時仍應正常評估。"""
    closes = [NAN if i % 2 else 100.0 + i for i in range(60)]
    result = risk_engine.calc_chase_risk(make_ohlcv(closes))
    assert result["ok"] is True
    assert result["score"] is not None
