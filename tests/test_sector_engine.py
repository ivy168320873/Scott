"""sector_engine：板塊領先度評分的邊界條件測試。"""
from __future__ import annotations

import sector_engine
from conftest import flat_series, make_ohlcv, rising_series

LEVELS = {"LEADING", "IMPROVING", "NEUTRAL", "WEAKENING", "LAGGING"}


def _members(count: int, series_fn=flat_series, n: int = 25) -> dict:
    return {f"SYM{i}": make_ohlcv(series_fn(n)) for i in range(count)}


# ── 無效輸入 ──────────────────────────────────────────────────────────────────

def test_empty_sector_returns_not_ok():
    result = sector_engine.calc_sector_leadership("空板塊", {})
    assert result["ok"] is False
    assert result["stock_count"] == 0
    assert result["score"] is None


def test_all_members_insufficient_returns_not_ok():
    """每檔都不足 20 天 → 無有效樣本。"""
    members = {f"S{i}": make_ohlcv(flat_series(10)) for i in range(3)}
    result = sector_engine.calc_sector_leadership("測試", members)
    assert result["ok"] is False
    assert result["stock_count"] == 0


def test_mixed_sufficiency_counts_only_valid_members():
    members = {
        "GOOD1": make_ohlcv(flat_series(25)),
        "GOOD2": make_ohlcv(flat_series(25)),
        "SHORT": make_ohlcv(flat_series(5)),
    }
    result = sector_engine.calc_sector_leadership("測試", members)
    assert result["ok"] is True
    assert result["stock_count"] == 2


def test_zero_reference_price_reports_insufficient_data():
    """0 價會讓 20 日漲幅除以零；清洗後應無有效樣本。"""
    members = {"Z": make_ohlcv([0.0] * 21 + [5.0])}
    result = sector_engine.calc_sector_leadership("測試", members)
    assert result["ok"] is False
    assert result["stock_count"] == 0


def test_member_with_closes_only_is_accepted():
    """只有 closes 仍是合法輸入（輔助序列回退為 close）。"""
    members = {"MINIMAL": {"closes": [10.0] * 25}}
    result = sector_engine.calc_sector_leadership("測試", members)
    assert result["ok"] is True
    assert result["stock_count"] == 1


def test_missing_volume_does_not_claim_volume_contraction():
    """回歸測試：缺成交量資料曾被當成 volume=0，憑空產出「板塊整體量能萎縮（0%）」。"""
    members = {"MINIMAL": {"closes": list(rising_series(25))}}
    result = sector_engine.calc_sector_leadership("測試", members)
    assert "量能萎縮" not in result["warning_flags"]
    assert not any("量能萎縮" in reason for reason in result["reasons"])
    # 文案會標示樣本數，例如「僅 0/1 檔有成交量資料，量能項未納入評分」
    assert any("有成交量資料" in reason and "未納入評分" in reason
               for reason in result["reasons"])
    assert result["detail"]["vol_expand_ratio_pct"] is None


def test_mismatched_volume_series_does_not_claim_contraction():
    """volumes 長度差一根（資料源常見 off-by-one）同樣不得被當成 0 量。"""
    ohlcv = make_ohlcv(rising_series(25))
    ohlcv["volumes"] = [1_000_000.0] * 24
    result = sector_engine.calc_sector_leadership("測試", {"A": ohlcv})
    assert "量能萎縮" not in result["warning_flags"]
    assert result["detail"]["vol_expand_ratio_pct"] is None


def test_real_volume_contraction_is_still_reported():
    """對照組：真的有量資料且量能萎縮時，仍必須示警。"""
    ohlcv = make_ohlcv(rising_series(25))
    ohlcv["volumes"] = [1_000_000.0] * 24 + [1.0]      # 最後一根極度萎縮
    result = sector_engine.calc_sector_leadership("測試", {"A": ohlcv})
    assert result["detail"]["vol_expand_ratio_pct"] == 0.0
    assert "量能萎縮" in result["warning_flags"]


def test_member_with_dirty_closes_is_skipped():
    members = {"DIRTY": make_ohlcv([None] * 25), "NAN": make_ohlcv([float("nan")] * 25)}
    result = sector_engine.calc_sector_leadership("測試", members)
    assert result["ok"] is False
    assert result["stock_count"] == 0


# ── 評分契約 ──────────────────────────────────────────────────────────────────

def test_score_within_bounds():
    for series_fn in (flat_series, rising_series):
        result = sector_engine.calc_sector_leadership("測試", _members(5, series_fn))
        assert 0 <= result["score"] <= 100
        assert result["level"] in LEVELS


def test_rising_sector_scores_higher_than_flat():
    flat = sector_engine.calc_sector_leadership("平", _members(5, flat_series))
    rise = sector_engine.calc_sector_leadership("漲", _members(5, rising_series))
    assert rise["score"] > flat["score"]


def test_ratios_are_percentages_within_bounds():
    result = sector_engine.calc_sector_leadership("測試", _members(4, rising_series))
    detail = result["detail"]
    for key in ("new_high_ratio_pct", "vol_expand_ratio_pct", "above_ma20_ratio_pct"):
        assert detail[key] is not None, f"{key} 不應為 None（測試資料含成交量）"
        assert 0.0 <= detail[key] <= 100.0, f"{key} 超出 0-100"


def test_single_member_sector_works():
    result = sector_engine.calc_sector_leadership("單檔", _members(1))
    assert result["ok"] is True
    assert result["stock_count"] == 1


def test_result_contract():
    result = sector_engine.calc_sector_leadership("測試", _members(3))
    for key in ("ok", "engine", "score", "level", "level_label", "level_color", "reasons",
                "suggested_action", "warning_flags", "sector", "stock_count",
                "leadership_level", "detail"):
        assert key in result, f"缺少欄位 {key}"
    assert result["engine"] == "sector_leadership"
    assert result["sector"] == "測試"


def test_sector_name_appears_in_suggested_action():
    result = sector_engine.calc_sector_leadership("台股半導體", _members(3, rising_series))
    assert "台股半導體" in result["suggested_action"]


def test_exactly_twenty_days_is_counted():
    """20 天為納入門檻的邊界值。"""
    members = {"A": make_ohlcv(flat_series(20))}
    assert sector_engine.calc_sector_leadership("測試", members)["stock_count"] == 1


def test_nineteen_days_is_excluded():
    members = {"A": make_ohlcv(flat_series(19))}
    assert sector_engine.calc_sector_leadership("測試", members)["stock_count"] == 0
