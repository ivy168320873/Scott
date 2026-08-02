"""Unified data-provider tests with all external calls stubbed."""

import data_provider as dp


def test_normalize_includes_aligned_timestamps():
    rows = [
        {"date": "2025-01-02", "open": 10, "high": 11, "low": 9, "close": 10.5, "volume": 1000},
        {"date": "2025-01-03", "open": 10.5, "high": 12, "low": 10, "close": 11, "volume": 2000},
    ]
    out = dp._normalize(rows, "unit_test")
    assert out is not None
    assert out["closes"] == [10.5, 11.0]
    assert len(out["timestamps"]) == len(out["closes"])
    assert all(out["timestamps"])


def test_normalize_filters_nonpositive_close():
    out = dp._normalize([{"close": 0}, {"close": 5.0}], "x")
    assert out["closes"] == [5.0]
    assert dp._normalize([], "x") is None


def test_cache_is_isolated_by_period():
    dp._CACHE.clear()
    dp._cache_set("AAPL", {"source": "one-year"}, "1y")
    assert dp._cache_get("AAPL", "1y")["source"] == "one-year"
    assert dp._cache_get("AAPL", "6mo") is None


def _disable_live_sources(monkeypatch):
    monkeypatch.setattr(dp, "_fetch_yfinance", lambda *args, **kwargs: None)
    monkeypatch.setattr(dp, "_fetch_yahoo_api", lambda *args, **kwargs: None)
    monkeypatch.delenv("ALPHA_VANTAGE_KEY", raising=False)
    monkeypatch.delenv("FINNHUB_KEY", raising=False)
    dp._CACHE.clear()


def test_live_failure_is_fail_closed_by_default(monkeypatch):
    _disable_live_sources(monkeypatch)
    monkeypatch.delenv("ALLOW_DEMO_DATA", raising=False)
    assert dp.get_ohlcv("UNITTEST", "1y") is None


def test_demo_requires_explicit_opt_in(monkeypatch):
    _disable_live_sources(monkeypatch)
    monkeypatch.setenv("ALLOW_DEMO_DATA", "true")
    result = dp.get_ohlcv("UNITTEST", "3mo")
    assert result["is_demo"] is True
    assert result["source"] == "demo"


def test_taiwan_long_history_prefers_adjusted_yahoo(monkeypatch):
    dp._CACHE.clear()
    calls = []

    def fake_yfinance(symbol, period):
        calls.append(("yfinance", symbol, period))
        return {"closes": [1.0, 2.0], "source": "yfinance", "is_demo": False}

    monkeypatch.setattr(dp, "_fetch_yfinance", fake_yfinance)
    monkeypatch.setattr(dp, "_fetch_twse", lambda symbol: (_ for _ in ()).throw(
        AssertionError("TWSE fallback must not replace available adjusted history")
    ))

    result = dp.get_ohlcv("2330.TW", "5y")

    assert result["source"] == "yfinance"
    assert calls == [("yfinance", "2330.TW", "5y")]


def test_market_state_accepts_injected_data():
    state = dp.market_state(lambda symbol, period="1y": dp._fetch_demo(symbol, n=260))
    assert state["is_demo"] is True
    assert state["regime"] in {"bull", "bear", "risk_on", "sideways"}
