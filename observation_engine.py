"""
Phase 11 – Live Observation Period Engine
Tracks signal quality during the live-observation window (no auto-trading).
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from datetime import date, datetime, timedelta
from typing import Callable

_DB_PATH = "user_data.db"
_LOCK = threading.Lock()

# ── DB init ────────────────────────────────────────────────────────────────────

def init_db(db_path: str = _DB_PATH) -> None:
    global _DB_PATH
    _DB_PATH = db_path
    with sqlite3.connect(_DB_PATH) as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS signal_observations (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                date        TEXT    NOT NULL,
                symbol      TEXT    NOT NULL,
                signal_type TEXT    NOT NULL,
                signal_class TEXT   NOT NULL DEFAULT '',
                signal      TEXT    NOT NULL DEFAULT '',
                score       REAL    NOT NULL DEFAULT 0,
                price_at_signal REAL NOT NULL DEFAULT 0,
                market_state TEXT   NOT NULL DEFAULT '',
                is_demo     INTEGER NOT NULL DEFAULT 0,
                created_at  TEXT    NOT NULL
            );
            CREATE TABLE IF NOT EXISTS obs_outcomes (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                obs_id       INTEGER NOT NULL,
                check_days   INTEGER NOT NULL,
                price_at_check REAL  NOT NULL DEFAULT 0,
                pct_change   REAL    NOT NULL DEFAULT 0,
                checked_at   TEXT    NOT NULL,
                FOREIGN KEY (obs_id) REFERENCES signal_observations(id)
            );
            CREATE TABLE IF NOT EXISTS obs_daily_log (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                date                TEXT    NOT NULL UNIQUE,
                market_state        TEXT    NOT NULL DEFAULT '',
                sector_leaders_json TEXT    NOT NULL DEFAULT '[]',
                top_picks_json      TEXT    NOT NULL DEFAULT '[]',
                kill_signals_json   TEXT    NOT NULL DEFAULT '[]',
                sell_signals_json   TEXT    NOT NULL DEFAULT '[]',
                high_chase_risk_json TEXT   NOT NULL DEFAULT '[]',
                rotation_recs_json  TEXT    NOT NULL DEFAULT '[]',
                alert_s_count       INTEGER NOT NULL DEFAULT 0,
                alert_a_count       INTEGER NOT NULL DEFAULT 0,
                notes               TEXT    NOT NULL DEFAULT '',
                created_at          TEXT    NOT NULL
            );
        """)
        conn.commit()


# ── Helpers ────────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _today() -> str:
    return date.today().isoformat()


def _j(v) -> str:
    if isinstance(v, str):
        return v
    return json.dumps(v, ensure_ascii=False)


# ── Signal recording ───────────────────────────────────────────────────────────

