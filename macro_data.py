"""Direct macro series from FRED with a neutral, explicit no-key fallback."""

from __future__ import annotations

import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

import requests


_URL = "https://api.stlouisfed.org/fred/series/observations"
_SERIES = {
    "us_2y_yield": "DGS2",
    "us_10y_yield": "DGS10",
    "high_yield_spread": "BAMLH0A0HYM2",
    "trade_weighted_usd": "DTWEXBGS",
}
_CACHE: dict[tuple, dict] = {}
_LOCK = threading.Lock()
_TTL = 30 * 60


def _series(name: str, series_id: str, api_key: str, *, session=None) -> tuple[str, dict]:
    getter = session.get if session is not None else requests.get
    response = getter(
        _URL,
        params={
            "series_id": series_id,
            "api_key": api_key,
            "file_type": "json",
            "observation_start": (datetime.now(timezone.utc) - timedelta(days=120)).date().isoformat(),
            "sort_order": "asc",
        },
        headers={"Accept": "application/json"},
        timeout=12,
    )
    response.raise_for_status()
    values = []
    for item in response.json().get("observations", []):
        try:
            value = float(item.get("value"))
        except (TypeError, ValueError):
            continue
        values.append((str(item.get("date") or "")[:10], value))
    if not values:
        raise RuntimeError(f"{series_id} 無有效觀測值")
    previous = values[-21][1] if len(values) >= 21 else values[0][1]
    return name, {
        "series_id": series_id,
        "date": values[-1][0],
        "value": round(values[-1][1], 4),
        "change_20_obs": round(values[-1][1] - previous, 4),
        "observations": len(values),
        "source": "FRED",
    }


def _risk_score(series: dict) -> tuple[int, list[str]]:
    reasons = []
    two = (series.get("us_2y_yield") or {}).get("value")
    ten = (series.get("us_10y_yield") or {}).get("value")
    spread = (series.get("high_yield_spread") or {}).get("value")
    components = []
    if two is not None and ten is not None:
        curve = ten - two
        curve_score = 72 if curve >= 0.5 else 60 if curve >= 0 else 38 if curve >= -0.5 else 22
        components.append((curve_score, 0.4))
        reasons.append(f"10Y-2Y 利差 {curve:+.2f} 個百分點")
    if spread is not None:
        spread_score = 82 if spread < 3 else 68 if spread < 4 else 50 if spread < 5 else 30 if spread < 7 else 12
        spread_change = (series.get("high_yield_spread") or {}).get("change_20_obs") or 0
        if spread_change > 0.5:
            spread_score -= 12
        components.append((max(0, spread_score), 0.6))
        reasons.append(f"美國高收益債利差 {spread:.2f}%（20期 {spread_change:+.2f}）")
    if not components:
        return 50, ["直接利率／信用利差資料不足"]
    return round(
        sum(score * weight for score, weight in components)
        / sum(weight for _, weight in components)
    ), reasons


def get_macro_snapshot(*, session=None) -> dict:
    api_key = os.environ.get("FRED_API_KEY", "").strip()
    now = datetime.now(timezone.utc).isoformat()
    if not api_key:
        return {
            "ok": False,
            "status": "NOT_CONFIGURED",
            "as_of": now,
            "series": {},
            "rates_credit_score": 50,
            "reasons": ["FRED_API_KEY 未設定，利率與信用利差採中性、不加分"],
            "warnings": ["缺少直接 FRED 總經資料"],
        }

    cache_key = (hash(api_key), id(session))
    with _LOCK:
        cached = _CACHE.get(cache_key)
    if cached and time.monotonic() - cached["ts"] < _TTL:
        return dict(cached["data"])

    results: dict[str, dict] = {}
    errors = []
    with ThreadPoolExecutor(max_workers=len(_SERIES)) as pool:
        pending = {
            pool.submit(_series, name, series_id, api_key, session=session): name
            for name, series_id in _SERIES.items()
        }
        for future in as_completed(pending):
            name = pending[future]
            try:
                key, value = future.result()
                results[key] = value
            except Exception as exc:
                errors.append(f"{name}: {str(exc)[:120]}")
    score, reasons = _risk_score(results)
    status = "OK" if len(results) == len(_SERIES) else (
        "DEGRADED" if results else "UNAVAILABLE"
    )
    result = {
        "ok": bool(results),
        "status": status,
        "as_of": now,
        "series": results,
        "rates_credit_score": score,
        "reasons": reasons,
        "warnings": errors,
    }
    with _LOCK:
        _CACHE[cache_key] = {"ts": time.monotonic(), "data": result}
    return dict(result)
