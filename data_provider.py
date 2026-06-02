"""
data_provider.py — Phase 10 unified OHLCV provider

Priority chain per symbol:
  1. yfinance  (yf.download — avoids the broken .info / .history path)
  2. Yahoo Finance v8 JSON chart API  (direct HTTP, no library)
  3. Alpha Vantage  (if ALPHA_VANTAGE_KEY env-var set)
  4. Finnhub        (if FINNHUB_KEY env-var set)
  5. TWSE / TPEX    (.TW / .TWO symbols)
  6. Demo seed data (last resort, flagged with is_demo=True)

Normalised output format (matches decision_engine.normalize_list):
  {
    "closes":     [float, ...],   # chronological, oldest first
    "opens":      [float, ...],
    "highs":      [float, ...],
    "lows":       [float, ...],
    "volumes":    [int, ...],
    "timestamps": [],
    "is_demo":    bool,
    "source":     str,            # 'yfinance' | 'yahoo_api' | 'alpha_vantage' |
                                  # 'finnhub' | 'twse' | 'demo'
  }

Public API
----------
get_ohlcv(symbol, period='1y') -> dict | None
get_ohlcv_multi(symbols, period='1y') -> dict[str, dict | None]
market_state(ohlcv_fn=None) -> dict   # SPY + QQQ regime
"""

from __future__ import annotations

import os
import time
import threading
import logging
from datetime import datetime, timezone, timedelta
from typing import Callable

import requests

logger = logging.getLogger(__name__)

# Suppress yfinance noise on demo/network failures
logging.getLogger("yfinance").setLevel(logging.CRITICAL)
logging.getLogger("peewee").setLevel(logging.CRITICAL)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_YAHOO_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
}

_CACHE: dict[str, dict] = {}
_CACHE_TTL = 15 * 60        # 15 minutes
_CACHE_LOCK = threading.Lock()

_DEFAULT_PERIOD = "1y"      # yfinance period string

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _now_ts() -> float:
    return time.monotonic()


def _cache_get(symbol: str) -> dict | None:
    with _CACHE_LOCK:
        entry = _CACHE.get(symbol.upper())
    if entry and (_now_ts() - entry["ts"]) < _CACHE_TTL:
        return entry["data"]
    return None


def _cache_set(symbol: str, data: dict) -> None:
    with _CACHE_LOCK:
        _CACHE[symbol.upper()] = {"ts": _now_ts(), "data": data}


def _normalize(rows: list[dict], source: str) -> dict | None:
    """Convert list[{open,high,low,close,volume}] → normalised dict."""
    valid = [r for r in rows if r.get("close", 0) > 0]
    if not valid:
        return None
    return {
        "closes":     [float(r["close"])          for r in valid],
        "opens":      [float(r.get("open",  r["close"])) for r in valid],
        "highs":      [float(r.get("high",  r["close"])) for r in valid],
        "lows":       [float(r.get("low",   r["close"])) for r in valid],
        "volumes":    [int(r.get("volume",  0))           for r in valid],
        "timestamps": [],
        "is_demo":    False,
        "source":     source,
    }


# ---------------------------------------------------------------------------
# Source 1 — yfinance (primary)
# ---------------------------------------------------------------------------

def _fetch_yfinance(symbol: str, period: str = _DEFAULT_PERIOD) -> dict | None:
    """
    Use yf.download() which hits query2.finance.yahoo.com/v8/finance/download
    and does NOT require the broken .info / .history path that triggers 403.
    """
    try:
        import yfinance as yf
        import warnings
        # yf.download returns a DataFrame indexed by Date
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            df = yf.download(symbol, period=period, auto_adjust=True, progress=False)
        if df is None or df.empty:
            return None
        rows = []
        for ts, row in df.iterrows():
            # Handle both flat and MultiIndex columns (yfinance ≥0.2.50)
            def _g(col: str) -> float:
                try:
                    v = row[col] if col in row.index else row.get(col, 0)
                    if hasattr(v, "__float__"):
                        return float(v)
                    return float(v.item()) if hasattr(v, "item") else 0.0
                except Exception:
                    return 0.0
            rows.append({
                "date":   ts.strftime("%Y-%m-%d"),
                "open":   _g("Open"),
                "high":   _g("High"),
                "low":    _g("Low"),
                "close":  _g("Close"),
                "volume": int(_g("Volume")),
            })
        return _normalize(rows, "yfinance")
    except Exception as exc:
        logger.debug("yfinance failed for %s: %s", symbol, exc)
        return None


# ---------------------------------------------------------------------------
# Source 2 — Yahoo Finance v8 chart API (direct HTTP)
# ---------------------------------------------------------------------------

