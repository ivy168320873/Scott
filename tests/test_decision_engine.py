"""decision_engine：正規化與清洗層的邊界條件測試。

這一層是 app.py／alert_scanner／daily_report_engine 進入各評分引擎的
唯一入口，因此缺值處理必須在這裡就擋掉，不能讓髒資料流進引擎。
"""
from __future__ import annotations

import decision_engine as de
import pytest
from conftest import INF, NAN, flat_series, make_ohlcv, rising_series


# ── _num ──────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("value,expected", [
    (1, 1.0),
    (1.5, 1.5),
    ("2.5", 2.5),
    (0, 0.0),
    (-3, -3.0),
])
def test_num_accepts_finite_numbers(value, expected):
    assert de._num(value) == expected


@pytest.mark.parametrize("value", [None, NAN, INF, -INF, "abc", "", [], {}, object()])
def test_num_rejects_invalid(value):
    assert de._num(value) is None


# ── normalize_yahoo ───────────────────────────────────────────────────────────

def _yahoo(closes, opens=None, highs=None, lows=None, volumes=None, timestamps=None):
    n = len(closes)
    return {"chart": {"result": [{
        "timestamp": timestamps if timestamps is not None else list(range(n)),
        "indicators": {"quote": [{
            "open":   opens   if opens   is not None else [1.0] * n,
            "high":   highs   if highs   is not None else [2.0] * n,
            "low":    lows    if lows    is not None else [0.5] * n,
            "close":  closes,
            "volume": volumes if volumes is not None else [100] * n,
        }]},
    }]}}


def test_normalize_yahoo_happy_path():
    out = de.normalize_yahoo(_yahoo([10.0, 11.0, 12.0]))
    assert out is not None
    assert out["closes"] == [10.0, 11.0, 12.0]
    assert len(out["opens"]) == len(out["closes"]) == len(out["volumes"])


@pytest.mark.parametrize("payload", [
    None, {}, {"chart": {}}, {"chart": {"result": []}},
    {"chart": {"result": [{}]}}, "not-a-dict", 123,
])
def test_normalize_yahoo_malformed_returns_none(payload):
    assert de.normalize_yahoo(payload) is None


def test_normalize_yahoo_filters_nonpositive_and_missing_closes():
    # 0、None、負數都應被濾掉，只留下 11.0
    out = de.normalize_yahoo(_yahoo([0.0, None, -5.0, 11.0]))
    assert out is not None
    assert out["closes"] == [11.0]


def test_normalize_yahoo_all_invalid_returns_none():
    assert de.normalize_yahoo(_yahoo([0.0, None, -1.0])) is None


def test_normalize_yahoo_handles_missing_timestamps():
    """非交易日／來源缺 timestamp 時不應崩潰。"""
    out = de.normalize_yahoo(_yahoo([10.0, 11.0], timestamps=[]))
    assert out is not None
    assert out["closes"] == [10.0, 11.0]


# ── normalize_list ────────────────────────────────────────────────────────────

def test_normalize_list_happy_path():
    rows = [{"date": "2026-01-02", "open": 9, "high": 11, "low": 8, "close": 10, "volume": 500}]
    out = de.normalize_list(rows)
    assert out == {
        "closes": [10.0], "opens": [9.0], "highs": [11.0],
        "lows": [8.0], "volumes": [500.0], "timestamps": [],
        "has_volume": True,
    }


@pytest.mark.parametrize("rows", [None, [], [{}], [{"close": 0}], [{"close": -1}]])
def test_normalize_list_empty_or_invalid_returns_none(rows):
    assert de.normalize_list(rows) is None


def test_normalize_list_close_none_does_not_raise():
    """回歸測試：close=None 曾拋 TypeError（'>' NoneType vs int）。"""
    assert de.normalize_list([{"close": None}]) is None


def test_normalize_list_close_string_does_not_raise():
    """回歸測試：close 為非數值字串曾拋 TypeError。"""
    assert de.normalize_list([{"close": "abc"}]) is None


def test_normalize_list_numeric_string_is_accepted():
    out = de.normalize_list([{"close": "10.5"}])
    assert out is not None and out["closes"] == [10.5]


def test_normalize_list_nan_close_is_dropped():
    assert de.normalize_list([{"close": NAN}]) is None


def test_normalize_list_non_dict_rows_do_not_raise():
    """回歸測試：非 dict 的列曾拋 AttributeError。"""
    assert de.normalize_list([1, "x", None]) is None


