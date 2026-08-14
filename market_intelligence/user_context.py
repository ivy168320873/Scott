"""Read and normalise the user's persisted portfolio and watchlist."""

from __future__ import annotations

import json
import os
import re
import sqlite3
from typing import Any

_SYMBOL_RE = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,19}$")


def _json(value: Any, fallback):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return fallback
    return value if value is not None else fallback


def _symbol(value: Any) -> str:
    symbol = str(value or "").upper().strip()
    return symbol if _SYMBOL_RE.fullmatch(symbol) else ""


def load_kv(db_path: str) -> dict:
    """Load the shared ``kv`` table without importing the Flask application."""
    if not db_path or not os.path.exists(db_path):
        return {}
    try:
        con = sqlite3.connect(db_path, timeout=10)
        con.execute("PRAGMA busy_timeout=10000")
        rows = con.execute("SELECT key, value FROM kv").fetchall()
        con.close()
    except sqlite3.Error:
        return {}
    result = {}
    for key, raw in rows:
        try:
            result[key] = json.loads(raw)
        except (TypeError, ValueError):
            continue
    return result


def _normalise_holding(item: Any) -> dict | None:
    if not isinstance(item, dict):
        return None
    if str(item.get("status", "open")).lower() not in {"", "open", "holding", "active"}:
        return None
    symbol = _symbol(item.get("symbol") or item.get("sym"))
    if not symbol:
        return None
    try:
        cost = float(
            item.get("cost", item.get("buyPrice", item.get("avg_entry", 0))) or 0
        )
        qty = float(item.get("qty", item.get("shares", 0)) or 0)
    except (TypeError, ValueError, OverflowError):
        cost = qty = 0.0
    return {
        "symbol": symbol,
        "cost": cost if cost > 0 else 0.0,
        "qty": qty if qty > 0 else 0.0,
        "buy_date": str(item.get("buy_date") or item.get("buyDate") or "")[:10],
        "sector": str(item.get("sector") or "")[:80],
    }


def _symbols(value: Any) -> list[str]:
    value = _json(value, [])
    if isinstance(value, str):
        value = value.split(",")
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        symbol = _symbol(item.get("symbol") if isinstance(item, dict) else item)
        if symbol and symbol not in result:
            result.append(symbol)
    return result


def extract_user_context(data: dict | None, *, max_symbols: int = 50) -> dict:
    """Support both the current browser keys and the older server keys.

    The mobile UI persists positions as ``portfolio_v1`` and symbols as
    ``radarWatchlist``.  Older reporting code expected ``holdings`` and
    ``watchlist``; accepting both keeps existing installations compatible.
    """
    data = data if isinstance(data, dict) else {}
    holding_candidates = []
    for key in ("portfolio_v1", "holdings"):
        raw = _json(data.get(key), [])
        if isinstance(raw, list):
            holding_candidates.extend(raw)

    holdings: list[dict] = []
    seen_holdings: set[str] = set()
    for item in holding_candidates:
        holding = _normalise_holding(item)
        if holding and holding["symbol"] not in seen_holdings:
            seen_holdings.add(holding["symbol"])
            holdings.append(holding)

    watchlist: list[str] = []
    for key in ("radarActive", "radarWatchlist", "watchlist", "customScanList"):
        for symbol in _symbols(data.get(key)):
            if symbol not in watchlist and symbol not in seen_holdings:
                watchlist.append(symbol)

    email = ""
    for key in ("alertSettings_v1", "settings"):
        settings = _json(data.get(key), {})
        if isinstance(settings, dict) and settings.get("email"):
            email = str(settings["email"]).strip()[:254]
            break
    if not email:
        email = (
            os.environ.get("ALERT_EMAIL_TO", "").strip()
            or os.environ.get("SMTP_USER", "").strip()
        )[:254]

    universe = [holding["symbol"] for holding in holdings]
    for symbol in watchlist:
        if symbol not in universe:
            universe.append(symbol)

    return {
        "holdings": holdings[:max_symbols],
        "holding_symbols": [holding["symbol"] for holding in holdings[:max_symbols]],
        "watchlist": watchlist[:max_symbols],
        "symbols": universe[:max_symbols],
        "email": email,
    }