def _fetch_yahoo_api(symbol: str, range_: str = "1y") -> dict | None:
    try:
        r = requests.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
            params={"range": range_, "interval": "1d", "events": "history"},
            headers=_YAHOO_HEADERS,
            timeout=12,
        )
        if r.status_code != 200:
            return None
        j = r.json()
        res = j["chart"]["result"][0]
        q = res["indicators"]["quote"][0]
        ts_list = res["timestamp"]
        rows = []
        for i, t in enumerate(ts_list):
            try:
                rows.append({
                    "date":   datetime.utcfromtimestamp(t).strftime("%Y-%m-%d"),
                    "open":   float(q["open"][i]   or 0),
                    "high":   float(q["high"][i]   or 0),
                    "low":    float(q["low"][i]    or 0),
                    "close":  float(q["close"][i]  or 0),
                    "volume": int(q["volume"][i]   or 0),
                })
            except (TypeError, ValueError, IndexError):
                continue
        return _normalize(rows, "yahoo_api")
    except Exception as exc:
        logger.debug("Yahoo API failed for %s: %s", symbol, exc)
        return None


# ---------------------------------------------------------------------------
# Source 3 — Alpha Vantage
# ---------------------------------------------------------------------------

def _fetch_alpha_vantage(symbol: str, api_key: str) -> dict | None:
    try:
        r = requests.get(
            "https://www.alphavantage.co/query",
            params={
                "function": "TIME_SERIES_DAILY_ADJUSTED",
                "symbol":   symbol,
                "outputsize": "full",
                "apikey":   api_key,
            },
            timeout=15,
        )
        if r.status_code != 200:
            return None
        j = r.json()
        series = j.get("Time Series (Daily)", {})
        if not series:
            return None
        rows = []
        for date_str in sorted(series.keys()):
            d = series[date_str]
            rows.append({
                "date":   date_str,
                "open":   float(d.get("1. open",            0)),
                "high":   float(d.get("2. high",            0)),
                "low":    float(d.get("3. low",             0)),
                "close":  float(d.get("5. adjusted close",  d.get("4. close", 0))),
                "volume": int(float(d.get("6. volume",      0))),
            })
        return _normalize(rows, "alpha_vantage")
    except Exception as exc:
        logger.debug("Alpha Vantage failed for %s: %s", symbol, exc)
        return None


# ---------------------------------------------------------------------------
# Source 4 — Finnhub
# ---------------------------------------------------------------------------

def _fetch_finnhub(symbol: str, api_key: str) -> dict | None:
    try:
        now   = int(time.time())
        from_ = now - 365 * 24 * 3600
        r = requests.get(
            "https://finnhub.io/api/v1/stock/candle",
            params={
                "symbol":     symbol,
                "resolution": "D",
                "from":       from_,
                "to":         now,
                "token":      api_key,
            },
            timeout=12,
        )
        if r.status_code != 200:
            return None
        j = r.json()
        if j.get("s") != "ok":
            return None
        rows = []
        for i, t in enumerate(j["t"]):
            rows.append({
                "date":   datetime.utcfromtimestamp(t).strftime("%Y-%m-%d"),
                "open":   float(j["o"][i]),
                "high":   float(j["h"][i]),
                "low":    float(j["l"][i]),
                "close":  float(j["c"][i]),
                "volume": int(j["v"][i]),
            })
        return _normalize(rows, "finnhub")
    except Exception as exc:
        logger.debug("Finnhub failed for %s: %s", symbol, exc)
        return None


# ---------------------------------------------------------------------------
# Source 5 — TWSE / TPEX (Taiwan stocks .TW / .TWO)
# ---------------------------------------------------------------------------

_TW_CACHE: dict[str, dict] = {}
_TW_TTL = 30 * 60