def test_normalize_list_mixes_valid_and_invalid():
    rows = [{"close": None}, {"close": 10}, "junk", {"close": NAN}, {"close": 12}]
    out = de.normalize_list(rows)
    assert out is not None
    assert out["closes"] == [10.0, 12.0]


def test_normalize_list_missing_ohlc_falls_back_to_close():
    out = de.normalize_list([{"close": 10}])
    assert out["opens"] == [10.0] and out["highs"] == [10.0] and out["lows"] == [10.0]
    assert out["volumes"] == [0.0]


def test_normalize_list_none_ohlc_falls_back_to_close():
    out = de.normalize_list([{"close": 10, "open": None, "high": None, "low": None, "volume": None}])
    assert out["opens"] == [10.0] and out["volumes"] == [0.0]


def test_normalize_list_all_series_same_length():
    rows = [{"close": c} for c in (10, 11, 12)]
    out = de.normalize_list(rows)
    lengths = {len(out[k]) for k in ("closes", "opens", "highs", "lows", "volumes")}
    assert lengths == {3}


# ── sanitize_ohlcv ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("value", [None, {}, "x", 5, [], {"closes": []}])
def test_sanitize_ohlcv_empty_returns_none(value):
    assert de.sanitize_ohlcv(value) is None


def test_sanitize_ohlcv_passes_clean_data_through():
    ohlcv = make_ohlcv(flat_series(25))
    out = de.sanitize_ohlcv(ohlcv)
    assert out is not None
    assert len(out["closes"]) == 25


def test_sanitize_ohlcv_drops_none_rows_and_keeps_alignment():
    closes = [10.0, None, 12.0, NAN, 14.0]
    out = de.sanitize_ohlcv(make_ohlcv(closes))
    assert out is not None
    assert out["closes"] == [10.0, 12.0, 14.0]
    for key in ("opens", "highs", "lows", "volumes"):
        assert len(out[key]) == 3, f"{key} 長度與 closes 不一致"


def test_sanitize_ohlcv_missing_ohlc_falls_back_to_close():
    """輔助序列缺值時回退為當根收盤價，不丟棄整根 K 棒。

    只有 closes 是必要序列——`sell_engine`／`portfolio_engine` 本來就只用
    closes，若要求五個序列齊全，會讓停損告警在資料源缺 volume 時靜默失效。
    """
    ohlcv = make_ohlcv([10.0, 11.0], opens=[9.0, None], highs=[11.0, 12.0], lows=[8.0, 9.0])
    out = de.sanitize_ohlcv(ohlcv)
    assert out is not None
    assert out["closes"] == [10.0, 11.0]
    assert out["opens"] == [9.0, 11.0]      # 第 2 根回退為 close


def test_sanitize_ohlcv_closes_only_is_accepted():
    """回歸測試：曾要求五個序列都非空，導致只有 closes 時被判為資料不足。"""
    out = de.sanitize_ohlcv({"closes": [10.0] * 30})
    assert out is not None
    assert len(out["closes"]) == 30
    assert out["opens"] == out["closes"]
    assert out["volumes"] == [0.0] * 30


def test_sanitize_ohlcv_preserves_metadata():
    """is_demo／source 等標記不得在清洗過程中消失（示範資料須誠實標示）。"""
    out = de.sanitize_ohlcv({**make_ohlcv([10.0] * 5), "is_demo": True, "source": "demo"})
    assert out is not None
    assert out["is_demo"] is True
    assert out["source"] == "demo"


def test_sanitize_ohlcv_reports_dropped_bar_count():
    out = de.sanitize_ohlcv(make_ohlcv([10.0, None, 12.0, NAN, 14.0]))
    assert out is not None
    assert out["dropped_bars"] == 2


def test_sanitize_ohlcv_dropped_bars_accumulate_across_passes():
    """重複清洗時 dropped_bars 必須累加，不可被重置為 0。"""
    first = de.sanitize_ohlcv(make_ohlcv([10.0, None, 12.0]))
    second = de.sanitize_ohlcv(first)
    assert first["dropped_bars"] == 1
    assert second["dropped_bars"] == 1


def test_sanitize_ohlcv_marks_missing_volume_as_unknown():
    """缺 volumes 必須標記 has_volume=False，讓引擎能區分『沒有量』與『量為 0』。"""
    out = de.sanitize_ohlcv({"closes": [10.0] * 5})
    assert out is not None
    assert out["has_volume"] is False
    # 完全未提供 → 不算「有提供但不可用」，故不列入 ignored_series
    assert out["ignored_series"] == []


