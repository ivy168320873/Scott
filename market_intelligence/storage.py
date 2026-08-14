"""SQLite persistence for articles, runs, catalysts, state, and leases."""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def connect(db_path: str) -> sqlite3.Connection:
    parent = os.path.dirname(os.path.abspath(db_path))
    os.makedirs(parent, exist_ok=True)
    con = sqlite3.connect(db_path, timeout=15, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=15000")
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    return con


def init_db(db_path: str) -> None:
    with connect(db_path) as con:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS mi_articles (
                dedupe_key TEXT PRIMARY KEY,
                source_provider TEXT NOT NULL,
                publisher TEXT,
                title TEXT NOT NULL,
                summary TEXT,
                url TEXT,
                published_at TEXT,
                symbols_json TEXT NOT NULL DEFAULT '[]',
                provider_sentiment REAL,
                analysis_json TEXT NOT NULL DEFAULT '{}',
                raw_json TEXT NOT NULL DEFAULT '{}',
                first_seen_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS mi_articles_published
                ON mi_articles(published_at DESC);

            CREATE TABLE IF NOT EXISTS mi_runs (
                id TEXT PRIMARY KEY,
                run_type TEXT NOT NULL,
                started_at TEXT NOT NULL,
                completed_at TEXT,
                status TEXT NOT NULL,
                fetched_count INTEGER NOT NULL DEFAULT 0,
                new_count INTEGER NOT NULL DEFAULT 0,
                analyzed_count INTEGER NOT NULL DEFAULT 0,
                report_json TEXT NOT NULL DEFAULT '{}',
                error TEXT
            );
            CREATE INDEX IF NOT EXISTS mi_runs_started ON mi_runs(started_at DESC);

            CREATE TABLE IF NOT EXISTS mi_symbol_catalysts (
                symbol TEXT PRIMARY KEY,
                direction TEXT NOT NULL,
                score_adjustment REAL NOT NULL,
                importance INTEGER NOT NULL,
                confidence INTEGER NOT NULL,
                summary TEXT NOT NULL,
                headlines_json TEXT NOT NULL DEFAULT '[]',
                source_urls_json TEXT NOT NULL DEFAULT '[]',
                updated_at TEXT NOT NULL,
                expires_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS mi_state (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS mi_leases (
                name TEXT PRIMARY KEY,
                owner TEXT NOT NULL,
                lease_until TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )


def start_run(db_path: str, run_type: str) -> str:
    init_db(db_path)
    run_id = uuid.uuid4().hex
    with connect(db_path) as con:
        con.execute(
            "INSERT INTO mi_runs(id,run_type,started_at,status) VALUES(?,?,?,'RUNNING')",
            (run_id, str(run_type)[:30], utc_now()),
        )
    return run_id


def finish_run(
    db_path: str,
    run_id: str,
    *,
    status: str,
    fetched_count: int = 0,
    new_count: int = 0,
    analyzed_count: int = 0,
    report: dict | None = None,
    error: str = "",
) -> None:
    with connect(db_path) as con:
        con.execute(
            """
            UPDATE mi_runs SET completed_at=?, status=?, fetched_count=?, new_count=?,
                analyzed_count=?, report_json=?, error=? WHERE id=?
            """,
            (
                utc_now(),
                str(status).upper()[:20],
                max(0, int(fetched_count)),
                max(0, int(new_count)),
                max(0, int(analyzed_count)),
                json.dumps(report or {}, ensure_ascii=False),
                str(error)[:2000] or None,
                run_id,
            ),
        )


def upsert_articles(db_path: str, articles: list[dict]) -> set[str]:
    """Persist articles and return the keys that were new in this call."""
    init_db(db_path)
    inserted: set[str] = set()
    now = utc_now()
    with connect(db_path) as con:
        for article in articles:
            key = str(article.get("dedupe_key") or "")[:128]
            if not key:
                continue
            exists = con.execute(
                "SELECT 1 FROM mi_articles WHERE dedupe_key=?", (key,)
            ).fetchone()
            if not exists:
                inserted.add(key)
            con.execute(
                """
                INSERT INTO mi_articles(
                    dedupe_key,source_provider,publisher,title,summary,url,published_at,
                    symbols_json,provider_sentiment,analysis_json,raw_json,first_seen_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(dedupe_key) DO UPDATE SET
                    source_provider=excluded.source_provider,
                    publisher=excluded.publisher,
                    title=excluded.title,
                    summary=excluded.summary,
                    url=excluded.url,
                    published_at=excluded.published_at,
                    symbols_json=excluded.symbols_json,
                    provider_sentiment=excluded.provider_sentiment,
                    analysis_json=excluded.analysis_json,
                    raw_json=excluded.raw_json,
                    updated_at=excluded.updated_at
                """,
                (
                    key,
                    str(article.get("source_provider") or "unknown")[:80],
                    str(article.get("publisher") or "")[:160],
                    str(article.get("title") or "")[:1000],
                    str(article.get("summary") or "")[:5000],
                    str(article.get("url") or "")[:2000],
                    str(article.get("published_at") or "")[:40],
                    json.dumps(article.get("symbols") or [], ensure_ascii=False),
                    article.get("provider_sentiment"),
                    json.dumps(article.get("analysis") or {}, ensure_ascii=False),
                    json.dumps(article.get("raw") or {}, ensure_ascii=False)[:50_000],
                    now,
                    now,
                ),
            )
    return inserted


def recent_articles(db_path: str, *, limit: int = 100) -> list[dict]:
    init_db(db_path)
    limit = max(1, min(500, int(limit)))
    with connect(db_path) as con:
        rows = con.execute(
            "SELECT * FROM mi_articles ORDER BY published_at DESC, first_seen_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        for field, fallback in (
            ("symbols_json", []),
            ("analysis_json", {}),
            ("raw_json", {}),
        ):
            try:
                item[field.removesuffix("_json")] = json.loads(item.pop(field))
            except (TypeError, ValueError):
                item[field.removesuffix("_json")] = fallback
        result.append(item)
    return result


def article_analyses(db_path: str, keys: list[str]) -> dict[str, dict]:
    """Return prior validated analyses so unchanged news never spends AI twice."""
    init_db(db_path)
    clean_keys = [str(key)[:128] for key in dict.fromkeys(keys) if str(key).strip()]
    if not clean_keys:
        return {}
    placeholders = ",".join("?" for _ in clean_keys)
    with connect(db_path) as con:
        rows = con.execute(
            f"SELECT dedupe_key,analysis_json FROM mi_articles "
            f"WHERE dedupe_key IN ({placeholders})",
            clean_keys,
        ).fetchall()
    result = {}
    for row in rows:
        try:
            analysis = json.loads(row["analysis_json"])
        except (TypeError, ValueError):
            continue
        if isinstance(analysis, dict) and analysis:
            result[row["dedupe_key"]] = analysis
    return result


def latest_report(db_path: str, run_type: str | None = None) -> dict | None:
    init_db(db_path)
    with connect(db_path) as con:
        if run_type:
            row = con.execute(
                """SELECT * FROM mi_runs WHERE status='SUCCESS' AND run_type=?
                   ORDER BY completed_at DESC LIMIT 1""",
                (run_type,),
            ).fetchone()
        else:
            row = con.execute(
                """SELECT * FROM mi_runs WHERE status='SUCCESS'
                   ORDER BY completed_at DESC LIMIT 1"""
            ).fetchone()
    if not row:
        return None
    try:
        report = json.loads(row["report_json"])
    except (TypeError, ValueError):
        report = {}
    report["_run"] = {
        "id": row["id"],
        "run_type": row["run_type"],
        "started_at": row["started_at"],
        "completed_at": row["completed_at"],
        "fetched_count": row["fetched_count"],
        "new_count": row["new_count"],
    }
    return report


def save_catalyst(db_path: str, catalyst: dict, *, ttl_hours: int = 48) -> None:
    init_db(db_path)
    now = datetime.now(timezone.utc)
    expires = now + timedelta(hours=max(1, min(168, int(ttl_hours))))
    with connect(db_path) as con:
        con.execute(
            """
            INSERT INTO mi_symbol_catalysts(
                symbol,direction,score_adjustment,importance,confidence,summary,
                headlines_json,source_urls_json,updated_at,expires_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(symbol) DO UPDATE SET
                direction=excluded.direction,
                score_adjustment=excluded.score_adjustment,
                importance=excluded.importance,
                confidence=excluded.confidence,
                summary=excluded.summary,
                headlines_json=excluded.headlines_json,
                source_urls_json=excluded.source_urls_json,
                updated_at=excluded.updated_at,
                expires_at=excluded.expires_at
            """,
            (
                str(catalyst.get("symbol") or "").upper()[:20],
                str(catalyst.get("direction") or "NEUTRAL")[:12],
                max(-8.0, min(8.0, float(catalyst.get("score_adjustment") or 0))),
                max(0, min(100, int(catalyst.get("importance") or 0))),
                max(0, min(100, int(catalyst.get("confidence") or 0))),
                str(catalyst.get("summary") or "")[:2000],
                json.dumps(catalyst.get("headlines") or [], ensure_ascii=False),
                json.dumps(catalyst.get("source_urls") or [], ensure_ascii=False),
                now.isoformat(),
                expires.isoformat(),
            ),
        )


def get_symbol_catalyst(db_path: str, symbol: str) -> dict | None:
    init_db(db_path)
    with connect(db_path) as con:
        row = con.execute(
            "SELECT * FROM mi_symbol_catalysts WHERE symbol=? AND expires_at>?",
            (str(symbol or "").upper()[:20], utc_now()),
        ).fetchone()
    if not row:
        return None
    result = dict(row)
    for field in ("headlines_json", "source_urls_json"):
        try:
            result[field.removesuffix("_json")] = json.loads(result.pop(field))
        except (TypeError, ValueError):
            result[field.removesuffix("_json")] = []
    return result


def list_catalysts(db_path: str, *, limit: int = 100) -> list[dict]:
    init_db(db_path)
    with connect(db_path) as con:
        rows = con.execute(
            """SELECT * FROM mi_symbol_catalysts WHERE expires_at>?
               ORDER BY importance DESC, updated_at DESC LIMIT ?""",
            (utc_now(), max(1, min(500, int(limit)))),
        ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        for field in ("headlines_json", "source_urls_json"):
            try:
                item[field.removesuffix("_json")] = json.loads(item.pop(field))
            except (TypeError, ValueError):
                item[field.removesuffix("_json")] = []
        result.append(item)
    return result


def set_state(db_path: str, key: str, value: Any) -> None:
    init_db(db_path)
    with connect(db_path) as con:
        con.execute(
            """INSERT INTO mi_state(key,value,updated_at) VALUES(?,?,?)
               ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at""",
            (str(key)[:100], json.dumps(value, ensure_ascii=False), utc_now()),
        )


def get_state(db_path: str, key: str, default=None):
    init_db(db_path)
    with connect(db_path) as con:
        row = con.execute(
            "SELECT value FROM mi_state WHERE key=?", (str(key)[:100],)
        ).fetchone()
    if not row:
        return default
    try:
        return json.loads(row["value"])
    except (TypeError, ValueError):
        return default


def acquire_lease(db_path: str, name: str, owner: str, *, seconds: int = 900) -> bool:
    """Acquire a renewable SQLite lease so web and worker runs cannot overlap."""
    init_db(db_path)
    now = datetime.now(timezone.utc)
    until = now + timedelta(seconds=max(30, min(3600, int(seconds))))
    with connect(db_path) as con:
        con.execute("BEGIN IMMEDIATE")
        row = con.execute(
            "SELECT owner,lease_until FROM mi_leases WHERE name=?", (name,)
        ).fetchone()
        if row and row["owner"] != owner and row["lease_until"] > now.isoformat():
            return False
        con.execute(
            """INSERT INTO mi_leases(name,owner,lease_until,updated_at) VALUES(?,?,?,?)
               ON CONFLICT(name) DO UPDATE SET owner=excluded.owner,
               lease_until=excluded.lease_until,updated_at=excluded.updated_at""",
            (name, owner, until.isoformat(), now.isoformat()),
        )
    return True


def release_lease(db_path: str, name: str, owner: str) -> None:
    init_db(db_path)
    with connect(db_path) as con:
        con.execute("DELETE FROM mi_leases WHERE name=? AND owner=?", (name, owner))


def status(db_path: str) -> dict:
    init_db(db_path)
    with connect(db_path) as con:
        latest = con.execute(
            """SELECT id,run_type,started_at,completed_at,status,fetched_count,
                      new_count,analyzed_count,error
               FROM mi_runs ORDER BY started_at DESC LIMIT 1"""
        ).fetchone()
        article_count = con.execute("SELECT COUNT(*) FROM mi_articles").fetchone()[0]
        catalyst_count = con.execute(
            "SELECT COUNT(*) FROM mi_symbol_catalysts WHERE expires_at>?", (utc_now(),)
        ).fetchone()[0]
    return {
        "article_count": article_count,
        "active_catalysts": catalyst_count,
        "latest_run": dict(latest) if latest else None,
    }
