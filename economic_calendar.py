"""Dynamic, source-attributed US economic event calendar.

No event date is hard-coded.  FOMC meetings are parsed from the Federal
Reserve calendar; optional FRED release dates add CPI, employment, GDP and
other scheduled releases when ``FRED_API_KEY`` is configured.  Provider
failure returns a visible data gap instead of silently reusing stale dates.
"""

from __future__ import annotations

import html as html_lib
import os
import re
import threading
import time
from datetime import date, datetime, timedelta, timezone

import requests


_FED_URL = "https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm"
_FRED_URL = "https://api.stlouisfed.org/fred/releases/dates"
_HEADERS = {
    "User-Agent": "ScottMarketIntelligence/2.0 (economic-calendar; read-only)",
    "Accept": "text/html,application/json",
}
_CACHE: dict[tuple, dict] = {}
_LOCK = threading.Lock()
_TTL = 6 * 3600

_MONTHS = {
    name: number
    for number, name in enumerate(
        (
            "January", "February", "March", "April", "May", "June",
            "July", "August", "September", "October", "November", "December",
        ),
        1,
    )
}
_MONTH_ALIASES = {
    name.lower(): number for name, number in _MONTHS.items()
}
_MONTH_ALIASES.update({name[:3].lower(): number for name, number in _MONTHS.items()})
_HIGH_IMPACT = (
    "consumer price", "cpi", "employment situation", "jobs", "payroll",
    "gross domestic product", "gdp", "personal income", "fomc",
)
_MEDIUM_IMPACT = (
    "producer price", "ppi", "retail sales", "industrial production",
    "job openings", "housing", "durable goods",
)


def _clean_text(raw: str) -> str:
    value = re.sub(r"<script\b[^>]*>.*?</script>", " ", raw, flags=re.I | re.S)
    value = re.sub(r"<style\b[^>]*>.*?</style>", " ", value, flags=re.I | re.S)
    value = re.sub(r"<[^>]+>", " ", value)
    return re.sub(r"\s+", " ", html_lib.unescape(value)).strip()


def _parse_fomc(raw_html: str, *, years: tuple[int, ...]) -> list[dict]:
    events = []
    for year in years:
        match = re.search(
            rf">\s*{year}\s+FOMC\s+Meetings\s*<(.+?)"
            rf"(?=<div\s+class=[\"']panel\s+panel-default[\"']|$)",
            raw_html,
            flags=re.I | re.S,
        )
        # Small HTML fixtures and a future Fed redesign can still use the
        # conservative text fallback below.  Release dates are removed first
        # so they can never be mislabeled as meeting dates.
        structured = True
        if not match:
            text = _clean_text(raw_html)
            match = re.search(
                rf"{year}\s+FOMC\s+Meetings(.*?)(?=\d{{4}}\s+FOMC\s+Meetings|$)",
                text,
                flags=re.I | re.S,
            )
            structured = False
        if not match:
            continue
        section = match.group(1)
        pairs = []
        if structured:
            pairs = re.findall(
                r"<div\b[^>]*class=[\"'][^\"']*fomc-meeting__month[^\"']*[\"'][^>]*>"
                r"(.*?)</div>\s*"
                r"<div\b[^>]*class=[\"'][^\"']*fomc-meeting__date[^\"']*[\"'][^>]*>"
                r"(.*?)</div>",
                section,
                flags=re.I | re.S,
            )
        if not pairs:
            cleaned = _clean_text(section)
            cleaned = re.sub(
                r"\(?\s*Released\s+[A-Za-z]+\s+\d{1,2},?\s+\d{4}\s*\)?",
                " ",
                cleaned,
                flags=re.I,
            )
            month_pattern = "|".join(_MONTHS)
            pairs = [
                (item.group(1), item.group(2))
                for item in re.finditer(
                    rf"\b({month_pattern})\s+(\d{{1,2}}(?:\s*[-–]\s*\d{{1,2}})?)",
                    cleaned,
                    flags=re.I,
                )
            ]
        for raw_month, raw_days in pairs:
            month_label = _clean_text(raw_month).strip()
            day_values = re.findall(r"\d{1,2}", _clean_text(raw_days))
            if not day_values:
                continue
            month_token = month_label.split("/")[-1].strip().lower()
            month_number = _MONTH_ALIASES.get(month_token)
            if month_number is None:
                continue
            day = int(day_values[-1])
            try:
                event_day = date(year, month_number, day)
            except ValueError:
                continue
            events.append(
                {
                    "event": "FOMC 利率會議",
                    "date": event_day.isoformat(),
                    "impact": "HIGH",
                    "category": "MONETARY_POLICY",
                    "source": "Federal Reserve",
                    "source_url": _FED_URL,
                }
            )
    unique = {event["date"]: event for event in events}
    return sorted(unique.values(), key=lambda event: event["date"])


