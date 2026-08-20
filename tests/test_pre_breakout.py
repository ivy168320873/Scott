from pre_breakout import rank_pre_breakout, score_pre_breakout


def _base_ohlcv(*, breakout=False, extended=False, weak=False):
    closes = []
    highs = []
    lows = []
    opens = []
    volumes = []
    price = 80.0
    for i in range(80):
        price *= 1.0025
        if i > 58:
            # Tight constructive base beneath ~100 resistance.
            price = 97.0 + (i - 58) * 0.10
        closes.append(price)
        opens.append(price * 0.998)
        # Keep a repeated pivot near 100 while the range contracts.
        tight = max(0.20, 1.1 - max(0, i - 55) * 0.035)
        highs.append(min(100.0, price + tight))
        lows.append(price - tight)
        volumes.append(1_500_000 if i < 55 else 900_000 - (i - 55) * 10_000)
    # Repeated tests of resistance.
    for idx in (-18, -13, -8, -4):
        highs[idx] = 100.0
    if breakout:
        closes[-1] = 100.3
        highs[-1] = 100.5
    if extended:
        closes[-1] = 105.0
        highs[-1] = 105.5
    if weak:
        closes[-1] = 88.0
        highs[-1] = 89.0
        lows[-1] = 87.0
    return {
        "closes": closes,
        "opens": opens,
        "highs": highs,
        "lows": lows,
        "volumes": volumes,
        "source": "test",
        "fetched_at": "2026-08-20T10:00:00+00:00",
        "is_demo": False,
    }


def test_constructive_base_scores_as_candidate():
    result = score_pre_breakout(_base_ohlcv(), market_score=80, sector_score=85, rs_score=90)
    assert result["score"] >= 75
    assert result["state"] in {"READY", "HIGH_PROBABILITY_CANDIDATE"}
    assert 0 <= result["distance_to_pivot_pct"] <= 3
    assert result["trigger_price"] > result["pivot"]
    assert result["invalidation_price"] < result["pivot"]
    assert result["components"]["pressure_absorption"] >= 60


def test_extended_move_is_penalized_not_called_ready():
    result = score_pre_breakout(_base_ohlcv(extended=True), market_score=90, sector_score=90, rs_score=95)
    assert result["score"] <= 55
    assert result["state"] == "NORMAL"
    assert "price already extended above pivot" in result["warnings"]


def test_demo_data_is_capped():
    data = _base_ohlcv()
    data["is_demo"] = True
    result = score_pre_breakout(data, market_score=90, sector_score=90, rs_score=95)
    assert result["score"] <= 45
    assert "demo data: readiness capped" in result["warnings"]


def test_ranker_isolates_bad_symbols_and_sorts_best_first():
    good = _base_ohlcv()
    weak = _base_ohlcv(weak=True)

    def provider(symbol):
        if symbol == "GOOD":
            return good
        if symbol == "WEAK":
            return weak
        return {"closes": [1, 2]}

    ranked = rank_pre_breakout(
        ["WEAK", "BAD", "GOOD"],
        provider,
        top_n=3,
        rs_scores={"GOOD": 90, "WEAK": 30},
    )
    assert ranked[0]["symbol"] == "GOOD"
    bad = next(row for row in ranked if row["symbol"] == "BAD")
    assert bad["state"] == "ERROR"
