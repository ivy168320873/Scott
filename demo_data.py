"""Generates deterministic sample OHLCV data for demo mode."""
import numpy as np
import pandas as pd
from datetime import date, timedelta

SEEDS = {
    "NVDA":    dict(seed=42,  start=400,  drift=0.0015, vol=0.025, name="NVIDIA Corporation"),
    "AAPL":    dict(seed=7,   start=170,  drift=0.0005, vol=0.014, name="Apple Inc."),
    "MSFT":    dict(seed=13,  start=380,  drift=0.0006, vol=0.013, name="Microsoft Corporation"),
    "TSLA":    dict(seed=99,  start=220,  drift=0.0002, vol=0.033, name="Tesla, Inc."),
    "AMZN":    dict(seed=55,  start=175,  drift=0.0007, vol=0.016, name="Amazon.com, Inc."),
    "GOOGL":   dict(seed=27,  start=155,  drift=0.0005, vol=0.015, name="Alphabet Inc."),
    "META":    dict(seed=33,  start=490,  drift=0.0008, vol=0.018, name="Meta Platforms, Inc."),
    "2330.TW": dict(seed=88,  start=700,  drift=0.0009, vol=0.020, name="台積電 TSMC"),
    "2317.TW": dict(seed=64,  start=145,  drift=0.0003, vol=0.018, name="鴻海精密"),
    "0050.TW": dict(seed=11,  start=155,  drift=0.0004, vol=0.013, name="元大台灣50"),
    "SPY":     dict(seed=21,  start=490,  drift=0.0004, vol=0.010, name="SPDR S&P 500 ETF"),
    "QQQ":     dict(seed=17,  start=430,  drift=0.0005, vol=0.012, name="Invesco QQQ Trust"),
}

DEFAULT = dict(seed=1, start=100, drift=0.0003, vol=0.020, name=None)


def generate(symbol: str, n: int = 252) -> pd.DataFrame:
    cfg = SEEDS.get(symbol.upper(), DEFAULT)
    rng = np.random.default_rng(cfg["seed"] + hash(symbol) % 1000)
    log_returns = rng.normal(cfg["drift"], cfg["vol"], n)
    prices = cfg["start"] * np.exp(np.cumsum(log_returns))

    # OHLCV from closes
    noise = lambda: 1 + rng.uniform(-cfg["vol"] * 0.5, cfg["vol"] * 0.5, n)
    highs = prices * (1 + np.abs(rng.normal(0, cfg["vol"] * 0.5, n)))
    lows = prices * (1 - np.abs(rng.normal(0, cfg["vol"] * 0.5, n)))
    opens = np.roll(prices, 1) * noise()
    opens[0] = prices[0] * (1 - cfg["drift"])
    volumes = rng.integers(int(1e6), int(5e7), n).astype(float)

    # Build business-day index ending today
    today = date.today()
    bdays = pd.bdate_range(end=today, periods=n)
    df = pd.DataFrame({
        "Open": opens, "High": highs, "Low": lows,
        "Close": prices, "Volume": volumes
    }, index=bdays)
    return df


def name(symbol: str) -> str:
    cfg = SEEDS.get(symbol.upper(), DEFAULT)
    return cfg.get("name") or symbol.upper()
