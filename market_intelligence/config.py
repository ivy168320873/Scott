"""Environment-backed configuration for the intelligence worker."""

from __future__ import annotations

import os
from dataclasses import dataclass


def env_bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.environ.get(name, default))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


def _valid_hhmm(value: str, default: str) -> str:
    try:
        hour, minute = (int(part) for part in value.split(":"))
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return f"{hour:02d}:{minute:02d}"
    except (AttributeError, TypeError, ValueError):
        pass
    return default


@dataclass(frozen=True)
class IntelligenceConfig:
    enabled: bool
    db_path: str
    timezone: str
    poll_minutes: int
    daily_time: str
    lookback_hours: int
    max_symbols: int
    max_articles: int
    breaking_importance: int
    dispatch_email: bool
    dispatch_line: bool
    anthropic_model: str
    finnhub_key: str
    alpha_vantage_key: str

    @classmethod
    def from_env(cls, *, db_path: str | None = None) -> IntelligenceConfig:
        resolved_db = (
            db_path
            or os.environ.get("USER_DATA_DB")
            or (
                os.path.join(os.environ["RAILWAY_VOLUME_MOUNT_PATH"], "user_data.db")
                if os.environ.get("RAILWAY_VOLUME_MOUNT_PATH")
                else "./user_data.db"
            )
        )
        return cls(
            enabled=env_bool("MARKET_INTELLIGENCE_ENABLE", False),
            db_path=resolved_db,
            timezone=os.environ.get(
                "MARKET_INTELLIGENCE_TIMEZONE", "Asia/Taipei"
            ).strip()
            or "Asia/Taipei",
            poll_minutes=_env_int("MARKET_INTELLIGENCE_POLL_MINUTES", 30, 10, 360),
            daily_time=_valid_hhmm(
                os.environ.get("MARKET_INTELLIGENCE_DAILY_TIME", "08:30"), "08:30"
            ),
            lookback_hours=_env_int("MARKET_INTELLIGENCE_LOOKBACK_HOURS", 30, 6, 168),
            max_symbols=_env_int("MARKET_INTELLIGENCE_MAX_SYMBOLS", 20, 1, 50),
            max_articles=_env_int("MARKET_INTELLIGENCE_MAX_ARTICLES", 60, 5, 200),
            breaking_importance=_env_int(
                "MARKET_INTELLIGENCE_BREAKING_IMPORTANCE", 82, 60, 100
            ),
            dispatch_email=env_bool("MARKET_INTELLIGENCE_EMAIL", True),
            dispatch_line=env_bool("MARKET_INTELLIGENCE_LINE", True),
            anthropic_model=os.environ.get(
                "MARKET_INTELLIGENCE_MODEL", "claude-haiku-4-5-20251001"
            ).strip(),
            finnhub_key=os.environ.get("FINNHUB_KEY", "").strip(),
            alpha_vantage_key=os.environ.get("ALPHA_VANTAGE_KEY", "").strip(),
        )

    def public_status(self) -> dict:
        """Return non-secret settings for admin/status pages."""
        return {
            "enabled": self.enabled,
            "timezone": self.timezone,
            "poll_minutes": self.poll_minutes,
            "daily_time": self.daily_time,
            "lookback_hours": self.lookback_hours,
            "max_symbols": self.max_symbols,
            "max_articles": self.max_articles,
            "dispatch_email": self.dispatch_email,
            "dispatch_line": self.dispatch_line,
            "providers": {
                "yahoo": True,
                "finnhub": bool(self.finnhub_key),
                "alpha_vantage": bool(self.alpha_vantage_key),
                "anthropic": bool(os.environ.get("ANTHROPIC_API_KEY", "").strip()),
            },
        }