def record_signal(
    symbol: str,
    signal_type: str,
    signal_class: str = "",
    signal: str = "",
    score: float = 0.0,
    price_at_signal: float = 0.0,
    market_state: str = "",
    is_demo: bool = False,
    obs_date: str | None = None,
) -> int:
    """Insert one signal observation. Returns the new row id."""
    today = obs_date or _today()
    with _LOCK, sqlite3.connect(_DB_PATH) as conn:
        cur = conn.execute(
            """INSERT INTO signal_observations
               (date, symbol, signal_type, signal_class, signal,
                score, price_at_signal, market_state, is_demo, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (today, symbol.upper(), signal_type, signal_class, signal,
             round(score, 2), round(price_at_signal, 4),
             market_state, int(is_demo), _now_iso()),
        )
        conn.commit()
        return cur.lastrowid


# ── Outcome tracking ───────────────────────────────────────────────────────────

def update_outcomes(ohlcv_fn: Callable | None = None) -> dict:
    """
    For each observation without a 1/3/5-day outcome check, fetch price and
    compute pct_change. Returns {"updated": N, "errors": [...]}.
    """
    now = _now_iso()
    today_d = date.today()
    updated = 0
    errors: list[str] = []

    with _LOCK, sqlite3.connect(_DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM signal_observations ORDER BY id"
        ).fetchall()

        for row in rows:
            obs_date = date.fromisoformat(row["date"])
            obs_id = row["id"]
            sym = row["symbol"]
            p0 = row["price_at_signal"]
            if not p0:
                continue

            existing = {
                r["check_days"]
                for r in conn.execute(
                    "SELECT check_days FROM obs_outcomes WHERE obs_id=?", (obs_id,)
                )
            }

            for days in (1, 3, 5):
                check_date = obs_date + timedelta(days=days)
                if check_date > today_d:
                    continue
                if days in existing:
                    continue
                # Try to fetch price
                price = 0.0
                try:
                    if ohlcv_fn:
                        ohlcv = ohlcv_fn(sym)
                        if ohlcv and ohlcv.get("closes"):
                            closes = ohlcv["closes"]
                            # Use latest close as proxy when we can't do historic lookup
                            price = float(closes[-1])
                except Exception as e:
                    errors.append(f"{sym}[{days}d]: {e}")
                    continue

                if price <= 0:
                    continue

                pct = round((price - p0) / p0 * 100, 2)
                conn.execute(
                    """INSERT INTO obs_outcomes
                       (obs_id, check_days, price_at_check, pct_change, checked_at)
                       VALUES (?,?,?,?,?)""",
                    (obs_id, days, round(price, 4), pct, now),
                )
                updated += 1

        conn.commit()

    return {"updated": updated, "errors": errors}


# ── Signal stats ───────────────────────────────────────────────────────────────

def get_signal_stats(days: int = 14) -> dict:
    """Return signal quality statistics over the last `days` calendar days."""
    cutoff = (date.today() - timedelta(days=days)).isoformat()

    with sqlite3.connect(_DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        obs = conn.execute(
            "SELECT * FROM signal_observations WHERE date >= ? ORDER BY date DESC",
            (cutoff,),
        ).fetchall()

        if not obs:
            return {
                "period_days": days,
                "total": 0,
                "by_type": {},
                "hit_rates": {},
                "avg_pct_1d": None,
                "avg_pct_3d": None,
                "avg_pct_5d": None,
                "top_symbols": [],
                "disclaimer": "目前為實盤觀察期，不代表自動下單。",
            }

        obs_ids = [r["id"] for r in obs]
        outcomes_rows = conn.execute(
            f"SELECT * FROM obs_outcomes WHERE obs_id IN ({','.join('?'*len(obs_ids))})",
            obs_ids,
        ).fetchall()

    by_type: dict[str, int] = {}
    for r in obs:
        t = r["signal_type"]
        by_type[t] = by_type.get(t, 0) + 1

    # Outcome aggregation
    from collections import defaultdict
    out_map: dict[int, dict[int, float]] = defaultdict(dict)
    for o in outcomes_rows:
        out_map[o["obs_id"]][o["check_days"]] = o["pct_change"]

    pct_by_days: dict[int, list[float]] = {1: [], 3: [], 5: []}
    buy_hits_1d = buy_total_1d = 0
    for r in obs:
        oid = r["id"]
        sig = r["signal_type"]
        for d in (1, 3, 5):
            if d in out_map.get(oid, {}):
                pct_by_days[d].append(out_map[oid][d])
        if sig in ("BUY", "STRONG_BUY") and 1 in out_map.get(oid, {}):
            buy_total_1d += 1
            if out_map[oid][1] > 0:
                buy_hits_1d += 1

    def _avg(lst):
        return round(sum(lst) / len(lst), 2) if lst else None

    hit_rates = {}
    if buy_total_1d:
        hit_rates["BUY_1d_positive"] = round(buy_hits_1d / buy_total_1d * 100, 1)

    # Top 5 symbols by observation count
    sym_count: dict[str, int] = {}
    for r in obs:
        s = r["symbol"]
        sym_count[s] = sym_count.get(s, 0) + 1
    top_symbols = sorted(sym_count.items(), key=lambda x: -x[1])[:5]

    return {
        "period_days": days,
        "total": len(obs),
        "by_type": by_type,
        "hit_rates": hit_rates,
        "avg_pct_1d": _avg(pct_by_days[1]),
        "avg_pct_3d": _avg(pct_by_days[3]),
        "avg_pct_5d": _avg(pct_by_days[5]),
        "top_symbols": [{"symbol": s, "count": c} for s, c in top_symbols],
        "disclaimer": "目前為實盤觀察期，不代表自動下單。",
    }


# ── Daily log ──────────────────────────────────────────────────────────────────

def create_daily_log(
    log_date: str | None = None,
    market_state: str = "",
    sector_leaders: list | None = None,
    top_picks: list | None = None,
    kill_signals: list | None = None,
    sell_signals: list | None = None,
    high_chase_risk: list | None = None,
    rotation_recs: list | None = None,
    alert_s_count: int = 0,
    alert_a_count: int = 0,
    notes: str = "",
) -> dict:
    today = log_date or _today()
    now = _now_iso()
    with _LOCK, sqlite3.connect(_DB_PATH) as conn:
        conn.execute(
            """INSERT INTO obs_daily_log
               (date, market_state, sector_leaders_json, top_picks_json,
                kill_signals_json, sell_signals_json, high_chase_risk_json,
                rotation_recs_json, alert_s_count, alert_a_count, notes, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(date) DO UPDATE SET
                 market_state=excluded.market_state,
                 sector_leaders_json=excluded.sector_leaders_json,
                 top_picks_json=excluded.top_picks_json,
                 kill_signals_json=excluded.kill_signals_json,
                 sell_signals_json=excluded.sell_signals_json,
                 high_chase_risk_json=excluded.high_chase_risk_json,
                 rotation_recs_json=excluded.rotation_recs_json,
                 alert_s_count=excluded.alert_s_count,
                 alert_a_count=excluded.alert_a_count,
                 notes=excluded.notes,
                 created_at=excluded.created_at""",
            (
                today,
                market_state,
                _j(sector_leaders or []),
                _j(top_picks or []),
                _j(kill_signals or []),
                _j(sell_signals or []),
                _j(high_chase_risk or []),
                _j(rotation_recs or []),
                alert_s_count,
                alert_a_count,
                notes,
                now,
            ),
        )
        conn.commit()
    return get_daily_log(log_date=today)


def get_daily_log(days: int = 14, log_date: str | None = None) -> dict | list:
    with sqlite3.connect(_DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        if log_date:
            row = conn.execute(
                "SELECT * FROM obs_daily_log WHERE date=?", (log_date,)
            ).fetchone()
            if not row:
                return {}
            return _log_row_to_dict(row)

        cutoff = (date.today() - timedelta(days=days)).isoformat()
        rows = conn.execute(
            "SELECT * FROM obs_daily_log WHERE date >= ? ORDER BY date DESC",
            (cutoff,),
        ).fetchall()
        return [_log_row_to_dict(r) for r in rows]


def _log_row_to_dict(row) -> dict:
    def _parse(v):
        try:
            return json.loads(v)
        except Exception:
            return v

    return {
        "_db_id": row["id"],
        "_db_date": row["date"],
        "date": row["date"],
        "market_state": row["market_state"],
        "sector_leaders": _parse(row["sector_leaders_json"]),
        "top_picks": _parse(row["top_picks_json"]),
        "kill_signals": _parse(row["kill_signals_json"]),
        "sell_signals": _parse(row["sell_signals_json"]),
        "high_chase_risk": _parse(row["high_chase_risk_json"]),
        "rotation_recs": _parse(row["rotation_recs_json"]),
        "alert_s_count": row["alert_s_count"],
        "alert_a_count": row["alert_a_count"],
        "notes": row["notes"],
        "created_at": row["created_at"],
    }


# ── Accuracy report ────────────────────────────────────────────────────────────

def get_accuracy_report() -> dict:
    """End-of-period accuracy report: hit rates, best indicators, go/no-go."""
    with sqlite3.connect(_DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        obs = conn.execute(
            "SELECT * FROM signal_observations ORDER BY date"
        ).fetchall()
        outcomes = conn.execute(
            "SELECT * FROM obs_outcomes"
        ).fetchall()

    if not obs:
        return {
            "total_signals": 0,
            "observation_days": 0,
            "verdict": "insufficient_data",
            "disclaimer": "目前為實盤觀察期，不代表自動下單。",
        }

    from collections import defaultdict
    out_map: dict[int, dict[int, float]] = defaultdict(dict)
    for o in outcomes:
        out_map[o["obs_id"]][o["check_days"]] = o["pct_change"]

    dates = sorted({r["date"] for r in obs})
    obs_days = len(dates)

    # Per signal-type accuracy
    type_stats: dict[str, dict] = {}
    for r in obs:
        t = r["signal_type"]
        if t not in type_stats:
            type_stats[t] = {"total": 0, "hits_1d": 0, "hits_3d": 0, "pct_1d": [], "pct_3d": []}
        st = type_stats[t]
        st["total"] += 1
        oid = r["id"]
        if 1 in out_map.get(oid, {}):
            p1 = out_map[oid][1]
            st["pct_1d"].append(p1)
            if p1 > 0:
                st["hits_1d"] += 1
        if 3 in out_map.get(oid, {}):
            p3 = out_map[oid][3]
            st["pct_3d"].append(p3)
            if p3 > 0:
                st["hits_3d"] += 1

    def _avg(lst):
        return round(sum(lst) / len(lst), 2) if lst else None

    type_summary = {}
    for t, st in type_stats.items():
        n = st["total"]
        h1 = st["hits_1d"]
        checked1 = len(st["pct_1d"])
        type_summary[t] = {
            "total": n,
            "hit_rate_1d": round(h1 / checked1 * 100, 1) if checked1 else None,
            "avg_pct_1d": _avg(st["pct_1d"]),
            "avg_pct_3d": _avg(st["pct_3d"]),
        }

    # Best and worst indicators by 1d hit rate
    ranked = sorted(
        [(t, v["hit_rate_1d"]) for t, v in type_summary.items() if v["hit_rate_1d"] is not None],
        key=lambda x: -(x[1] or 0),
    )
    best_indicators = [r[0] for r in ranked[:3]]
    worst_indicators = [r[0] for r in ranked[-3:] if r[1] is not None and r[1] < 50]

    # Overall buy hit rate for go/no-go
    buy_types = {"BUY", "STRONG_BUY", "WATCH"}
    all_buy_obs = [r for r in obs if r["signal_type"] in buy_types]
    checked_buy = [r for r in all_buy_obs if 1 in out_map.get(r["id"], {})]
    hits = sum(1 for r in checked_buy if out_map[r["id"]][1] > 0)
    overall_hit_rate = round(hits / len(checked_buy) * 100, 1) if checked_buy else None

    # False signal rate: buy signals that dropped > 3% next day
    false_signals = sum(
        1 for r in checked_buy if out_map[r["id"]][1] < -3
    )
    false_signal_rate = round(false_signals / len(checked_buy) * 100, 1) if checked_buy else None

    # Go/no-go: need ≥7 obs days and ≥50% hit rate
    if obs_days >= 7 and overall_hit_rate is not None:
        go_no_go = "go" if overall_hit_rate >= 55 and (false_signal_rate or 0) < 30 else "no_go"
    else:
        go_no_go = "observing"

    return {
        "total_signals": len(obs),
        "observation_days": obs_days,
        "date_range": {"start": dates[0], "end": dates[-1]} if dates else {},
        "overall_hit_rate_1d": overall_hit_rate,
        "false_signal_rate": false_signal_rate,
        "by_signal_type": type_summary,
        "best_indicators": best_indicators,
        "worst_indicators": worst_indicators,
        "verdict": go_no_go,
        "disclaimer": "目前為實盤觀察期，不代表自動下單。",
    }
