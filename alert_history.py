"""
Alert History — SQLite-backed deduplication and audit trail.
Prevents notification spam and tracks alert state over time.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timezone, timedelta

_DB_PATH = os.environ.get("USER_DATA_DB", "./user_data.db")
_LOCK = threading.Lock()

LEVEL_RANK: dict[str, int] = {"S": 4, "A": 3, "B": 2, "C": 1}


# ── DB bootstrap ──────────────────────────────────────────────────────────────

def init_db() -> None:
    """Create alert_history table if it doesn't exist."""
    with _lock_conn() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS alert_history (
                id          TEXT NOT NULL,
                symbol      TEXT NOT NULL,
                alert_type  TEXT NOT NULL,
                level       TEXT NOT NULL,
                title       TEXT,
                message     TEXT,
                created_at  TEXT NOT NULL,
                resolved    INTEGER DEFAULT 0,
                PRIMARY KEY (id)
            )
        """)
        con.execute("CREATE INDEX IF NOT EXISTS ah_sym_type ON alert_history(symbol, alert_type)")
        con.execute("CREATE INDEX IF NOT EXISTS ah_created  ON alert_history(created_at)")


def _lock_conn():
    """Context manager that returns a sqlite3 connection with the module lock."""
    class _CM:
        def __enter__(self):
            _LOCK.acquire()
            self._con = sqlite3.connect(_DB_PATH, check_same_thread=False)
            self._con.row_factory = sqlite3.Row
            return self._con
        def __exit__(self, *_):
            self._con.commit()
            self._con.close()
            _LOCK.release()
    return _CM()


# ── Core dedup logic ─────────────────────────────────────────────────────────

def should_send(symbol: str, alert_type: str, new_level: str) -> bool:
    """
    Returns True if the alert should be sent (and subsequently recorded).

    Rules:
    1. S级 → always send immediately.
    2. Same symbol+type with same or higher level sent within 24 h → suppress.
    3. Level escalated (e.g. B→A or A→S) → allow resend.
    4. No recent alert of this type → always send.
    """
    if new_level == "S":
        return True

    cutoff    = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    new_rank  = LEVEL_RANK.get(new_level, 0)

    with _lock_conn() as con:
        row = con.execute(
            """
            SELECT level FROM alert_history
            WHERE symbol=? AND alert_type=? AND created_at > ? AND resolved=0
            ORDER BY created_at DESC LIMIT 1
            """,
            (symbol, alert_type, cutoff),
        ).fetchone()

    if not row:
        return True                          # no recent alert — send

    existing_rank = LEVEL_RANK.get(row["level"], 0)
    return new_rank > existing_rank          # only send if escalating


# ── Record / query ────────────────────────────────────────────────────────────

def record(alert) -> None:
    """Persist a sent alert to history."""
    with _lock_conn() as con:
        con.execute(
            """
            INSERT OR REPLACE INTO alert_history
                (id, symbol, alert_type, level, title, message, created_at, resolved)
            VALUES (?, ?, ?, ?, ?, ?, ?, 0)
            """,
            (
                alert.id, alert.symbol, alert.alert_type,
                alert.level, alert.title, alert.message, alert.created_at,
            ),
        )


def get_recent(hours: int = 24) -> list[dict]:
    """Return alerts created within the last `hours` hours, newest first."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    with _lock_conn() as con:
        rows = con.execute(
            """
            SELECT * FROM alert_history
            WHERE created_at > ?
            ORDER BY created_at DESC
            LIMIT 500
            """,
            (cutoff,),
        ).fetchall()
    return [dict(r) for r in rows]


def resolve(alert_id: str) -> None:
    """Mark an alert as resolved."""
    with _lock_conn() as con:
        con.execute(
            "UPDATE alert_history SET resolved=1 WHERE id=?",
            (alert_id,),
        )


def get_stats() -> dict:
    """Summary counts for the last 24 hours."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    with _lock_conn() as con:
        rows = con.execute(
            """
            SELECT level, COUNT(*) as cnt
            FROM alert_history
            WHERE created_at > ?
            GROUP BY level
            """,
            (cutoff,),
        ).fetchall()
    return {r["level"]: r["cnt"] for r in rows}
