from datetime import datetime, timedelta, timezone

from production_guard import apply_freshness_guard, check_freshness, deployment_metadata, evaluate_sources


def test_stale_price_blocks_high_conviction_buy():
    now = datetime.now(timezone.utc)
    freshness = evaluate_sources({
        "price": {"observed_at": (now - timedelta(hours=2)).isoformat(), "max_age_seconds": 60},
        "market": {"observed_at": now.isoformat(), "max_age_seconds": 60},
        "news": {"observed_at": now.isoformat()},
    })
    guarded = apply_freshness_guard({"signal": "STRONG BUY", "confidence": 92}, freshness)
    assert freshness["status"] == "blocked"
    assert guarded["signal"] == "WATCH"
    assert guarded["confidence"] <= 60
    assert guarded["execution_blocked"] is True


def test_fresh_critical_data_keeps_execution_available():
    now = datetime.now(timezone.utc)
    freshness = evaluate_sources({
        "price": {"observed_at": now.isoformat()},
        "market": {"observed_at": now.isoformat()},
    })
    guarded = apply_freshness_guard({"signal": "BUY", "confidence": 80}, freshness)
    assert freshness["status"] == "healthy"
    assert guarded["signal"] == "BUY"
    assert guarded["execution_blocked"] is False


def test_missing_timestamp_is_not_silently_fresh():
    result = check_freshness("price", None)
    assert result.status == "missing"
    assert result.age_seconds is None


def test_deployment_metadata_identifies_platform_and_commit():
    meta = deployment_metadata({
        "ZEABUR_SERVICE_ID": "svc-1",
        "GIT_COMMIT_SHA": "1234567890abcdef",
        "GIT_BRANCH": "main",
    })
    assert meta["platform"] == "zeabur"
    assert meta["commit"] == "1234567890ab"
    assert meta["branch"] == "main"
