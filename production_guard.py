"""Production observability and data-freshness guardrails for RocketStock.

This module is deliberately dependency-light so it can be used by the web app,
workers and tests without importing the large Flask application.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
import os
from typing import Any, Mapping


DEFAULT_MAX_AGE_SECONDS = {
    "price": 20 * 60,
    "market": 20 * 60,
    "news": 6 * 60 * 60,
    "macro": 6 * 60 * 60,
    "fundamental": 120 * 24 * 60 * 60,
}


@dataclass(frozen=True)
class FreshnessResult:
    source: str
    observed_at: str | None
    age_seconds: int | None
    max_age_seconds: int
    status: str
    provider: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def parse_timestamp(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value).strip().replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(text)
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def check_freshness(
    source: str,
    observed_at: Any,
    *,
    provider: str | None = None,
    max_age_seconds: int | None = None,
    now: datetime | None = None,
) -> FreshnessResult:
    limit = int(max_age_seconds or DEFAULT_MAX_AGE_SECONDS.get(source, 6 * 60 * 60))
    timestamp = parse_timestamp(observed_at)
    if timestamp is None:
        return FreshnessResult(source, None, None, limit, "missing", provider)
    current = (now or _utcnow()).astimezone(timezone.utc)
    age = max(0, int((current - timestamp).total_seconds()))
    status = "fresh" if age <= limit else "stale"
    return FreshnessResult(source, timestamp.isoformat(), age, limit, status, provider)


def evaluate_sources(sources: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    checks: dict[str, dict[str, Any]] = {}
    for name, payload in sources.items():
        payload = payload or {}
        result = check_freshness(
            name,
            payload.get("observed_at") or payload.get("timestamp") or payload.get("updated_at"),
            provider=payload.get("provider"),
            max_age_seconds=payload.get("max_age_seconds"),
        )
        checks[name] = result.to_dict()
    critical = ("price", "market")
    critical_bad = any(checks.get(key, {}).get("status") != "fresh" for key in critical)
    known = [c for c in checks.values() if c["status"] != "missing"]
    fresh = [c for c in checks.values() if c["status"] == "fresh"]
    quality = round(100 * len(fresh) / max(1, len(checks)))
    return {
        "status": "blocked" if critical_bad else ("degraded" if len(fresh) != len(checks) else "healthy"),
        "freshness_score": quality,
        "checks": checks,
        "critical_data_ready": not critical_bad,
        "known_source_count": len(known),
    }


def apply_freshness_guard(
    decision: Mapping[str, Any],
    freshness: Mapping[str, Any],
) -> dict[str, Any]:
    """Prevent stale/missing critical market data from producing high-conviction buys."""
    result = dict(decision)
    score = int(freshness.get("freshness_score", 0) or 0)
    ready = bool(freshness.get("critical_data_ready"))
    raw_confidence = result.get("confidence", result.get("confidence_score"))
    if raw_confidence is not None:
        try:
            confidence = float(raw_confidence)
            # Freshness can only reduce confidence, never increase it.
            confidence = min(confidence, confidence * score / 100.0)
            if not ready:
                confidence = min(confidence, 60.0)
            result["confidence"] = round(confidence, 1)
        except (TypeError, ValueError):
            pass
    if not ready:
        for key in ("signal", "action", "recommendation"):
            value = str(result.get(key, "")).lower()
            if any(word in value for word in ("strong buy", "buy", "買進", "加碼")):
                result[key] = "WATCH"
        result["execution_blocked"] = True
        result["data_warning"] = "Critical market data is stale or missing; high-conviction buy signals are blocked."
    else:
        result["execution_blocked"] = False
    result["data_freshness"] = dict(freshness)
    return result


def deployment_metadata(env: Mapping[str, str] | None = None) -> dict[str, Any]:
    values = os.environ if env is None else env
    platform = "zeabur" if values.get("ZEABUR_SERVICE_ID") or values.get("ZEABUR") else (
        "railway" if values.get("RAILWAY_ENVIRONMENT") or values.get("RAILWAY_PROJECT_ID") else "unknown"
    )
    sha = (
        values.get("GIT_COMMIT_SHA")
        or values.get("RAILWAY_GIT_COMMIT_SHA")
        or values.get("ZEABUR_GIT_COMMIT_SHA")
        or values.get("COMMIT_SHA")
        or "unknown"
    )
    return {
        "service": "rocketstock",
        "engine": values.get("ROCKETSTOCK_ENGINE_VERSION", "institutional-core-v4"),
        "platform": platform,
        "commit": sha[:12] if sha != "unknown" else sha,
        "branch": values.get("GIT_BRANCH") or values.get("RAILWAY_GIT_BRANCH") or values.get("ZEABUR_GIT_BRANCH") or "unknown",
        "started_at": values.get("ROCKETSTOCK_STARTED_AT"),
        "environment": values.get("RAILWAY_ENVIRONMENT_NAME") or values.get("FLASK_ENV") or values.get("ENVIRONMENT") or "production",
    }