def test_sanitize_ohlcv_marks_present_volume_as_known():
    out = de.sanitize_ohlcv(make_ohlcv([10.0] * 5))
    assert out is not None
    assert out["has_volume"] is True
    assert out["ignored_series"] == []


def test_sanitize_ohlcv_all_zero_volume_is_treated_as_missing():
    """整段視窗成交量全為 0 視為缺值，而非「市場真的零成交」。

    正規化層（data_provider._normalize / normalize_list / normalize_yahoo）
    一律把缺漏的 volume 補成 0，因此「全 0」在實務上就是「沒有量資料」。
    單日 0 量（停牌）仍屬合法資料，由
    test_sanitize_ohlcv_single_zero_volume_day_is_real_data 覆蓋。
    """
    out = de.sanitize_ohlcv(make_ohlcv([10.0] * 5, volumes=[0.0] * 5))
    assert out is not None
    assert out["has_volume"] is False
    assert out["volumes"] == [0.0] * 5


def test_sanitize_ohlcv_single_zero_volume_day_is_real_data():
    """單日 0 量（停牌）不得讓整條序列被判為缺值。"""
    out = de.sanitize_ohlcv(make_ohlcv([10.0] * 5, volumes=[1000.0] * 4 + [0.0]))
    assert out is not None
    assert out["has_volume"] is True
    assert out["volumes"][-1] == 0.0


def test_sanitize_ohlcv_mismatched_volume_is_marked_unknown():
    ohlcv = make_ohlcv([10.0] * 5)
    ohlcv["volumes"] = [1.0] * 4
    out = de.sanitize_ohlcv(ohlcv)
    assert out is not None
    assert out["has_volume"] is False
    assert "volumes" in out["ignored_series"]


def test_sanitize_ohlcv_has_volume_not_resurrected_by_second_pass():
    """第一次清洗判定無量，第二次不得因為佔位 0.0 而誤判成有量。"""
    first = de.sanitize_ohlcv({"closes": [10.0] * 5})
    second = de.sanitize_ohlcv(first)
    assert second["has_volume"] is False


def test_sanitize_ohlcv_all_invalid_returns_none():
    assert de.sanitize_ohlcv(make_ohlcv([None, NAN, 0.0, -1.0])) is None


def test_sanitize_ohlcv_missing_volume_defaults_to_zero():
    out = de.sanitize_ohlcv(make_ohlcv([10.0], volumes=[None]))
    assert out is not None and out["volumes"] == [0.0]


def test_sanitize_ohlcv_mismatched_series_are_ignored_not_misaligned():
    """長度不符的輔助序列必須整條忽略，不可用尾端對齊硬湊。

    回歸測試：曾以 [-n:] 尾端對齊，會把第 2 根的 close 配上第 1 根的 open，
    讓 risk_engine 的「爆量長上影」算出假訊號。
    """
    ohlcv = {
        "closes": [1.0, 2.0, 3.0, 4.0], "opens": [1.0, 2.0, 3.0],
        "highs": [1.0, 2.0, 3.0], "lows": [1.0, 2.0, 3.0],
        "volumes": [1.0, 2.0, 3.0], "timestamps": [10, 20, 30, 40],
    }
    out = de.sanitize_ohlcv(ohlcv)
    assert out is not None
    assert out["closes"] == [1.0, 2.0, 3.0, 4.0]
    # opens 長度不符 → 整條忽略，逐根回退為 close（而非錯位對齊）
    assert out["opens"] == [1.0, 2.0, 3.0, 4.0]
    assert out["timestamps"] == [10, 20, 30, 40]


def test_sanitize_ohlcv_keeps_timestamps_aligned_after_dropping():
    ohlcv = make_ohlcv([10.0, None, 12.0])
    ohlcv["timestamps"] = [100, 200, 300]
    out = de.sanitize_ohlcv(ohlcv)
    assert out is not None
    assert out["closes"] == [10.0, 12.0]
    assert out["timestamps"] == [100, 300]


# ── run_* 包裝層（app.py 實際呼叫的入口）──────────────────────────────────────

def test_run_chase_risk_survives_dirty_data():
    """回歸測試：closes 含 None 曾讓 calc_chase_risk 拋 TypeError。"""
    dirty = make_ohlcv([None] * 30)
    result = de.run_chase_risk(dirty)
    assert result["ok"] is False
    assert result["level"] == "UNKNOWN"


