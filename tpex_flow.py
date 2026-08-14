"""Direct TPEX after-close institutional and margin data for ``.TWO``.

The adapter intentionally mirrors ``twse_flow`` while keeping exchange sources
separate.  Values are evidence-only until the strategy accumulates independent
calibration data; they never receive an untested score adjustment.
"""

from __future__ import annotations

import threading
import time
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests


_INSTITUTIONAL = (
    "https://www.tpex.org.tw/web/stock/3insti/daily_trade/"
    "3itrade_hedge_result.php"
)
_MARGIN = "https://www.tpex.org.tw/www/zh-tw/margin/balance"
_HEADERS = {
    "User-Agent": "ScottInstitutionalCore/4.0",
    "Accept": "application/json",
}
_CACHE: dict[str, dict] = {}
_LOCK = threading.Lock()
_TTL = 20 * 60


def _num(value) -> float | None:
    raw = str(value or "").replace(",", "").replace(" ", "").strip()
    if raw in {"", "--", "---", "X"}:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _tables(payload: dict) -> list[tuple[list, list]]:
    result = []
    if payload.get("fields") and payload.get("data"):
        result.append((payload["fields"], payload["data"]))
    for table in payload.get("tables") or []:
        if table.get("fields") and table.get("data"):
            result.append((table["fields"], table["data"]))
    return result


def _row(payload: dict, code: str) -> tuple[list, list]:
    for fields, rows in _tables(payload):
        for item in rows:
            if item and str(item[0]).strip() == code:
                return fields, item
    return [], []


def _request(url: str, params: dict, *, session=None) -> dict:
    getter = session.get if session is not None else requests.get
    response = getter(url, params=params, headers=_HEADERS, timeout=12)
    response.raise_for_status()
    payload = response.json()
    return payload if isinstance(payload, dict) else {}


def _candidate_days(now: datetime | None, count: int = 8) -> list[date]:
    current_at = now or datetime.now(timezone.utc)
    if current_at.tzinfo is None:
        current_at = current_at.replace(tzinfo=timezone.utc)
    current = current_at.astimezone(ZoneInfo("Asia/Taipei")).date()
    result = []
    while len(result) < count:
        if current.weekday() < 5:
            result.append(current)
        current -= timedelta(days=1)
    return result


def _institutional(code: str, days: list[date], *, session=None) -> dict | None:
    for day in days:
        roc_day = f"{day.year - 1911:03d}/{day.month:02d}/{day.day:02d}"
        payload = _request(
            _INSTITUTIONAL,
            {"l": "zh-tw", "o": "json", "se": "EW", "t": "D", "d": roc_day},
            session=session,
        )
        _fields, row = _row(payload, code)
        if len(row) < 24:
            continue
        # Official CSV/JSON positions: foreign total net=10, investment trust
        # net=13, dealer total net=22, all institutions net=23.
        foreign = _num(row[10])
        trust = _num(row[13])
        dealer = _num(row[22])
        total = _num(row[23])
        if total is None:
            known = [value for value in (foreign, trust, dealer) if value is not None]
            total = sum(known) if known else None
        if all(value is None for value in (foreign, trust, dealer, total)):
            continue
        return {
            "date": day.isoformat(),
            "unit": "shares",
            "foreign_net": foreign,
            "investment_trust_net": trust,
            "dealer_net": dealer,
            "total_net": total,
            "total_net_lots": round(total / 1000, 2) if total is not None else None,
            "source_url": _INSTITUTIONAL,
        }
    return None


def _margin(code: str, days: list[date], *, session=None) -> dict | None:
    for day in days:
        payload = _request(
            _MARGIN,
            {"date": day.strftime("%Y/%m/%d"), "response": "json"},
            session=session,
        )
        _fields, row = _row(payload, code)
        if len(row) < 15:
            continue
        # Current TPEX schema: prior/current margin at 2/6; prior/current short
        # at 10/14.  TPEX publishes per-security balances in lots.
        margin_prev = _num(row[2])
        margin_today = _num(row[6])
        short_prev = _num(row[10])
        short_today = _num(row[14])
        if all(
            value is None
            for value in (margin_prev, margin_today, short_prev, short_today)
        ):
            continue
        return {
            "date": day.isoformat(),
            "unit": "lots",
            "margin_balance": margin_today,
            "margin_change": (
                margin_today - margin_prev
                if margin_today is not None and margin_prev is not None
                else None
            ),
            "short_balance": short_today,
            "short_change": (
                short_today - short_prev
                if short_today is not None and short_prev is not None
                else None
            ),
            "source_url": _MARGIN,
        }
    return None


def get_tpex_flow(symbol: str, *, session=None, now: datetime | None = None) -> dict:
    sym = str(symbol or "").upper().strip()
    if not sym.endswith(".TWO") or not sym[:-4].isdigit():
        return {
            "ok": False,
            "status": "NOT_TPEX_SYMBOL",
            "symbol": sym,
            "is_direct": False,
            "warnings": [],
        }
    with _LOCK:
        cached = _CACHE.get(sym)
    if cached and time.monotonic() - cached["ts"] < _TTL:
        return dict(cached["data"])

    days = _candidate_days(now)
    errors = []
    institutional = margin = None
    try:
        institutional = _institutional(sym[:-4], days, session=session)
    except Exception as exc:  # noqa: BLE001 - source degradation is explicit
        errors.append(f"TPEX institutional: {str(exc)[:120]}")
    try:
        margin = _margin(sym[:-4], days, session=session)
    except Exception as exc:  # noqa: BLE001 - source degradation is explicit
        errors.append(f"TPEX margin: {str(exc)[:120]}")
    status = "OK" if institutional and margin else (
        "DEGRADED" if institutional or margin else "UNAVAILABLE"
    )
    result = {
        "ok": bool(institutional or margin),
        "status": status,
        "symbol": sym,
        "source": "TPEX_OFFICIAL",
        "is_direct": True,
        "update_frequency": "AFTER_CLOSE",
        "institutional": institutional,
        "margin": margin,
        "warnings": errors,
        "fetched_at": (now or datetime.now(timezone.utc)).isoformat(),
        "score_adjustment": 0,
        "note": "直接資料僅作證據展示；在完成獨立校準前不改變決策分數。",
    }
    with _LOCK:
        _CACHE[sym] = {"ts": time.monotonic(), "data": result}
    return dict(result)

