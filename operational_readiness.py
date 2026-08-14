"""Operational and institutional-readiness checks without external API calls."""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path

from database_backup import backup_status


_TRUE_VALUES = {"1", "true", "yes", "on"}


def _is_true(value) -> bool:
    return str(value or "").strip().lower() in _TRUE_VALUES


def _aware_now(now: datetime | None) -> datetime:
    value = now or datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _item(check_id: str, status: str, message: str, *, critical: bool = False, details=None) -> dict:
    return {
        "id": check_id,
        "status": status,
        "critical": critical,
        "message": message,
        "details": details or {},
    }


def _persistent(db_path: str, volume_path: str) -> bool:
    if not volume_path:
        return False
    try:
        database = str(Path(db_path).resolve())
        volume = str(Path(volume_path).resolve())
        return os.path.commonpath([database, volume]) == volume
    except (OSError, ValueError):
        return False


def _latest_intelligence(db_path: str, now: datetime) -> tuple[dict | None, float | None]:
    try:
        from market_intelligence.storage import status

        latest = status(db_path).get("latest_run")
        if not latest:
            return None, None
        timestamp = latest.get("completed_at") or latest.get("started_at")
        parsed = datetime.fromisoformat(str(timestamp or ""))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        age = max(0.0, (now - parsed.astimezone(timezone.utc)).total_seconds() / 3600)
        return latest, age
    except Exception:  # noqa: BLE001 - readiness reports failure below
        return None, None


