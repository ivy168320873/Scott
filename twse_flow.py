"""Direct TWSE after-close institutional and margin data.

Unlike ``institutional_flow_engine`` (an OHLCV proxy), this adapter returns
official exchange-published fields and labels their date/unit explicitly.
Only TWSE-listed ``.TW`` symbols are supported; TPEX is reported as a gap.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests


_T86 = "https://www.twse.com.tw/rwd/zh/fund/T86"
_MARGIN = "https://www.twse.com.tw/rwd/zh/marginTrading/MI_MARGN"
_HEADERS = {
    "User-Agent": "ScottInstitutionalCore/3.0",
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


def _security_row(payload: dict, code: str) -> tuple[list, list]:
    """Find the per-security table instead of assuming it is table zero."""
    for fields, rows in _tables(payload):
        code_index = 0
        for index, field in enumerate(fields):
            label = str(field).replace(" ", "")
            if any(key in label for key in ("證券代號", "股票代號")):
                code_index = index
                break
        row = next(
            (
                item for item in rows
                if code_index < len(item) and str(item[code_index]).strip() == code
            ),
            None,
        )
        if row:
            return fields, row
    return [], []


def _column(fields: list, row: list, *keywords: str) -> float | None:
    target = "".join(str(keyword).replace(" ", "") for keyword in keywords)
    for index, field in enumerate(fields):
        label = str(field).replace(" ", "")
        if label == target and index < len(row):
            return _num(row[index])
    for index, field in enumerate(fields):
        label = str(field).replace(" ", "")
        if all(keyword in label for keyword in keywords) and index < len(row):
            return _num(row[index])
    return None


def _request(url: str, params: dict, *, session=None) -> dict:
    getter = session.get if session is not None else requests.get
    response = getter(url, params=params, headers=_HEADERS, timeout=10)
    response.raise_for_status()
    payload = response.json()
    return payload if isinstance(payload, dict) else {}


def _weekdays(now: datetime | None, count: int = 8) -> list[str]:
    current_at = now or datetime.now(timezone.utc)
    if current_at.tzinfo is None:
        current_at = current_at.replace(tzinfo=timezone.utc)
    current = current_at.astimezone(ZoneInfo("Asia/Taipei")).date()
    result = []
    while len(result) < count:
        if current.weekday() < 5:
            result.append(current.strftime("%Y%m%d"))
        current -= timedelta(days=1)
    return result


def _institutional(code: str, dates: list[str], *, session=None) -> dict | None:
    for day in dates:
        payload = _request(
            _T86,
            {"date": day, "selectType": "ALLBUT0999", "response": "json"},
            session=session,
        )
        if payload.get("stat") not in {"OK", None}:
            continue
        fields, row = _security_row(payload, code)
        if not row:
            continue
        foreign = _column(fields, row, "外資及陸資", "買賣超")
        if foreign is None:
            foreign = _column(fields, row, "外", "買賣超")
        trust = _column(fields, row, "投信", "買賣超")
        dealer = _column(fields, row, "自營商買賣超股數")
        total = _column(fields, row, "三大法人買賣超")
        if total is None:
            known = [value for value in (foreign, trust, dealer) if value is not None]
            total = sum(known) if known else None
        return {
            "date": f"{day[:4]}-{day[4:6]}-{day[6:]}",
            "unit": "shares",
            "foreign_net": foreign,
            "investment_trust_net": trust,
            "dealer_net": dealer,
            "total_net": total,
            "total_net_lots": round(total / 1000, 2) if total is not None else None,
            "source_url": _T86,
        }
    return None


def _margin(code: str, dates: list[str], *, session=None) -> dict | None:
    for day in dates:
        payload = _request(
            _MARGIN,
            {"date": day, "selectType": "ALL", "response": "json"},
            session=session,
        )
        if payload.get("stat") not in {"OK", None}:
            continue
        fields, row = _security_row(payload, code)
        if not row:
            continue
        margin_today = _column(fields, row, "融資", "今日餘額")
        margin_prev = _column(fields, row, "融資", "前日餘額")
        short_today = _column(fields, row, "融券", "今日餘額")
        short_prev = _column(fields, row, "融券", "前日餘額")
        # The official per-security table currently repeats generic headings:
        # columns 2-7 are margin financing and 8-13 are securities lending.
        if len(row) >= 13 and all(
            value is None
            for value in (margin_today, margin_prev, short_today, short_prev)
        ):
            margin_prev = _num(row[5])
            margin_today = _num(row[6])
            short_prev = _num(row[11])
            short_today = _num(row[12])
        if all(
            value is None
            for value in (margin_today, margin_prev, short_today, short_prev)
        ):
            continue
        return {
            "date": f"{day[:4]}-{day[4:6]}-{day[6:]}",
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


def get_twse_flow(symbol: str, *, session=None, now: datetime | None = None) -> dict:
    sym = str(symbol or "").upper().strip()
    if sym.endswith(".TWO"):
        return {
            "ok": False,
            "status": "UNSUPPORTED_TPEX",
            "symbol": sym,
            "is_direct": False,
            "warnings": ["上櫃 .TWO 需使用 TPEX 官方資料源，目前不以 TWSE 資料替代"],
        }
    if not sym.endswith(".TW") or not sym[:-3].isdigit():
        return {
            "ok": False,
            "status": "NOT_TWSE_SYMBOL",
            "symbol": sym,
            "is_direct": False,
            "warnings": [],
        }
    with _LOCK:
        cached = _CACHE.get(sym)
    if cached and time.monotonic() - cached["ts"] < _TTL:
        return dict(cached["data"])

    dates = _weekdays(now)
    errors = []
    institutional = margin = None
    try:
        institutional = _institutional(sym[:-3], dates, session=session)
    except Exception as exc:
        errors.append(f"T86: {str(exc)[:120]}")
    try:
        margin = _margin(sym[:-3], dates, session=session)
    except Exception as exc:
        errors.append(f"MI_MARGN: {str(exc)[:120]}")
    status = "OK" if institutional and margin else (
        "DEGRADED" if institutional or margin else "UNAVAILABLE"
    )
    result = {
        "ok": bool(institutional or margin),
        "status": status,
        "symbol": sym,
        "source": "TWSE_OFFICIAL",
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