def _fetch_twse(symbol: str) -> dict | None:
    """Fetch from TWSE (上市) + TPEX (上櫃) open-data APIs."""
    sym_key = symbol.upper()
    cached = _TW_CACHE.get(sym_key)
    if cached and (time.time() - cached["ts"]) < _TW_TTL:
        return cached["data"]

    stock_no = sym_key.replace(".TWO", "").replace(".TW", "").strip()
    if not stock_no.isdigit():
        return None

    is_otc = sym_key.endswith(".TWO")

    months: list[str] = []
    now = datetime.now()
    for i in range(14):
        d = now - timedelta(days=i * 30)
        months.append(f"{d.year}{d.month:02d}01")

    rows: list[dict] = []

    for yyyymmdd in months:
        try:
            if is_otc:
                tw_date = f"{int(yyyymmdd[:4]) - 1911}/{yyyymmdd[4:6]}/01"
                r = requests.get(
                    "https://www.tpex.org.tw/web/stock/aftertrading/daily_trading_info/st43_result.php",
                    params={"d": tw_date, "stkno": stock_no, "l": "zh-tw"},
                    timeout=8,
                )
            else:
                r = requests.get(
                    "https://www.twse.com.tw/exchangeReport/STOCK_DAY",
                    params={"response": "json", "date": yyyymmdd, "stockNo": stock_no},
                    timeout=8,
                )
            if r.status_code != 200:
                continue
            j = r.json()
            data_key = "aaData" if is_otc else "data"
            raw_rows = j.get(data_key, [])
            for row in raw_rows:
                try:
                    if is_otc:
                        date_str_raw = row[0]
                        parts = date_str_raw.split("/")
                        yr = int(parts[0]) + 1911
                        date_str = f"{yr}-{parts[1]:>02s}-{parts[2]:>02s}"
                        close = float(str(row[4]).replace(",", ""))
                        open_ = float(str(row[3]).replace(",", ""))
                        high  = float(str(row[5]).replace(",", ""))
                        low   = float(str(row[6]).replace(",", ""))
                        vol   = int(str(row[1]).replace(",", "")) * 1000
                    else:
                        date_parts = row[0].split("/")
                        yr = int(date_parts[0]) + 1911
                        date_str = f"{yr}-{date_parts[1]:>02s}-{date_parts[2]:>02s}"
                        close = float(str(row[6]).replace(",", ""))
                        open_ = float(str(row[3]).replace(",", ""))
                        high  = float(str(row[4]).replace(",", ""))
                        low   = float(str(row[5]).replace(",", ""))
                        vol   = int(str(row[1]).replace(",", ""))
                    rows.append({
                        "date": date_str, "open": open_, "high": high,
                        "low": low, "close": close, "volume": vol,
                    })
                except (ValueError, IndexError):
                    continue
        except Exception:
            continue

    if not rows:
        return None

    rows.sort(key=lambda x: x["date"])
    result = _normalize(rows, "twse")
    if result:
        _TW_CACHE[sym_key] = {"ts": time.time(), "data": result}
    return result


# ---------------------------------------------------------------------------
# Source 6 — Demo seed data (absolute last resort)
# ---------------------------------------------------------------------------

def _fetch_demo(symbol: str, n: int = 300) -> dict:
    """
    Seeded random-walk demo data. Always succeeds.
    Clearly flagged with is_demo=True and source='demo'.
    """
    import hashlib
    import math

    seed = int(hashlib.md5(symbol.upper().encode()).hexdigest()[:8], 16) % 100_000
    base = 100.0 + (seed % 400)
    closes = [base]
    import random
    rng = random.Random(seed)
    for _ in range(n - 1):
        pct = rng.gauss(0, 0.015)
        closes.append(round(max(1.0, closes[-1] * (1 + pct)), 4))

    result = {
        "closes":     closes,
        "opens":      [round(c * rng.uniform(0.995, 1.005), 4) for c in closes],
        "highs":      [round(c * rng.uniform(1.000, 1.020), 4) for c in closes],
        "lows":       [round(c * rng.uniform(0.980, 1.000), 4) for c in closes],
        "volumes":    [rng.randint(500_000, 5_000_000) for _ in closes],
        "timestamps": [],
        "is_demo":    True,
        "source":     "demo",
    }
    return result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_ohlcv(symbol: str, period: str = _DEFAULT_PERIOD) -> dict | None:
    """
    Fetch OHLCV for `symbol`. Returns normalised dict or None.
    Never returns None — falls back to demo data as last resort.
    """
    sym = symbol.upper().strip()

    # 1. Cache hit
    cached = _cache_get(sym)
    if cached:
        return cached

    result: dict | None = None

    # 2. Taiwan stocks → TWSE first
    if sym.endswith(".TW") or sym.endswith(".TWO"):
        result = _fetch_twse(sym)
        if result:
            _cache_set(sym, result)
            return result
        # For TW stocks also try yfinance (has .TW support)
        result = _fetch_yfinance(sym, period)
        if result:
            _cache_set(sym, result)
            return result
    else:
        # US stocks: yfinance → Yahoo API → AV → Finnhub
        result = _fetch_yfinance(sym, period)
        if result:
            _cache_set(sym, result)
            return result

        result = _fetch_yahoo_api(sym)
        if result:
            _cache_set(sym, result)
            return result

        av_key = os.environ.get("ALPHA_VANTAGE_KEY", "")
        if av_key:
            result = _fetch_alpha_vantage(sym, av_key)
            if result:
                _cache_set(sym, result)
                return result

        fh_key = os.environ.get("FINNHUB_KEY", "")
        if fh_key:
            result = _fetch_finnhub(sym, fh_key)
            if result:
                _cache_set(sym, result)
                return result

    # Last resort: demo
    logger.warning("All live sources failed for %s — using demo data", sym)
    result = _fetch_demo(sym)
    _cache_set(sym, result)
    return result


