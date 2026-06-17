"""data_provider 純函式的單元測試（不需網路）。"""

import data_provider as dp


def test_normalize_basic():
    rows = [
        {"open": 10, "high": 11, "low": 9, "close": 10.5, "volume": 1000},
        {"open": 10.5, "high": 12, "low": 10, "close": 11.0, "volume": 2000},
    ]
    out = dp._normalize(rows, "unit_test")
    assert out is not None
    assert out["closes"] == [10.5, 11.0]
    assert out["is_demo"] is False
    assert out["source"] == "unit_test"
    assert len(out["highs"]) == len(out["closes"])


def test_normalize_filters_nonpositive_close():
    rows = [
        {"close": 0, "volume": 1},      # 應被濾掉
        {"close": 5.0, "volume": 1},
    ]
    out = dp._normalize(rows, "x")
    assert out["closes"] == [5.0]


def test_normalize_empty_returns_none():
    assert dp._normalize([], "x") is None
    assert dp._normalize([{"close": 0}], "x") is None


def test_fetch_demo_is_flagged():
    d = dp._fetch_demo("ANYTHING", n=50)
    assert d["is_demo"] is True
    assert d["source"] == "demo"
    assert len(d["closes"]) == 50
    assert all(c > 0 for c in d["closes"])


def test_market_state_with_injected_data():
    # 注入合成 OHLCV，避免任何網路呼叫。
    def fake_ohlcv(symbol, period="1y"):
        return dp._fetch_demo(symbol, n=260)

    ms = dp.market_state(ohlcv_fn=fake_ohlcv)
    assert isinstance(ms, dict)
    assert "overall" in ms
