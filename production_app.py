"""Production WSGI entrypoint with deployment/readiness observability."""
from __future__ import annotations

import os
from datetime import datetime, timezone

os.environ.setdefault("ROCKETSTOCK_STARTED_AT", datetime.now(timezone.utc).isoformat())

from app import app  # noqa: E402
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
            os.environ.get("PERSISTENT_STORAGE_PATH", "").strip()
            or os.environ.get("RAILWAY_VOLUME_MOUNT_PATH", "").strip()
            or os.environ.get("USER_DATA_DB", "").strip()
            or os.environ.get("DATABASE_URL", "").strip()
        ),
        "commit_identified": meta["commit"] != "unknown",
    }
    # Build identity is an observability warning, not a serving dependency.
    # Some platforms do not expose a commit environment variable; making it a
    # hard readiness requirement would create a deployment outage while the
    # database, authentication and application are otherwise healthy.
    critical = ("secret_key", "auth_configured", "persistent_storage")
    ready = all(checks[name] for name in critical)
    return {"ok": ready, "checks": checks, **meta}, 200 if ready else 503


@app.get("/api/system/version")
def system_version():
    """Safe metadata for the UI footer/admin status panel."""
    return deployment_metadata(), 200
