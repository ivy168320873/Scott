"""Cross-sectional market breadth with explicit coverage and provenance.

This is a representative-universe breadth measure, not exchange-wide
advance/decline data.  The distinction is part of the returned contract so a
proxy can never be presented as a direct institutional feed.
"""

from __future__ import annotations

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed


US_UNIVERSE = (
    "RSP", "IWM", "MDY", "XLK", "XLF", "XLI",
    "XLY", "XLP", "XLE", "XLV", "XLU", "SMH",
)
TW_UNIVERSE = (
    "2330.TW", "2317.TW", "2454.TW", "2308.TW", "2382.TW", "2881.TW",
    "2882.TW", "2891.TW", "1301.TW", "1303.TW", "2002.TW", "1216.TW",
)

_CACHE: dict[tuple, dict] = {}
_LOCK = threading.Lock()
_TTL = 15 * 60


def _sma(values: list, period: int) -> float | None:
    valid = [float(value) for value in values[-period:] if value and float(value) > 0]
    return sum(valid) / len(valid) if len(valid) >= period else None


def _symbol_stats(symbol: str, data: dict | None) -> dict | None:
    if not isinstance(data, dict) or data.get("is_demo"):
        return None
    closes = [float(v) for v in data.get("closes", []) if v and float(v) > 0]
    if len(closes) < 21:
        return None
    price = closes[-1]
    prev = closes[-2]
    ma20 = _sma(closes, 20)
    ma50 = _sma(closes, 50)
    ma200 = _sma(closes, 200)
    window = closes[-min(252, len(closes)):]
    return {
        "symbol": symbol,
        "source": data.get("source", "unknown"),
        "price": round(price, 4),
        "advance": price > prev,
        "decline": price < prev,
        "change_1d_pct": round((price - prev) / prev * 100, 3) if prev else None,
        "above_ma20": price > ma20 if ma20 else None,
        "above_ma50": price > ma50 if ma50 else None,
        "above_ma200": price > ma200 if ma200 else None,
        "near_period_high": price >= max(window) * 0.995,
        "near_period_low": price <= min(window) * 1.005,
        "bar_count": len(closes),
    }


def _pct(rows: list[dict], key: str) -> float | None:
    judged = [row for row in rows if row.get(key) is not None]
    if not judged:
        return None
    return round(sum(1 for row in judged if row.get(key)) / len(judged) * 100, 1)


def _score(metrics: dict) -> int:
    weighted = []
    for key, weight in (
        ("pct_above_ma20", 0.20),
        ("pct_above_ma50", 0.30),
        ("pct_above_ma200", 0.20),
        ("advancers_pct", 0.20),
        ("high_low_balance", 0.10),
    ):
        value = metrics.get(key)
        if value is not None:
            weighted.append((float(value), weight))
    if not weighted:
        return 50
    return max(0, min(100, round(
        sum(value * weight for value, weight in weighted)
        / sum(weight for _, weight in weighted)
    )))


def _configured_universe(market: str) -> tuple[str, ...]:
    env_name = f"{market}_BREADTH_SYMBOLS"
    raw = os.environ.get(env_name, "")
    if raw.strip():
        values = []
        for item in raw.replace(";", ",").split(","):
            symbol = item.upper().strip()
            if symbol and symbol not in values:
                values.append(symbol)
        if values:
            return tuple(values[:40])
    return TW_UNIVERSE if market == "TW" else US_UNIVERSE


def run_market_breadth(
    ohlcv_fn,
    *,
    market: str = "US",
    symbols: list[str] | tuple[str, ...] | None = None,
    min_coverage: int = 6,
) -> dict:
    code = "TW" if str(market).upper() == "TW" else "US"
    universe = tuple(symbols or _configured_universe(code))
    key = (id(ohlcv_fn), code, universe)
    with _LOCK:
        cached = _CACHE.get(key)
    if cached and time.monotonic() - cached["ts"] < _TTL:
        return dict(cached["data"])

    rows: list[dict] = []
    errors: list[str] = []
    workers = min(6, max(1, len(universe)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        pending = {pool.submit(ohlcv_fn, symbol): symbol for symbol in universe}
        for future in as_completed(pending):
            symbol = pending[future]
            try:
                stats = _symbol_stats(symbol, future.result())
                if stats:
                    rows.append(stats)
            except Exception as exc:  # isolate providers/symbols
                if len(errors) < 3:
                    errors.append(f"{symbol}: {str(exc)[:80]}")

    rows.sort(key=lambda item: item["symbol"])
    advancers = sum(1 for row in rows if row["advance"])
    decliners = sum(1 for row in rows if row["decline"])
    active = advancers + decliners
    highs = sum(1 for row in rows if row["near_period_high"])
    lows = sum(1 for row in rows if row["near_period_low"])
    metrics = {
        "pct_above_ma20": _pct(rows, "above_ma20"),
        "pct_above_ma50": _pct(rows, "above_ma50"),
        "pct_above_ma200": _pct(rows, "above_ma200"),
        "advancers_pct": round(advancers / active * 100, 1) if active else None,
        "advance_decline_ratio": round(advancers / max(1, decliners), 3)
        if active else None,
        "near_highs": highs,
        "near_lows": lows,
        "high_low_balance": round((highs + 1) / (highs + lows + 2) * 100, 1),
    }
    coverage = len(rows)
    if coverage >= min_coverage:
        status = "OK"
    elif coverage >= 3:
        status = "DEGRADED"
    else:
        status = "INSUFFICIENT"
    warnings = []
    if status != "OK":
        warnings.append(f"市場廣度覆蓋不足：{coverage}/{len(universe)}")
    warnings.extend(errors)
    result = {
        "ok": True,
        "market": code,
        "status": status,
        "breadth_score": _score(metrics),
        "method": "REPRESENTATIVE_CROSS_SECTION",
        "is_exchange_wide": False,
        "coverage": coverage,
        "universe_size": len(universe),
        "coverage_pct": round(coverage / max(1, len(universe)) * 100, 1),
        "metrics": metrics,
        "symbols": rows,
        "sources": sorted({row["source"] for row in rows}),
        "warnings": warnings,
        "disclaimer": "代表性橫斷面廣度，非交易所全市場上漲／下跌家數。",
    }
    with _LOCK:
        _CACHE[key] = {"ts": time.monotonic(), "data": result}
    return dict(result)