def build_readiness(
    db_path: str,
    *,
    env: Mapping[str, str] | None = None,
    now: datetime | None = None,
) -> dict:
    values = os.environ if env is None else env
    current = _aware_now(now)
    production = str(values.get("RAILWAY_ENVIRONMENT", "")).lower() in {
        "production",
        "prod",
    } or str(values.get("RAILWAY_ENVIRONMENT_NAME", "")).lower() in {
        "production",
        "prod",
    }
    checks: list[dict] = []

    try:
        with sqlite3.connect(db_path, timeout=5) as connection:
            quick_check = str(connection.execute("PRAGMA quick_check").fetchone()[0])
        if quick_check.lower() != "ok":
            raise RuntimeError(quick_check)
        checks.append(_item("database_integrity", "PASS", "SQLite quick_check 正常", critical=True))
    except Exception as exc:  # noqa: BLE001 - readiness must remain serializable
        checks.append(
            _item(
                "database_integrity",
                "FAIL",
                "SQLite 完整性檢查失敗",
                critical=True,
                details={"error": str(exc)[:200]},
            )
        )

    volume_path = str(values.get("RAILWAY_VOLUME_MOUNT_PATH", "")).strip()
    persistent = _persistent(db_path, volume_path)
    if production and not persistent:
        checks.append(
            _item(
                "persistent_storage",
                "FAIL",
                "正式環境資料庫不在 Railway Volume",
                critical=True,
            )
        )
    else:
        checks.append(
            _item(
                "persistent_storage",
                "PASS" if persistent else "WARN",
                "資料庫位於持久化 Volume" if persistent else "本機環境未驗證持久化 Volume",
                critical=production,
            )
        )

    auth_ready = bool(values.get("ACCESS_CODE")) and bool(values.get("SECRET_KEY"))
    checks.append(
        _item(
            "production_auth",
            "PASS" if auth_ready else ("FAIL" if production else "WARN"),
            "正式環境認證密鑰已設定" if auth_ready else "ACCESS_CODE／SECRET_KEY 未完整設定",
            critical=production,
        )
    )

    feed = str(values.get("ALPACA_DATA_FEED", "iex") or "iex").lower()
    alpaca_ready = bool(values.get("ALPACA_API_KEY")) and bool(values.get("ALPACA_SECRET_KEY"))
    sip_ready = alpaca_ready and feed == "sip"
    checks.append(
        _item(
            "consolidated_us_feed",
            "PASS" if sip_ready else "WARN",
            "美股報價設定為 consolidated SIP" if sip_ready else "美股尚未驗證 SIP／NBBO 資料權限",
            details={"configured_feed": feed, "alpaca_credentials_set": alpaca_ready},
        )
    )

    macro_ready = bool(values.get("FRED_API_KEY"))
    sec_ready = bool(values.get("SEC_USER_AGENT"))
    checks.append(
        _item(
            "primary_macro_and_filings",
            "PASS" if macro_ready and sec_ready else "WARN",
            "FRED 與 SEC EDGAR 主來源已設定" if macro_ready and sec_ready else "FRED 或 SEC EDGAR 主來源未完整設定",
            details={"fred": macro_ready, "sec": sec_ready},
        )
    )

    latest, age_hours = _latest_intelligence(db_path, current)
    intelligence_ok = bool(
        latest and latest.get("status") == "SUCCESS" and age_hours is not None and age_hours <= 36
    )
    checks.append(
        _item(
            "intelligence_freshness",
            "PASS" if intelligence_ok else "WARN",
            "市場情報 Worker 最近成功" if intelligence_ok else "市場情報 Worker 尚無 36 小時內成功紀錄",
            details={"latest_run": latest, "age_hours": round(age_hours, 2) if age_hours is not None else None},
        )
    )

    line_ready = bool(values.get("LINE_CHANNEL_ACCESS_TOKEN")) and bool(values.get("LINE_USER_ID"))
    email_ready = bool(values.get("SMTP_USER")) and bool(values.get("SMTP_PASS")) and bool(values.get("ALERT_EMAIL_TO"))
    checks.append(
        _item(
            "notification_channel",
            "PASS" if line_ready or email_ready else "WARN",
            "至少一個外部通知管道已設定" if line_ready or email_ready else "LINE／Email 推播均未完整設定",
            details={"line": line_ready, "email": email_ready},
        )
    )

    try:
        from scott_evolution.notifications import summary

        outbox = summary(db_path=db_path)
        outbox_ok = int(outbox.get("dead", 0)) == 0
    except Exception as exc:  # noqa: BLE001
        outbox = {"error": str(exc)[:200]}
        outbox_ok = False
    checks.append(
        _item(
            "notification_outbox",
            "PASS" if outbox_ok else "WARN",
            "通知 outbox 無永久失敗項目" if outbox_ok else "通知 outbox 有 DEAD 或無法讀取",
            details=outbox,
        )
    )

    backup = backup_status(db_path, now=current)
    checks.append(
        _item(
            "sqlite_backup",
            "PASS" if backup.get("ok") else "WARN",
            "最近 SQLite 備份已驗證" if backup.get("ok") else "尚無 36 小時內已驗證 SQLite 備份",
            details=backup,
        )
    )
    platform_backup = _is_true(values.get("RAILWAY_BACKUP_SCHEDULE_CONFIRMED"))
    checks.append(
        _item(
            "off_volume_backup",
            "PASS" if platform_backup else "WARN",
            "Railway 平台備份已人工確認" if platform_backup else "同 Volume 備份無法防 Volume 遺失；尚未確認 Railway 平台備份",
        )
    )

    critical_failures = [item for item in checks if item["critical"] and item["status"] == "FAIL"]
    warnings = [item for item in checks if item["status"] in {"WARN", "FAIL"}]
    operational_ready = not critical_failures
    institutional_ready = operational_ready and not warnings
    return {
        "ok": operational_ready,
        "status": "BLOCKED" if critical_failures else ("DEGRADED" if warnings else "READY"),
        "operational_ready": operational_ready,
        "institutional_ready": institutional_ready,
        "checks": checks,
        "summary": {
            "pass": sum(item["status"] == "PASS" for item in checks),
            "warn": sum(item["status"] == "WARN" for item in checks),
            "fail": sum(item["status"] == "FAIL" for item in checks),
        },
        "generated_at": current.isoformat(),
    }