def _impact(name: str) -> str:
    value = str(name or "").lower()
    if any(keyword in value for keyword in _HIGH_IMPACT):
        return "HIGH"
    if any(keyword in value for keyword in _MEDIUM_IMPACT):
        return "MEDIUM"
    return "LOW"


def _fred_events(api_key: str, start: date, end: date, *, session=None) -> list[dict]:
    getter = session.get if session is not None else requests.get
    response = getter(
        _FRED_URL,
        params={
            "api_key": api_key,
            "file_type": "json",
            "realtime_start": start.isoformat(),
            "realtime_end": end.isoformat(),
            "include_release_dates_with_no_data": "true",
            "limit": 1000,
            "order_by": "release_date",
            "sort_order": "asc",
        },
        headers=_HEADERS,
        timeout=12,
    )
    response.raise_for_status()
    result = []
    for raw in response.json().get("release_dates", []):
        event_day = str(raw.get("date") or "")[:10]
        name = str(raw.get("release_name") or "").strip()
        try:
            parsed = date.fromisoformat(event_day)
        except ValueError:
            continue
        if not name or not start <= parsed <= end:
            continue
        impact = _impact(name)
        if impact == "LOW":
            continue
        result.append(
            {
                "event": name[:180],
                "date": event_day,
                "impact": impact,
                "category": "ECONOMIC_RELEASE",
                "source": "FRED",
                "source_url": _FRED_URL,
                "release_id": raw.get("release_id"),
            }
        )
    return result


def get_upcoming_events(
    *,
    now: datetime | None = None,
    horizon_days: int = 45,
    session=None,
) -> dict:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    start = current.date()
    end = start + timedelta(days=max(1, min(180, int(horizon_days))))
    fred_key = os.environ.get("FRED_API_KEY", "").strip()
    cache_key = (start.isoformat(), end.isoformat(), bool(fred_key), id(session))
    with _LOCK:
        cached = _CACHE.get(cache_key)
    if cached and time.monotonic() - cached["ts"] < _TTL:
        return dict(cached["data"])

    events: list[dict] = []
    health = {
        "federal_reserve": {"configured": True, "ok": False, "error": None},
        "fred": {"configured": bool(fred_key), "ok": False, "error": None},
    }
    getter = session.get if session is not None else requests.get
    try:
        response = getter(_FED_URL, params={}, headers=_HEADERS, timeout=12)
        response.raise_for_status()
        events.extend(
            _parse_fomc(response.text, years=tuple(range(start.year, end.year + 1)))
        )
        health["federal_reserve"]["ok"] = True
    except Exception as exc:  # provider gap must remain visible
        health["federal_reserve"]["error"] = str(exc)[:160]

    if fred_key:
        try:
            events.extend(_fred_events(fred_key, start, end, session=session))
            health["fred"]["ok"] = True
        except Exception as exc:
            health["fred"]["error"] = str(exc)[:160]

    filtered: dict[tuple[str, str], dict] = {}
    for event in events:
        try:
            event_day = date.fromisoformat(event["date"])
        except (KeyError, ValueError):
            continue
        if start <= event_day <= end:
            filtered[(event["date"], event["event"].lower())] = event
    upcoming = sorted(filtered.values(), key=lambda event: (event["date"], event["event"]))
    attempted = [item for item in health.values() if item["configured"]]
    succeeded = sum(1 for item in attempted if item["ok"])
    status = "OK" if attempted and succeeded == len(attempted) else (
        "DEGRADED" if succeeded else "UNAVAILABLE"
    )
    warnings = []
    if not fred_key:
        warnings.append("FRED_API_KEY 未設定：目前僅驗證 FOMC，CPI／就業／GDP 日程不完整")
    for provider, item in health.items():
        if item["configured"] and item["error"]:
            warnings.append(f"{provider} 日曆取得失敗：{item['error']}")
    result = {
        "ok": status != "UNAVAILABLE",
        "status": status,
        "as_of": current.astimezone(timezone.utc).isoformat(),
        "horizon_end": end.isoformat(),
        "events": upcoming,
        "provider_health": health,
        "warnings": warnings,
        "stale_static_dates_used": False,
    }
    with _LOCK:
        _CACHE[cache_key] = {"ts": time.monotonic(), "data": result}
    return dict(result)
