from __future__ import annotations

import breakout_watch


def _ohlcv() -> dict:
    closes = []
    opens = []
    highs = []
    lows = []
    volumes = []

    # Long uptrend, then a tight consolidation just under resistance.
    for i in range(70):
        close = 70 + i * 0.42
        closes.append(close)
        opens.append(close - 0.15)
        highs.append(close + 0.9)
        lows.append(close - 0.9)
        volumes.append(1_000_000 + i * 3_000)

    base = closes[-1]
    tight = [
        base + 0.10, base + 0.28, base + 0.35, base + 0.42, base + 0.50,
        base + 0.46, base + 0.55, base + 0.62, base + 0.58, base + 0.70,
        base + 0.72, base + 0.78, base + 0.80, base + 0.83, base + 0.86,
        base + 0.90, base + 0.94, base + 0.97, base + 1.00, base + 1.02,
    ]
    for i, close in enumerate(tight):
        closes.append(close)
        opens.append(close - 0.08)
        highs.append(close + 0.24)
        lows.append(close - 0.24)
        volumes.append(1_250_000 + i * 8_000)

    return {
        "closes": closes,
        "opens": opens,
        "highs": highs,
        "lows": lows,
        "volumes": volumes,
        "source": "test",
        "is_demo": False,
    }


def test_high_quality_candidate_has_actionable_levels(monkeypatch):
    monkeypatch.setattr(
        breakout_watch.relative_strength_score,
        "compute",
        lambda *_args, **_kwargs: {"score": 91},
    )
    data = _ohlcv()
    candidate = breakout_watch.evaluate_candidate(
        "CRDO",
        data,
        bench_ohlcv=data,
        market={"regime": "bull", "overall": "偏多"},
        catalyst={
            "symbol": "CRDO",
            "direction": "BULLISH",
            "score_adjustment": 6,
            "importance": 85,
            "confidence": 82,
            "summary": "AI networking demand and guidance improved",
        },
        events=[{
            "title": "CRDO earnings guidance raised",
            "importance": 88,
            "confidence": 85,
            "direction": "BULLISH",
        }],
        min_score=80,
    )

    assert candidate is not None
    assert candidate["score"] >= 80
    assert candidate["entry_price"] > candidate["price"]
    assert candidate["invalid_price"] < candidate["entry_price"]
    assert 0 < candidate["risk_pct"] <= 9
    assert "成交量至少達 20 日均量 1.3×" in candidate["entry_condition"]
    assert candidate["reasons"]


def test_bear_market_suppresses_candidate(monkeypatch):
    monkeypatch.setattr(
        breakout_watch.relative_strength_score,
        "compute",
        lambda *_args, **_kwargs: {"score": 95},
    )
    assert breakout_watch.evaluate_candidate(
        "NVDA",
        _ohlcv(),
        bench_ohlcv=_ohlcv(),
        market={"regime": "bear", "overall": "偏弱"},
        catalyst={"direction": "BULLISH", "importance": 90, "confidence": 90},
        min_score=75,
    ) is None


def test_no_signal_means_no_notification():
    result = breakout_watch.dispatch_breakout_watch({"candidates": []})
    assert result["sent"] == []
    assert result["skipped"] == "NO_HIGH_QUALITY_SIGNAL"
