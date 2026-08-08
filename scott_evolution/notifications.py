"""Durable notification outbox with deduplication and exponential retry."""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timedelta, timezone
from typing import Callable

from .storage import connect, dumps, loads, utc_now


_BACKOFF_SECONDS = (60, 300, 900, 3600, 21_600)
_VALID_CHANNELS = {"line", "email", "app"}
_SECRET_KEY_PARTS = ("token", "secret", "password", "api_key", "apikey")


def _scrub(value):
    if isinstance(value, dict):
        return {
            str(key)[:100]: _scrub(item)
            for key, item in value.items()
            if not any(part in str(key).lower() for part in _SECRET_KEY_PARTS)
        }
    if isinstance(value, list):
        return [_scrub(item) for item in value[:100]]
    if isinstance(value, str):
        return value[:10_000]
    return value


def init_db(db_path: str | None = None) -> None:
    with connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS notification_outbox (
                id TEXT PRIMARY KEY,
                event_key TEXT NOT NULL,
                channel TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'PENDING',
                attempts INTEGER NOT NULL DEFAULT 0,
                max_attempts INTEGER NOT NULL DEFAULT 5,
                next_attempt_at TEXT NOT NULL,
                last_error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                delivered_at TEXT,
                UNIQUE(event_key, channel)
            );
            CREATE INDEX IF NOT EXISTS no_due
                ON notification_outbox(status, next_attempt_at);
            """
        )
        stale_before = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
        conn.execute(
            """
            UPDATE notification_outbox
            SET status='FAILED', next_attempt_at=?, last_error='Recovered stale delivery claim'
            WHERE status='IN_PROGRESS' AND updated_at < ?
            """,
            (utc_now(), stale_before),
        )


def _row_to_dict(row) -> dict:
    item = dict(row)
    item["payload"] = loads(item.pop("payload_json", None), {})
    item["deduplicated"] = False
    return item


def enqueue(
    event_key: str,
    channel: str,
    payload: dict,
    *,
    max_attempts: int = 5,
    db_path: str | None = None,
) -> dict:
    init_db(db_path)
    channel = str(channel or "").lower().strip()
    if channel not in _VALID_CHANNELS:
        raise ValueError("channel 必須是 line、email 或 app")
    if not isinstance(payload, dict):
        raise ValueError("payload 必須是 JSON object")
    raw_key = str(event_key or "").strip()
    if not raw_key:
        raise ValueError("event_key 不可為空")
    event_key = raw_key[:180]
    if len(raw_key) > 180:
        event_key = hashlib.sha256(raw_key.encode()).hexdigest()
    max_attempts = max(1, min(10, int(max_attempts)))
    now = utc_now()
    notification_id = uuid.uuid4().hex
    safe_payload = _scrub(payload)
    with connect(db_path) as conn:
        try:
            conn.execute(
                """
                INSERT INTO notification_outbox(
                    id, event_key, channel, payload_json, status, attempts,
                    max_attempts, next_attempt_at, created_at, updated_at
                ) VALUES(?,?,?,?, 'PENDING', 0, ?, ?, ?, ?)
                """,
                (
                    notification_id,
                    event_key,
                    channel,
                    dumps(safe_payload),
                    max_attempts,
                    now,
                    now,
                    now,
                ),
            )
        except Exception as exc:
            row = conn.execute(
                "SELECT * FROM notification_outbox WHERE event_key=? AND channel=?",
                (event_key, channel),
            ).fetchone()
            if row is None:
                raise exc
            result = _row_to_dict(row)
            result["deduplicated"] = True
            return result
        row = conn.execute(
            "SELECT * FROM notification_outbox WHERE id=?", (notification_id,)
        ).fetchone()
    return _row_to_dict(row)


def list_notifications(
    *,
    status: str | None = None,
    limit: int = 100,
    db_path: str | None = None,
) -> list[dict]:
    init_db(db_path)
    limit = max(1, min(500, int(limit)))
    with connect(db_path) as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM notification_outbox WHERE status=? ORDER BY created_at DESC LIMIT ?",
                (str(status).upper(), limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM notification_outbox ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
    return [_row_to_dict(row) for row in rows]


def _mark_result(
    notification_id: str,
    *,
    ok: bool,
    attempt: int,
    max_attempts: int,
    error: str = "",
    db_path: str | None = None,
) -> None:
    now = datetime.now(timezone.utc)
    if ok:
        with connect(db_path) as conn:
            conn.execute(
                """
                UPDATE notification_outbox
                SET status='DELIVERED', delivered_at=?, updated_at=?, last_error=NULL
                WHERE id=?
                """,
                (now.isoformat(), now.isoformat(), notification_id),
            )
        return
    exhausted = attempt >= max_attempts
    status = "DEAD" if exhausted else "FAILED"
    delay = _BACKOFF_SECONDS[min(max(attempt - 1, 0), len(_BACKOFF_SECONDS) - 1)]
    next_at = now + timedelta(seconds=delay)
    with connect(db_path) as conn:
        conn.execute(
            """
            UPDATE notification_outbox
            SET status=?, next_attempt_at=?, updated_at=?, last_error=?
            WHERE id=?
            """,
            (status, next_at.isoformat(), now.isoformat(), str(error)[:1000], notification_id),
        )


def deliver_due(
    sender: Callable[[str, dict], object],
    *,
    limit: int = 20,
    db_path: str | None = None,
) -> dict:
    """Deliver due rows. `sender(channel, payload)` may raise or return false."""
    if not callable(sender):
        raise ValueError("sender 必須可呼叫")
    init_db(db_path)
    now = utc_now()
    limit = max(1, min(100, int(limit)))
    with connect(db_path) as conn:
        rows = conn.execute(
            """
            SELECT * FROM notification_outbox
            WHERE status IN ('PENDING','FAILED')
              AND next_attempt_at <= ?
              AND attempts < max_attempts
            ORDER BY created_at ASC LIMIT ?
            """,
            (now, limit),
        ).fetchall()

    delivered = failed = 0
    errors: list[dict] = []
    for row in rows:
        notification_id = row["id"]
        with connect(db_path) as conn:
            claimed = conn.execute(
                """
                UPDATE notification_outbox
                SET status='IN_PROGRESS', attempts=attempts+1, updated_at=?
                WHERE id=? AND status IN ('PENDING','FAILED')
                """,
                (utc_now(), notification_id),
            ).rowcount
            if not claimed:
                continue
            current = conn.execute(
                "SELECT * FROM notification_outbox WHERE id=?", (notification_id,)
            ).fetchone()
        attempt = int(current["attempts"])
        try:
            response = sender(current["channel"], loads(current["payload_json"], {}))
            if response is False or (isinstance(response, dict) and not response.get("ok", True)):
                raise RuntimeError(
                    (response or {}).get("error", "sender returned failure")
                    if isinstance(response, dict)
                    else "sender returned failure"
                )
        except Exception as exc:
            failed += 1
            errors.append({"id": notification_id, "error": str(exc)[:300]})
            _mark_result(
                notification_id,
                ok=False,
                attempt=attempt,
                max_attempts=int(current["max_attempts"]),
                error=str(exc),
                db_path=db_path,
            )
        else:
            delivered += 1
            _mark_result(
                notification_id,
                ok=True,
                attempt=attempt,
                max_attempts=int(current["max_attempts"]),
                db_path=db_path,
            )
    return {
        "ok": failed == 0,
        "processed": delivered + failed,
        "delivered": delivered,
        "failed": failed,
        "errors": errors,
    }


def retry(
    notification_id: str | None = None,
    *,
    db_path: str | None = None,
) -> int:
    init_db(db_path)
    now = utc_now()
    with connect(db_path) as conn:
        if notification_id:
            changed = conn.execute(
                """
                UPDATE notification_outbox
                SET status='PENDING', attempts=0, next_attempt_at=?, updated_at=?, last_error=NULL
                WHERE id=? AND status IN ('FAILED','DEAD')
                """,
                (now, now, str(notification_id)),
            ).rowcount
        else:
            changed = conn.execute(
                """
                UPDATE notification_outbox
                SET status='PENDING', attempts=0, next_attempt_at=?, updated_at=?, last_error=NULL
                WHERE status IN ('FAILED','DEAD')
                """,
                (now, now),
            ).rowcount
    return int(changed)


def summary(db_path: str | None = None) -> dict:
    init_db(db_path)
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) AS count FROM notification_outbox GROUP BY status"
        ).fetchall()
    counts = {row["status"]: row["count"] for row in rows}
    return {
        "total": sum(counts.values()),
        "pending": counts.get("PENDING", 0),
        "in_progress": counts.get("IN_PROGRESS", 0),
        "delivered": counts.get("DELIVERED", 0),
        "failed": counts.get("FAILED", 0),
        "dead": counts.get("DEAD", 0),
    }
