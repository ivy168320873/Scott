"""Exchange-day calendars used by signal and completed-bar guardrails.

The US calendar is generated from NYSE holiday rules and supports explicit
emergency overrides.  Taiwan holidays are refreshed from the official TWSE
schedule in production and cached for the process lifetime.  A failed remote
refresh never silently claims official precision: callers receive the fallback
source and a warning.
"""

from __future__ import annotations

import os
import re
import threading
import time
from collections.abc import Mapping
from datetime import date, datetime, timedelta, timezone

import requests


_TWSE_HOLIDAY_URL = (
    "https://www.twse.com.tw/rwd/zh/holidaySchedule/holidaySchedule"
)
_HEADERS = {
    "User-Agent": "ScottInstitutionalCore/4.0 calendar-guard",
    "Accept": "application/json",
}
_CACHE: dict[int, dict] = {}
_CACHE_LOCK = threading.Lock()
_CACHE_TTL_SECONDS = 24 * 60 * 60
_FALSE_VALUES = {"0", "false", "no", "off"}
_TRUE_VALUES = {"1", "true", "yes", "on"}


def _nth_weekday(year: int, month: int, weekday: int, occurrence: int) -> date:
    current = date(year, month, 1)
    offset = (weekday - current.weekday()) % 7
    return current + timedelta(days=offset + 7 * (occurrence - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    if month == 12:
        current = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        current = date(year, month + 1, 1) - timedelta(days=1)
    return current - timedelta(days=(current.weekday() - weekday) % 7)


def _easter(year: int) -> date:
    """Gregorian Easter Sunday (Meeus/Jones/Butcher algorithm)."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = (h + l - 7 * m + 114) % 31 + 1
    return date(year, month, day)


def _observed(day: date, *, saturday_to_friday: bool = True) -> date:
    if day.weekday() == 6:
        return day + timedelta(days=1)
    if day.weekday() == 5 and saturday_to_friday:
        return day - timedelta(days=1)
    return day


def us_calendar(year: int) -> dict[date, dict]:
    """Return standard NYSE full closures and scheduled early closes."""
    holidays: dict[date, dict] = {}

    def close(day: date, name: str) -> None:
        holidays[day] = {"is_trading_day": False, "holiday_name": name}

    # NYSE does not move a Saturday New Year's Day back to Friday.
    close(_observed(date(year, 1, 1), saturday_to_friday=False), "New Year's Day")
    close(_nth_weekday(year, 1, 0, 3), "Martin Luther King Jr. Day")
    close(_nth_weekday(year, 2, 0, 3), "Washington's Birthday")
    close(_easter(year) - timedelta(days=2), "Good Friday")
    close(_last_weekday(year, 5, 0), "Memorial Day")
    if year >= 2022:
        close(_observed(date(year, 6, 19)), "Juneteenth National Independence Day")
    close(_observed(date(year, 7, 4)), "Independence Day")
    close(_nth_weekday(year, 9, 0, 1), "Labor Day")
    thanksgiving = _nth_weekday(year, 11, 3, 4)
    close(thanksgiving, "Thanksgiving Day")
    close(_observed(date(year, 12, 25)), "Christmas Day")

    early_candidates = {
        date(year, 7, 3): "Independence Day early close",
        thanksgiving + timedelta(days=1): "Day after Thanksgiving early close",
        date(year, 12, 24): "Christmas Eve early close",
    }
    for day, name in early_candidates.items():
        if day.weekday() < 5 and day not in holidays:
            holidays[day] = {
                "is_trading_day": True,
                "early_close": True,
                "close_time": "13:00",
                "holiday_name": name,
            }
    return holidays


def _date_list(value: str) -> set[date]:
    result: set[date] = set()
    for raw in str(value or "").split(","):
        try:
            result.add(date.fromisoformat(raw.strip()))
        except ValueError:
            continue
    return result


def _remote_enabled(env: Mapping[str, str]) -> bool:
    explicit = str(env.get("MARKET_CALENDAR_REMOTE_ENABLE", "")).strip().lower()
    if explicit in _TRUE_VALUES:
        return True
    if explicit in _FALSE_VALUES:
        return False
    return str(env.get("RAILWAY_ENVIRONMENT", "")).strip().lower() in {
        "production",
        "prod",
    } or str(env.get("RAILWAY_ENVIRONMENT_NAME", "")).strip().lower() in {
        "production",
        "prod",
    }


def parse_twse_holidays(payload: dict, year: int) -> dict[date, dict]:
    """Parse the official TWSE ROC-year holiday JSON into Gregorian dates."""
    fields = payload.get("fields") or []
    rows = payload.get("data") or []
    if not rows:
        for table in payload.get("tables") or []:
            if table.get("data"):
                fields = table.get("fields") or fields
                rows = table["data"]
                break
    date_index = next(
        (index for index, field in enumerate(fields) if "日期" in str(field)), 0
    )
    name_index = next(
        (index for index, field in enumerate(fields) if "名稱" in str(field)), 1
    )
    note_index = next(
        (index for index, field in enumerate(fields) if "說明" in str(field)), 2
    )
    result: dict[date, dict] = {}
    for row in rows:
        if not isinstance(row, (list, tuple)) or date_index >= len(row):
            continue
        raw_day = str(row[date_index]).strip()
        try:
            iso_match = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", raw_day)
            local_match = re.search(r"(\d{1,2})\s*月\s*(\d{1,2})\s*日", raw_day)
            if iso_match:
                day = date(
                    int(iso_match.group(1)),
                    int(iso_match.group(2)),
                    int(iso_match.group(3)),
                )
            elif local_match:
                day = date(year, int(local_match.group(1)), int(local_match.group(2)))
            else:
                continue
        except ValueError:
            continue
        name = str(row[name_index] if name_index < len(row) else "").strip()
        note = str(row[note_index] if note_index < len(row) else "").strip()
        label = " ".join(part for part in (name, note) if part)
        # The schedule also lists the first/last *trading* day as information;
        # those rows are explicitly open and must not be classified as holidays.
        is_open_marker = any(marker in label for marker in ("開始交易日", "最後交易日"))
        result[day] = {
            "is_trading_day": bool(is_open_marker),
            "holiday_name": None if is_open_marker else (name or note or "TWSE scheduled closure"),
            "note": note or None,
        }
    return result


def _fetch_twse_year(year: int, *, session=None) -> dict[date, dict]:
    getter = session.get if session is not None else requests.get
    response = getter(
        _TWSE_HOLIDAY_URL,
        params={"response": "json", "queryYear": str(year - 1911)},
        headers=_HEADERS,
        timeout=12,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError("TWSE holiday schedule returned a non-object payload")
    parsed = parse_twse_holidays(payload, year)
    if not parsed:
        raise RuntimeError("TWSE holiday schedule contained no parseable dates")
    return parsed


def _twse_year(year: int, *, session=None) -> tuple[dict[date, dict], str | None]:
    now_mono = time.monotonic()
    with _CACHE_LOCK:
        cached = _CACHE.get(year)
    if cached and now_mono - cached["ts"] < _CACHE_TTL_SECONDS:
        return cached["calendar"], None
    try:
        calendar = _fetch_twse_year(year, session=session)
    except Exception as exc:  # noqa: BLE001 - precision is reported to caller
        return {}, f"TWSE 官方休市日曆更新失敗：{str(exc)[:120]}"
    with _CACHE_LOCK:
        _CACHE[year] = {"ts": now_mono, "calendar": calendar}
    return calendar, None


def exchange_day(
    market: str,
    day: date,
    *,
    env: Mapping[str, str] | None = None,
    session=None,
) -> dict:
    """Return whether ``day`` is tradable, with precision and source metadata."""
    values = os.environ if env is None else env
    code = str(market or "US").upper()
    if code not in {"US", "TW"}:
        code = "US"
    warnings: list[str] = []
    closed_overrides = _date_list(values.get(f"MARKET_CLOSED_DATES_{code}", ""))
    open_overrides = _date_list(values.get(f"MARKET_OPEN_DATES_{code}", ""))
    if day in closed_overrides:
        return {
            "market": code,
            "date": day.isoformat(),
            "is_trading_day": False,
            "early_close": False,
            "close_time": None,
            "holiday_name": "Manual emergency closure",
            "source": "ENV_OVERRIDE",
            "source_url": None,
            "precision": "EXPLICIT_OVERRIDE",
            "verified": True,
            "warnings": [],
        }
    if day in open_overrides:
        return {
            "market": code,
            "date": day.isoformat(),
            "is_trading_day": True,
            "early_close": False,
            "close_time": None,
            "holiday_name": None,
            "source": "ENV_OVERRIDE",
            "source_url": None,
            "precision": "EXPLICIT_OVERRIDE",
            "verified": True,
            "warnings": [],
        }

    weekday = day.weekday() < 5
    if code == "US":
        rule = us_calendar(day.year).get(day, {})
        return {
            "market": code,
            "date": day.isoformat(),
            "is_trading_day": bool(rule.get("is_trading_day", weekday)),
            "early_close": bool(rule.get("early_close", False)),
            "close_time": rule.get("close_time"),
            "holiday_name": rule.get("holiday_name"),
            "source": "NYSE_RULES",
            "source_url": "https://www.nyse.com/trade/hours-calendars",
            "precision": "RULE_BASED_WITH_EMERGENCY_OVERRIDES",
            "verified": True,
            "warnings": [
                "臨時全市場休市需透過 MARKET_CLOSED_DATES_US 覆寫"
            ],
        }

    official: dict[date, dict] = {}
    error = None
    if _remote_enabled(values):
        official, error = _twse_year(day.year, session=session)
        if error:
            warnings.append(error)
    rule = official.get(day)
    if rule is not None:
        is_trading_day = bool(rule.get("is_trading_day", weekday)) and weekday
        precision = "OFFICIAL_SCHEDULE"
        verified = True
        source = "TWSE_OFFICIAL"
    else:
        is_trading_day = weekday
        precision = "WEEKDAY_FALLBACK"
        verified = False
        source = "LOCAL_FALLBACK"
        if _remote_enabled(values) and not error:
            # A weekday omitted from TWSE's closure schedule is a normal session.
            precision = "OFFICIAL_SCHEDULE"
            verified = True
            source = "TWSE_OFFICIAL"
        elif not _remote_enabled(values):
            warnings.append("官方台灣休市日曆遠端更新未啟用")
    return {
        "market": code,
        "date": day.isoformat(),
        "is_trading_day": is_trading_day,
        "early_close": False,
        "close_time": None,
        "holiday_name": rule.get("holiday_name") if rule else None,
        "source": source,
        "source_url": _TWSE_HOLIDAY_URL if source == "TWSE_OFFICIAL" else None,
        "precision": precision,
        "verified": verified,
        "warnings": warnings,
    }


def clear_cache() -> None:
    """Test/operations helper for forcing the next official refresh."""
    with _CACHE_LOCK:
        _CACHE.clear()