def get_ohlcv_multi(
    symbols: list[str],
    period: str = _DEFAULT_PERIOD,
) -> dict[str, dict | None]:
    """Fetch multiple symbols concurrently. Returns {symbol: ohlcv_dict}."""
    import concurrent.futures

    results: dict[str, dict | None] = {}

    def _fetch_one(sym: str) -> tuple[str, dict | None]:
        return sym, get_ohlcv(sym, period)

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        for sym, data in ex.map(_fetch_one, [s.upper() for s in symbols]):
            results[sym] = data

    return results


def market_state(ohlcv_fn: Callable | None = None) -> dict:
    """
    Compute market state from SPY + QQQ.
    Returns: {overall, regime, indices, avg_change_1d_pct, is_demo}

    overall: '偏多' | '偏弱' | '風險升高' | '中性'
    regime:  'bull' | 'bear' | 'risk_on' | 'sideways'
    """
    fn = ohlcv_fn or get_ohlcv
    indices = []
    any_demo = False

    for sym in ("SPY", "QQQ"):
        try:
            ohlcv = fn(sym)
            if not ohlcv or not ohlcv.get("closes") or len(ohlcv["closes"]) < 22:
                indices.append({
                    "symbol": sym, "price": None,
                    "change_1d_pct": 0.0, "change_5d_pct": 0.0,
                    "vs_ma20": "unknown", "momentum_score": 50,
                    "is_demo": True,
                })
                any_demo = True
                continue

            closes = ohlcv["closes"]
            if ohlcv.get("is_demo"):
                any_demo = True

            change_1d = round((closes[-1] - closes[-2]) / closes[-2] * 100, 2) if closes[-2] else 0.0
            change_5d = round((closes[-1] - closes[-6]) / closes[-6] * 100, 2) if len(closes) >= 6 and closes[-6] else 0.0
            ma20 = sum(closes[-20:]) / 20
            vs_ma20 = "above" if closes[-1] > ma20 else "below"

            # Simple momentum score: % above/below MA20
            mom_raw = (closes[-1] - ma20) / ma20 * 100 if ma20 else 0
            momentum_score = min(100, max(0, int(50 + mom_raw * 3)))

            indices.append({
                "symbol":         sym,
                "price":          round(closes[-1], 2),
                "change_1d_pct":  change_1d,
                "change_5d_pct":  change_5d,
                "vs_ma20":        vs_ma20,
                "ma20":           round(ma20, 2),
                "momentum_score": momentum_score,
                "is_demo":        ohlcv.get("is_demo", False),
            })
        except Exception:
            indices.append({
                "symbol": sym, "price": None,
                "change_1d_pct": 0.0, "change_5d_pct": 0.0,
                "vs_ma20": "unknown", "momentum_score": 50,
                "is_demo": True,
            })
            any_demo = True

    above_count = sum(1 for i in indices if i["vs_ma20"] == "above")
    avg_1d  = sum(i["change_1d_pct"] for i in indices) / max(len(indices), 1)
    avg_5d  = sum(i["change_5d_pct"] for i in indices) / max(len(indices), 1)
    avg_mom = sum(i["momentum_score"] for i in indices) / max(len(indices), 1)

    # Classify
    if above_count == 2 and avg_mom >= 55 and avg_1d >= 0:
        overall, regime = "偏多",    "bull"
    elif above_count == 0 and avg_mom <= 45 and avg_1d <= 0:
        overall, regime = "偏弱",    "bear"
    elif avg_1d <= -1.5 or avg_5d <= -3.0:
        overall, regime = "風險升高", "risk_on"
    else:
        overall, regime = "中性",    "sideways"

    return {
        "overall":          overall,
        "regime":           regime,
        "indices":          indices,
        "avg_change_1d_pct": round(avg_1d, 2),
        "avg_change_5d_pct": round(avg_5d, 2),
        "is_demo":          any_demo,
    }


def cache_clear() -> None:
    """Force-clear the OHLCV in-memory cache (useful for testing)."""
    with _CACHE_LOCK:
        _CACHE.clear()
    _TW_CACHE.clear()
