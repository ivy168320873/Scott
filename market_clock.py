"""Exchange-aware market clock and completed-bar guardrails.

The decision engines operate on daily bars.  A provider may expose today's
still-forming candle during regular trading; using that candle as though it
were a completed close creates look-ahead bias and unstable signals.  This
module gives every normalized dataset an explicit market/session/provenance
contract and removes trailing uncompleted or future-dated candles.

Exchange holidays and scheduled early closes are resolved by
``exchange_calendar``.  Taiwan's official schedule is refreshed in production;
both markets support explicit emergency closure overrides.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from exchange_calendar import exchange_day


_MARKETS = {
    "US": {
        "timezone": "America/New_York",
        "pre_open": time(4, 0),
        "regular_open": time(9, 30),
        "regular_close": time(16, 0),
        "post_close": time(20, 0),
        "bar_finalization_delay_minutes": 5,
    },
    "TW": {
        "timezone": "Asia/Taipei",
        "pre_open": time(8, 30),
        "regular_open": time(9, 0),
        "regular_close": time(13, 30),
        "post_close": time(14, 30),
        "bar_finalization_delay_minutes": 5,
    },
}

_ALIGNED_BAR_KEYS = (
    "closes",
    "opens",
    "highs",
    "lows",
    "volumes",
    "timestamps",
    "dates",
)


def market_for_symbol(symbol: str) -> str:
    """Return ``TW`` for Taiwan-listed symbols, otherwise ``US``."""
    value = str(symbol or "").upper().strip()
    if value.endswith((".TW", ".TWO")) or value in {"^TWII", "TAIEX"}:
        return "TW"
    return "US"


def _aware_now(now: datetime | None) -> datetime:
    value = now or datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value


def market_session(symbol: str = "", *, market: str | None = None,
                   now: datetime | None = None) -> dict:
    """Describe the exchange session with auditable calendar provenance."""
    code = str(market or market_for_symbol(symbol)).upper()
    if code not in _MARKETS:
        code = "US"
    cfg = _MARKETS[code]
    current = _aware_now(now).astimezone(ZoneInfo(cfg["timezone"]))
    weekday = current.weekday() < 5
    clock = current.timetz().replace(tzinfo=None)

    local_day = current.date()
    day_status = exchange_day(code, local_day)
    regular_close = cfg["regular_close"]
    if day_status.get("early_close") and day_status.get("close_time"):
        regular_close = time.fromisoformat(day_status["close_time"])

    if not day_status["is_trading_day"]:
        state = "CLOSED"
    elif clock < cfg["pre_open"]:
        state = "CLOSED"
    elif clock < cfg["regular_open"]:
        state = "PRE"
    elif clock < regular_close:
        state = "OPEN"
    elif clock < cfg["post_close"]:
        state = "POST"
    else:
        state = "CLOSED"

    return {
        "market": code,
        "timezone": cfg["timezone"],
        "state": state,
        "is_weekday": weekday,
        "regular_open": datetime.combine(
            local_day, cfg["regular_open"], tzinfo=ZoneInfo(cfg["timezone"])
        ).isoformat(),
        "regular_close": datetime.combine(
            local_day, regular_close, tzinfo=ZoneInfo(cfg["timezone"])
        ).isoformat(),
        "daily_bar_finalized_after": (
            datetime.combine(
                local_day, regular_close, tzinfo=ZoneInfo(cfg["timezone"])
            )
            + timedelta(minutes=cfg["bar_finalization_delay_minutes"])
        ).isoformat(),
        "as_of": current.isoformat(),
        "is_trading_day": day_status["is_trading_day"],
        "early_close": day_status["early_close"],
        "holiday_name": day_status["holiday_name"],
        "calendar_precision": day_status["precision"],
        "calendar_source": day_status["source"],
        "calendar_source_url": day_status["source_url"],
        "holiday_verified": day_status["verified"],
        "calendar_warnings": day_status["warnings"],
    }


def _parse_day(value) -> date | None:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    try:
        return date.fromisoformat(str(value or "")[:10])
    except (TypeError, ValueError):
        return None


def _timestamp_day(value) -> date | None:
    try:
        stamp = float(value)
        if stamp <= 0:
            return None
        return datetime.fromtimestamp(stamp, tz=timezone.utc).date()
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def daily_bar_is_complete(
    symbol: str,
    bar_day,
    *,
    now: datetime | None = None,
) -> bool:
    """Return whether a daily bar can safely be treated as final."""
    parsed = _parse_day(bar_day)
    if parsed is None:
        return False
    session = market_session(symbol, now=now)
    current = _aware_now(now).astimezone(ZoneInfo(session["timezone"]))
    day_status = exchange_day(session["market"], parsed)
    if not day_status["is_trading_day"]:
        return False
    if parsed < current.date():
        return True
    if parsed > current.date():
        return False
    finalized_at = datetime.fromisoformat(session["daily_bar_finalized_after"])
    return current >= finalized_at


def annotate_completed_bars(
    symbol: str,
    ohlcv: dict | None,
    *,
    now: datetime | None = None,
) -> dict | None:
    """Copy and enrich normalized OHLCV, excluding a partial daily candle.

    The original provider object is never mutated.  ``incomplete_bar`` keeps a
    compact audit snapshot of an excluded candle, while all indicator-facing
    arrays remain aligned and contain completed bars only.
    """
    if not isinstance(ohlcv, dict):
        return None
    result = dict(ohlcv)
    for key in _ALIGNED_BAR_KEYS:
        if isinstance(result.get(key), list):
            result[key] = list(result[key])

    closes = result.get("closes") or []
    dates = result.get("dates") or []
    timestamps = result.get("timestamps") or []
    if len(dates) != len(closes):
        dates = [
            (_timestamp_day(ts).isoformat() if _timestamp_day(ts) else "")
            for ts in timestamps[: len(closes)]
        ]
        if len(dates) < len(closes):
            dates.extend([""] * (len(closes) - len(dates)))
        result["dates"] = dates

    flags = [str(item) for item in result.get("quality_flags") or []]
    excluded_bars = []
    session = market_session(symbol, now=now)
    current_day = _aware_now(now).astimezone(ZoneInfo(session["timezone"])).date()
    while result.get("closes") and result.get("dates"):
        last_day = _parse_day(result["dates"][-1])
        if last_day is None or daily_bar_is_complete(symbol, last_day, now=now):
            break
        excluded_bars.append(
            {
                "date": last_day.isoformat(),
                "open": (result.get("opens") or [None])[-1],
                "high": (result.get("highs") or [None])[-1],
                "low": (result.get("lows") or [None])[-1],
                "close": result["closes"][-1],
                "volume": (result.get("volumes") or [None])[-1],
                "timestamp": (result.get("timestamps") or [None])[-1],
            }
        )
        if last_day > current_day:
            flags.append("FUTURE_BAR")
        else:
            flags.append("INCOMPLETE_DAILY_BAR_EXCLUDED")
        for key in _ALIGNED_BAR_KEYS:
            values = result.get(key)
            if isinstance(values, list) and values:
                result[key] = values[:-1]

    remaining_dates = result.get("dates") or []
    remaining_timestamps = result.get("timestamps") or []
    final_day = (
        _parse_day(remaining_dates[-1])
        if remaining_dates
        else (_timestamp_day(remaining_timestamps[-1]) if remaining_timestamps else None)
    )
    fetched = _aware_now(now).isoformat()
    final_complete = bool(
        final_day and daily_bar_is_complete(symbol, final_day, now=now)
    )
    result.update(
        {
            "market": session["market"],
            "market_session": session,
            "fetched_at": fetched,
            "last_bar_date": final_day.isoformat() if final_day else None,
            "last_bar_complete": final_complete,
            "excluded_incomplete_bar": bool(excluded_bars),
            "incomplete_bar": excluded_bars[0] if excluded_bars else None,
            "excluded_bars": list(reversed(excluded_bars)),
            "quality_flags": list(dict.fromkeys(flags)),
            "provider_chain": result.get("provider_chain")
            or [str(result.get("source") or "unknown")],
        }
    )
    return result
