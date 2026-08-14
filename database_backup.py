"""Verified online SQLite backups for the Railway persistent volume."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path


_STATUS_FILE = "backup_status.json"


def _aware_now(now: datetime | None) -> datetime:
    value = now or datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def backup_directory(db_path: str, backup_dir: str | None = None) -> Path:
    configured = str(
        backup_dir or os.environ.get("SCOTT_BACKUP_DIR", "")
    ).strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return (Path(db_path).expanduser().resolve().parent / "backups").resolve()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_status(directory: Path, payload: dict) -> None:
    fd, temporary = tempfile.mkstemp(
        prefix="backup-status-", suffix=".json", dir=str(directory)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, directory / _STATUS_FILE)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _prune(directory: Path, *, retention_days: int, now: datetime) -> int:
    cutoff = now - timedelta(days=max(1, int(retention_days)))
    removed = 0
    for candidate in directory.glob("scott-*.sqlite3"):
        try:
            modified = datetime.fromtimestamp(candidate.stat().st_mtime, timezone.utc)
            if modified < cutoff:
                candidate.unlink()
                removed += 1
        except OSError:
            continue
    return removed


def backup_database(
    db_path: str,
    *,
    backup_dir: str | None = None,
    retention_days: int = 14,
    now: datetime | None = None,
) -> dict:
    """Create an atomic SQLite online backup, verify it, checksum it, and prune."""
    created_at = _aware_now(now)
    source_path = Path(db_path).expanduser().resolve()
    if not source_path.is_file():
        return {"ok": False, "status": "MISSING_SOURCE", "error": "database not found"}
    directory = backup_directory(str(source_path), backup_dir)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"scott-{created_at.strftime('%Y%m%d-%H%M%S')}.sqlite3"
    fd, temporary = tempfile.mkstemp(
        prefix="scott-backup-", suffix=".sqlite3", dir=str(directory)
    )
    os.close(fd)
    try:
        with sqlite3.connect(
            f"file:{source_path}?mode=ro", uri=True, timeout=30
        ) as source, sqlite3.connect(temporary, timeout=30) as destination:
            source.execute("PRAGMA busy_timeout=30000")
            source.backup(destination, pages=256, sleep=0.05)
        with sqlite3.connect(temporary, timeout=10) as verified:
            quick_check = str(verified.execute("PRAGMA quick_check").fetchone()[0])
        if quick_check.lower() != "ok":
            raise RuntimeError(f"backup quick_check failed: {quick_check}")
        os.replace(temporary, target)
        result = {
            "ok": True,
            "status": "VERIFIED",
            "created_at": created_at.isoformat(),
            "filename": target.name,
            "size_bytes": target.stat().st_size,
            "sha256": _sha256(target),
            "quick_check": quick_check,
            "retention_days": max(1, int(retention_days)),
        }
        result["pruned"] = _prune(
            directory, retention_days=retention_days, now=created_at
        )
        _write_status(directory, result)
        return result
    except Exception as exc:  # noqa: BLE001 - backup boundary returns diagnostics
        failure = {
            "ok": False,
            "status": "FAILED",
            "created_at": created_at.isoformat(),
            "error": str(exc)[:500],
        }
        try:
            _write_status(directory, failure)
        except OSError:
            pass
        return failure
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def backup_status(
    db_path: str,
    *,
    backup_dir: str | None = None,
    max_age_hours: int = 36,
    now: datetime | None = None,
) -> dict:
    directory = backup_directory(db_path, backup_dir)
    status_path = directory / _STATUS_FILE
    if not status_path.is_file():
        return {"ok": False, "status": "NEVER_RUN", "age_hours": None}
    try:
        payload = json.loads(status_path.read_text(encoding="utf-8"))
        created = datetime.fromisoformat(str(payload.get("created_at") or ""))
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        age = max(0.0, (_aware_now(now) - created.astimezone(timezone.utc)).total_seconds() / 3600)
        backup_file = directory / str(payload.get("filename") or "")
        healthy = bool(
            payload.get("ok")
            and payload.get("quick_check") == "ok"
            and backup_file.is_file()
            and age <= max(1, int(max_age_hours))
        )
        return {
            **payload,
            "ok": healthy,
            "status": "VERIFIED" if healthy else "STALE_OR_MISSING",
            "age_hours": round(age, 2),
            "file_exists": backup_file.is_file(),
        }
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        return {"ok": False, "status": "INVALID_STATUS", "error": str(exc)[:300]}


def ensure_daily_backup(db_path: str, *, now: datetime | None = None) -> dict:
    current = _aware_now(now)
    existing = backup_status(db_path, now=current, max_age_hours=48)
    if existing.get("ok") and str(existing.get("created_at") or "")[:10] == current.date().isoformat():
        return {**existing, "created": False}
    try:
        retention_days = int(
            os.environ.get("SCOTT_BACKUP_RETENTION_DAYS", "14") or 14
        )
    except (TypeError, ValueError):
        retention_days = 14
    result = backup_database(
        db_path,
        retention_days=retention_days,
        now=current,
    )
    result["created"] = bool(result.get("ok"))
    return result