def test_run_chase_risk_partial_none_keeps_valid_bars():
    """60 根中一半為 None → 剩 30 根有效，仍足以評估（門檻 20）。"""
    closes = [None if i % 2 else 100.0 + i for i in range(60)]
    result = de.run_chase_risk(make_ohlcv(closes))
    assert result["ok"] is True
    assert result["level"] in ("LOW", "MEDIUM", "HIGH", "EXTREME")


def test_run_chase_risk_partial_none_below_threshold_reports_insufficient():
    """30 根中一半為 None → 剩 15 根 < 20，必須回報資料不足而非硬算。"""
    closes = [None if i % 2 else 100.0 + i for i in range(30)]
    assert de.run_chase_risk(make_ohlcv(closes))["ok"] is False


def test_run_chase_risk_all_nan_is_not_reported_as_valid():
    """全 NaN 不應回報 ok=True 的分數（那等同於憑空捏造結論）。"""
    result = de.run_chase_risk(make_ohlcv([NAN] * 30))
    assert result["ok"] is False


def test_run_sell_decision_survives_none_closes():
    """回歸測試：closes 含 None 曾讓 calc_sell_decision 拋 TypeError。

    5 根中丟 1 根 → 剩 4 根 < 5，應明確回報資料不足。
    """
    result = de.run_sell_decision(make_ohlcv([10.0, None, 10.0, 10.0, 10.0]), cost=10.0)
    assert result["ok"] is False
    assert result["decision"] == "NONE"


def test_run_sell_decision_accepts_closes_only_payload():
    """回歸測試：曾因要求五個序列齊全，讓只有 closes 的資料被判資料不足，
    導致停損告警在資料源缺 volume 時靜默失效。"""
    result = de.run_sell_decision({"closes": [100.0] * 30}, cost=90.0)
    assert result["ok"] is True


# ── happy path：確保清洗層不會把正常資料誤判為「資料不足」 ────────────────────

def test_run_chase_risk_happy_path():
    result = de.run_chase_risk(make_ohlcv(rising_series(60, step=8.0)))
    assert result["ok"] is True
    assert result["score"] > 0
    assert result["detail"]["close"] == pytest.approx(100.0 + 8.0 * 59)


def test_run_sell_decision_happy_path():
    result = de.run_sell_decision(make_ohlcv(flat_series(30, 110.0)), cost=100.0)
    assert result["ok"] is True
    assert result["detail"]["pnl_pct"] == pytest.approx(10.0)


def test_run_capital_efficiency_happy_path():
    holding = {"symbol": "X", "cost": 100.0, "qty": 10.0, "buy_date": "2020-01-01"}
    result = de.run_capital_efficiency(holding, make_ohlcv(flat_series(25, 120.0)))
    assert result["ok"] is True
    assert result["detail"]["pnl_pct"] == pytest.approx(20.0)
    assert 0 <= result["score"] <= 100


def test_run_sector_leadership_happy_path():
    stocks = {f"S{i}": make_ohlcv(rising_series(25)) for i in range(4)}
    result = de.run_sector_leadership("測試板塊", stocks)
    assert result["ok"] is True
    assert result["stock_count"] == 4
    assert result["score"] > 0


@pytest.mark.parametrize("cost", [0, -5, None, NAN, INF, "abc"])
def test_run_sell_decision_rejects_invalid_cost(cost):
    result = de.run_sell_decision(make_ohlcv(flat_series(30)), cost=cost)
    assert result["ok"] is False
    assert result["decision"] == "NONE"


def test_run_capital_efficiency_rejects_invalid_cost():
    result = de.run_capital_efficiency({"symbol": "X", "cost": NAN}, make_ohlcv(flat_series(25)))
    assert result["ok"] is False


def test_run_sector_leadership_ignores_dirty_members():
    stocks = {
        "GOOD": make_ohlcv(flat_series(25)),
        "DIRTY": make_ohlcv([None] * 25),
    }
    result = de.run_sector_leadership("測試板塊", stocks)
    assert result["ok"] is True
    assert result["stock_count"] == 1


def test_run_sector_leadership_handles_non_dict_input():
    assert de.run_sector_leadership("X", None)["ok"] is False


def test_run_sector_leadership_all_dirty_returns_not_ok():
    stocks = {"A": make_ohlcv([None] * 25), "B": make_ohlcv([NAN] * 25)}
    assert de.run_sector_leadership("X", stocks)["ok"] is False
