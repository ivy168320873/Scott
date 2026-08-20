"""Production WSGI entrypoint with deployment/readiness observability."""
from __future__ import annotations

import os
from datetime import datetime, timezone

os.environ.setdefault("ROCKETSTOCK_STARTED_AT", datetime.now(timezone.utc).isoformat())

from flask import request  # noqa: E402

from app import app  # noqa: E402
import data_provider as _dp  # noqa: E402
from pre_breakout import rank_pre_breakout  # noqa: E402
from production_guard import deployment_metadata  # noqa: E402


@app.get("/health/live")
def health_live():
    return {"ok": True, **deployment_metadata()}, 200


@app.get("/health/ready")
def health_ready():
    meta = deployment_metadata()
    checks = {
        "secret_key": bool(os.environ.get("SECRET_KEY", "").strip()),
        "auth_configured": bool(
            os.environ.get("ACCESS_CODE", "").strip()
            or os.environ.get("GOOGLE_CLIENT_ID", "").strip()
            or os.environ.get("ALLOW_INSECURE_NO_AUTH", "false").lower() == "true"
        ),
        "persistent_storage": bool(
            os.environ.get("RAILWAY_VOLUME_MOUNT_PATH", "").strip()
            or os.environ.get("USER_DATA_DB", "").strip()
            or os.environ.get("DATABASE_URL", "").strip()
        ),
        "commit_identified": meta["commit"] != "unknown",
    }
    ready = all(checks.values())
    return {"ok": ready, "checks": checks, **meta}, 200 if ready else 503


@app.get("/api/system/version")
def system_version():
    """Safe metadata for the UI footer/admin status panel."""
    return deployment_metadata(), 200


@app.get("/api/pre-breakout")
def pre_breakout_scan():
    """Rank a bounded symbol list by pre-breakout readiness.

    Example: /api/pre-breakout?symbols=MU,NVDA,AMD,CRDO,COHR&top=10
    The endpoint intentionally requires an explicit universe in v1; scanning the
    whole market belongs in the scheduled worker, not a synchronous web request.
    """
    raw = request.args.get("symbols", "")
    symbols = []
    for item in raw.split(","):
        symbol = item.upper().strip()
        if symbol and symbol not in symbols:
            symbols.append(symbol)
    if not symbols:
        return {"ok": False, "error": "symbols query parameter is required"}, 400
    if len(symbols) > 50:
        return {"ok": False, "error": "maximum 50 symbols per request"}, 400
    if any(len(symbol) > 20 or not all(ch.isalnum() or ch in ".-" for ch in symbol) for symbol in symbols):
        return {"ok": False, "error": "invalid symbol"}, 400
    try:
        top_n = max(1, min(int(request.args.get("top", "10")), 50))
    except ValueError:
        return {"ok": False, "error": "top must be an integer"}, 400

    ranked = rank_pre_breakout(symbols, lambda s: _dp.get_ohlcv(s, period="6mo"), top_n=top_n)
    return {
        "ok": True,
        "engine": "pre-breakout-v1",
        "count": len(ranked),
        "results": ranked,
    }, 200
