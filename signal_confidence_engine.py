"""
Signal Confidence Engine — Phase 12C
Records, tracks, and calibrates the quality of top-tier decision signals.

Public API
----------
init_db(db_path)                     -> None
record_signal(payload)               -> dict
record_signal_once(payload)          -> dict
update_outcomes(updates)             -> dict
update_symbol_outcomes(symbol, ohlcv, benchmark_ohlcv=None) -> dict
get_confidence_stats(signal_type)    -> list[dict] | dict
get_signal_history(limit)            -> list[dict]
get_calibration_override(decision)   -> dict  (used by top_tier_decision_engine)
"""
from __future__ import annotations

import math
import os
import json
import sqlite3
import threading
from datetime import date, datetime, timezone

_DB_PATH = os.environ.get("USER_DATA_DB", "./user_data.db")
_LOCK    = threading.RLock()

SIGNAL_TYPES = [
    "STRONG_BUY", "BUY", "WATCH", "HOLD",
    "TRIM", "SELL", "AVOID",
    "KILL_SIGNAL", "CHASE_RISK_HIGH",
]

# Calibration gates count independent signal dates, not raw symbols.  Ten
# stocks firing on the same macro day are correlated observations, not ten
# independent trials.
_MIN_CONFIDENCE_DAYS = 10
_MIN_TRUST_DAYS = 30
_MIN_KELLY_DAYS = 50
_MAX_KELLY_BRIER = 0.20
_MAX_KELLY_GAP_PCT = 10.0

_DDL = """
CREATE TABLE IF NOT EXISTS signal_history (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol                  TEXT    NOT NULL,
    signal_date             TEXT    NOT NULL,
    decision                TEXT    NOT NULL,
    top_tier_score          REAL,
    market_regime           TEXT,
    market_score            REAL,
    data_quality_status     TEXT,
    chase_risk_score        REAL,
    sell_signal             TEXT,
    kill_signal_triggered   INTEGER DEFAULT 0,
    sector_leadership       TEXT,
    position_size_level     TEXT,
    entry_price             REAL,
    entry_fill_price        REAL,
    entry_fill_date         TEXT,
    entry_gap_pct           REAL,
    round_trip_cost_pct     REAL,
    price_1d                REAL,
    price_3d                REAL,
    price_5d                REAL,
    return_1d               REAL,
    return_3d               REAL,
    return_5d               REAL,
    net_return_1d           REAL,
    net_return_3d           REAL,
    net_return_5d           REAL,
    benchmark_return_1d     REAL,
    benchmark_return_3d     REAL,
    benchmark_return_5d     REAL,
    relative_return_1d      REAL,
    relative_return_3d      REAL,
    relative_return_5d      REAL,
    max_favorable_excursion REAL,
    max_adverse_excursion   REAL,
    hit_stop_loss           INTEGER DEFAULT 0,
    was_correct             INTEGER,
    false_signal_reason     TEXT,
    paper_trade_id          TEXT,
    paper_return_pct        REAL,
    paper_r_multiple        REAL,
    paper_exit_reason       TEXT,
    paper_closed_at         TEXT,
    prediction_probability  REAL,
    quote_source            TEXT,
    quote_timestamp         TEXT,
    quote_age_seconds       REAL,
    data_snapshot_json      TEXT,
    calibration_regime      TEXT,
    outcome_model           TEXT,
    is_demo                 INTEGER DEFAULT 0,
    created_at              TEXT    NOT NULL,
    updated_at              TEXT
);
CREATE INDEX IF NOT EXISTS sh_decision ON signal_history(decision);
CREATE INDEX IF NOT EXISTS sh_symbol   ON signal_history(symbol);
CREATE INDEX IF NOT EXISTS sh_date     ON signal_history(signal_date);
"""

_PAPER_COLUMNS = {
    "paper_trade_id": "TEXT",
    "paper_return_pct": "REAL",
    "paper_r_multiple": "REAL",
    "paper_exit_reason": "TEXT",
    "paper_closed_at": "TEXT",
}

_INSTITUTIONAL_COLUMNS = {
    "entry_fill_price": "REAL",
    "entry_fill_date": "TEXT",
    "entry_gap_pct": "REAL",
    "round_trip_cost_pct": "REAL",
    "net_return_1d": "REAL",
    "net_return_3d": "REAL",
    "net_return_5d": "REAL",
    "prediction_probability": "REAL",
    "quote_source": "TEXT",
    "quote_timestamp": "TEXT",
    "quote_age_seconds": "REAL",
    "data_snapshot_json": "TEXT",
    "calibration_regime": "TEXT",
    "outcome_model": "TEXT",
}


# ── DB init ───────────────────────────────────────────────────────────────────

def init_db(db_path: str = _DB_PATH) -> None:
    global _DB_PATH
    _DB_PATH = db_path
    try:
        with _LOCK, sqlite3.connect(_DB_PATH) as conn:
            conn.executescript(_DDL)
            existing = {
                row[1] for row in conn.execute("PRAGMA table_info(signal_history)").fetchall()
            }
            for column, sql_type in {
                **_PAPER_COLUMNS,
                **_INSTITUTIONAL_COLUMNS,
            }.items():
                if column not in existing:
                    conn.execute(
                        f"ALTER TABLE signal_history ADD COLUMN {column} {sql_type}"
                    )
    except Exception:
        import traceback
        traceback.print_exc()


# ── Record a new signal ───────────────────────────────────────────────────────

def record_signal(payload: dict) -> dict:
    """
    Insert one signal record into signal_history.

    Required: symbol, decision
    Optional: all other fields from payload dict
    """
    sym = str(payload.get("symbol") or "").upper().strip()
    if not sym:
        return {"ok": False, "error": "symbol is required"}
    decision = str(payload.get("decision") or "").upper().strip()
    if not decision:
        return {"ok": False, "error": "decision is required"}

    now_iso     = datetime.now(timezone.utc).isoformat()
    signal_date = str(payload.get("signal_date") or now_iso[:10])

    snapshot = payload.get("data_snapshot")
    if isinstance(snapshot, (dict, list)):
        snapshot = json.dumps(snapshot, ensure_ascii=False, default=str)[:20000]
    elif snapshot is not None:
        snapshot = str(snapshot)[:20000]

    columns = [
        "symbol", "signal_date", "decision", "top_tier_score",
        "market_regime", "market_score", "data_quality_status",
        "chase_risk_score", "sell_signal", "kill_signal_triggered",
        "sector_leadership", "position_size_level", "entry_price",
        "entry_fill_price", "entry_fill_date", "entry_gap_pct",
        "round_trip_cost_pct", "price_1d", "price_3d", "price_5d",
        "return_1d", "return_3d", "return_5d", "net_return_1d",
        "net_return_3d", "net_return_5d", "benchmark_return_1d",
        "benchmark_return_3d", "benchmark_return_5d", "relative_return_1d",
        "relative_return_3d", "relative_return_5d",
        "max_favorable_excursion", "max_adverse_excursion",
        "hit_stop_loss", "was_correct", "false_signal_reason",
        "prediction_probability", "quote_source", "quote_timestamp",
        "quote_age_seconds", "data_snapshot_json", "calibration_regime",
        "outcome_model", "is_demo", "created_at", "updated_at",
    ]
    values = [
        sym, signal_date, decision, payload.get("top_tier_score"),
        payload.get("market_regime"), payload.get("market_score"),
        payload.get("data_quality_status"), payload.get("chase_risk_score"),
        payload.get("sell_signal"),
        int(bool(payload.get("kill_signal_triggered", False))),
        payload.get("sector_leadership"), payload.get("position_size_level"),
        payload.get("entry_price"), None, None, None, None,
        None, None, None, None, None, None, None, None, None,
        None, None, None, None, None, None, None, None,
        int(bool(payload.get("hit_stop_loss", False))), None, None,
        _normalized_probability(payload.get("prediction_probability")),
        str(payload.get("quote_source") or "")[:80] or None,
        str(payload.get("quote_timestamp") or "")[:64] or None,
        payload.get("quote_age_seconds"), snapshot,
        str(payload.get("calibration_regime") or payload.get("market_regime") or "")[:40] or None,
        "NEXT_OPEN_COST_ADJUSTED_V1",
        int(bool(payload.get("is_demo", False))), now_iso, None,
    ]

    try:
        with _LOCK, sqlite3.connect(_DB_PATH) as conn:
            placeholders = ",".join("?" for _ in columns)
            cur = conn.execute(
                f"INSERT INTO signal_history ({','.join(columns)}) VALUES ({placeholders})",
                values,
            )
            conn.commit()
            return {"ok": True, "id": cur.lastrowid, "symbol": sym, "decision": decision}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def record_signal_once(payload: dict) -> dict:
    """Record at most one identical symbol/date/decision signal per day."""
    sym = str(payload.get("symbol") or "").upper().strip()
    decision = str(payload.get("decision") or "").upper().strip()
    signal_date = str(
        payload.get("signal_date") or datetime.now(timezone.utc).date().isoformat()
    )
    if not sym:
        return {"ok": False, "error": "symbol is required"}
    if not decision:
        return {"ok": False, "error": "decision is required"}

    try:
        with _LOCK:
            with sqlite3.connect(_DB_PATH) as conn:
                row = conn.execute(
                    """SELECT id FROM signal_history
                       WHERE symbol=? AND signal_date=? AND decision=?
                       ORDER BY id DESC LIMIT 1""",
                    (sym, signal_date, decision),
                ).fetchone()
            if row:
                return {
                    "ok": True,
                    "id": row[0],
                    "symbol": sym,
                    "decision": decision,
                    "recorded": False,
                    "duplicate": True,
                }

            enriched = dict(payload)
            enriched.update({
                "symbol": sym,
                "decision": decision,
                "signal_date": signal_date,
            })
            result = record_signal(enriched)
            if result.get("ok"):
                result.update({"recorded": True, "duplicate": False})
            return result
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


# ── Update outcomes ───────────────────────────────────────────────────────────

def update_outcomes(updates) -> dict:
    """
    Update 1d/3d/5d prices and auto-compute returns + was_correct.

    updates: single dict OR list of dicts
    Each dict should have: id (or symbol+signal_date), plus any of:
      price_1d, price_3d, price_5d,
      benchmark_return_1d/3d/5d,
      hit_stop_loss, max_favorable_excursion, max_adverse_excursion
    """
    if isinstance(updates, dict):
        updates = [updates]

    updated, errors = 0, []
    now_iso = datetime.now(timezone.utc).isoformat()

    for u in updates:
        try:
            rec_id = u.get("id")
            if not rec_id:
                sym  = str(u.get("symbol") or "").upper()
                date = str(u.get("signal_date") or "")
                if sym and date:
                    with sqlite3.connect(_DB_PATH) as conn:
                        row = conn.execute(
                            "SELECT id FROM signal_history WHERE symbol=? AND signal_date=? ORDER BY id DESC LIMIT 1",
                            (sym, date),
                        ).fetchone()
                        if row:
                            rec_id = row[0]

            if not rec_id:
                errors.append(f"Cannot locate record: {u.get('id') or u.get('symbol')}/{u.get('signal_date')}")
                continue

            with _LOCK, sqlite3.connect(_DB_PATH) as conn:
                base = conn.execute(
                    """SELECT entry_price, entry_fill_price, decision,
                              kill_signal_triggered, round_trip_cost_pct
                       FROM signal_history WHERE id=?""",
                    (rec_id,),
                ).fetchone()
                if not base:
                    errors.append(f"Record id={rec_id} not found")
                    continue

                entry_price, stored_fill, decision, kill_trig, stored_cost = base
                entry_fill = u.get("entry_fill_price") or stored_fill
                entry = float(entry_fill or entry_price) if (entry_fill or entry_price) else None

                p1d = u.get("price_1d")
                p3d = u.get("price_3d")
                p5d = u.get("price_5d")

                r1d = u.get("return_1d")
                r3d = u.get("return_3d")
                r5d = u.get("return_5d")
                r1d = _pct_change(entry, p1d) if r1d is None else r1d
                r3d = _pct_change(entry, p3d) if r3d is None else r3d
                r5d = _pct_change(entry, p5d) if r5d is None else r5d

                round_trip_cost = u.get("round_trip_cost_pct")
                if round_trip_cost is None:
                    round_trip_cost = stored_cost or 0.0
                n1d = u.get("net_return_1d")
                n3d = u.get("net_return_3d")
                n5d = u.get("net_return_5d")
                n1d = _directional_net_return(decision, r1d, round_trip_cost) if n1d is None else n1d
                n3d = _directional_net_return(decision, r3d, round_trip_cost) if n3d is None else n3d
                n5d = _directional_net_return(decision, r5d, round_trip_cost) if n5d is None else n5d

                b1d = u.get("benchmark_return_1d")
                b3d = u.get("benchmark_return_3d")
                b5d = u.get("benchmark_return_5d")

                rel1d = _sub(r1d, b1d)
                rel3d = _sub(r3d, b3d)
                rel5d = _sub(r5d, b5d)

                hit_stop = int(bool(u.get("hit_stop_loss", False)))
                mfe = u.get("max_favorable_excursion")
                mae = u.get("max_adverse_excursion")

                was_correct = _determine_was_correct(
                    decision, n1d, n3d, n5d, rel5d, hit_stop
                )
                false_reason = _determine_false_reason(
                    decision, was_correct, r1d, r3d, r5d, rel5d
                )

                conn.execute("""
                    UPDATE signal_history SET
                        price_1d=?, price_3d=?, price_5d=?,
                        return_1d=?, return_3d=?, return_5d=?,
                        net_return_1d=?, net_return_3d=?, net_return_5d=?,
                        benchmark_return_1d=?, benchmark_return_3d=?, benchmark_return_5d=?,
                        relative_return_1d=?, relative_return_3d=?, relative_return_5d=?,
                        max_favorable_excursion=?, max_adverse_excursion=?,
                        hit_stop_loss=?, was_correct=?, false_signal_reason=?,
                        entry_fill_price=COALESCE(?, entry_fill_price),
                        entry_fill_date=COALESCE(?, entry_fill_date),
                        entry_gap_pct=COALESCE(?, entry_gap_pct),
                        round_trip_cost_pct=COALESCE(?, round_trip_cost_pct),
                        outcome_model=COALESCE(?, outcome_model),
                        updated_at=?
                    WHERE id=?
                """, (
                    p1d, p3d, p5d,
                    r1d, r3d, r5d,
                    n1d, n3d, n5d,
                    b1d, b3d, b5d,
                    rel1d, rel3d, rel5d,
                    mfe, mae,
                    hit_stop, was_correct, false_reason,
                    u.get("entry_fill_price"), u.get("entry_fill_date"),
                    u.get("entry_gap_pct"), u.get("round_trip_cost_pct"),
                    u.get("outcome_model"),
                    now_iso, rec_id,
                ))
                conn.commit()
                updated += 1

        except Exception as e:
            errors.append(str(e))

    return {"ok": True, "updated": updated, "errors": errors}


def update_symbol_outcomes(
    symbol: str,
    ohlcv: dict,
    benchmark_ohlcv: dict | None = None,
) -> dict:
    """Backfill pending 1/3/5 trading-day outcomes from normalized OHLCV.

    This is intentionally triggered when a symbol is analysed again.  It keeps
    calibration current without adding a background job or external scheduler.
    Demo data is never allowed to train the history.
    """
    sym = str(symbol or "").upper().strip()
    if not sym:
        return {"ok": False, "error": "symbol is required", "updated": 0}
    if not isinstance(ohlcv, dict) or ohlcv.get("is_demo"):
        return {"ok": True, "updated": 0, "skipped": "demo_or_missing_data"}

    bars = _dated_bars(ohlcv)
    if len(bars) < 2:
        return {"ok": True, "updated": 0, "skipped": "insufficient_bars"}
    benchmark_bars = _dated_bars(benchmark_ohlcv or {})

    try:
        with sqlite3.connect(_DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            pending = [dict(row) for row in conn.execute(
                """SELECT id, signal_date, entry_price, decision
                   FROM signal_history
                   WHERE symbol=? AND is_demo=0 AND return_5d IS NULL
                   ORDER BY signal_date ASC""",
                (sym,),
            ).fetchall()]
    except Exception as exc:
        return {"ok": False, "error": str(exc), "updated": 0}

    updates: list[dict] = []
    for row in pending:
        signal_day = _parse_day(row.get("signal_date"))
        if signal_day is None:
            continue
        entry_idx = _next_bar_index(bars, signal_day)
        if entry_idx is None:
            continue

        entry_bar = bars[entry_idx]
        raw_entry = _positive_float(entry_bar.get("open")) or entry_bar["close"]
        signal_reference = _positive_float(row.get("entry_price"))
        if signal_reference is None and entry_idx > 0:
            signal_reference = bars[entry_idx - 1]["close"]
        decision = str(row.get("decision") or "").upper()
        round_trip_cost = _round_trip_cost_pct(sym)
        modeled_fill = _modeled_entry_fill(raw_entry, decision, sym)
        update: dict = {
            "id": row["id"],
            "entry_fill_price": modeled_fill,
            "entry_fill_date": entry_bar["date"].isoformat(),
            "entry_gap_pct": _pct_change(signal_reference, raw_entry),
            "round_trip_cost_pct": round_trip_cost,
            "outcome_model": "NEXT_OPEN_COST_ADJUSTED_V1",
        }
        for horizon in (1, 3, 5):
            idx = entry_idx + horizon - 1
            if idx < len(bars):
                exit_price = bars[idx]["close"]
                gross_return = _pct_change(raw_entry, exit_price)
                update[f"price_{horizon}d"] = exit_price
                update[f"return_{horizon}d"] = gross_return
                update[f"net_return_{horizon}d"] = _directional_net_return(
                    decision, gross_return, round_trip_cost
                )
                benchmark_return = _forward_return_from_next_open(
                    benchmark_bars, signal_day, horizon
                )
                if benchmark_return is not None:
                    update[f"benchmark_return_{horizon}d"] = benchmark_return

        forward = bars[entry_idx:min(len(bars), entry_idx + 5)]
        mfe, mae = _directional_excursions(forward, raw_entry, decision)
        update["max_favorable_excursion"] = mfe
        update["max_adverse_excursion"] = mae
        updates.append(update)

    if not updates:
        return {"ok": True, "updated": 0, "pending": len(pending), "errors": []}
    result = update_outcomes(updates)
    result["pending"] = len(pending)
    return result


def update_paper_outcome(signal_id: int, payload: dict) -> dict:
    """Attach a closed paper trade to its originating signal.

    Paper returns stay in dedicated columns; they are not mislabeled as a
    fixed 1/3/5-day market outcome.  Calibration can use both datasets while
    keeping their meanings separate.
    """
    if not isinstance(payload, dict):
        return {"ok": False, "error": "payload must be a dict"}
    try:
        record_id = int(signal_id)
        paper_return = float(payload.get("paper_return_pct"))
        if not math.isfinite(paper_return):
            raise ValueError("paper_return_pct must be finite")
        r_multiple = payload.get("paper_r_multiple")
        if r_multiple is not None:
            r_multiple = float(r_multiple)
            if not math.isfinite(r_multiple):
                r_multiple = None
    except (TypeError, ValueError, OverflowError) as exc:
        return {"ok": False, "error": str(exc)}

    try:
        with _LOCK, sqlite3.connect(_DB_PATH) as conn:
            row = conn.execute(
                "SELECT decision, return_5d, was_correct FROM signal_history WHERE id=?",
                (record_id,),
            ).fetchone()
            if not row:
                return {"ok": False, "error": f"signal id={record_id} not found"}
            decision = str(row[0] or "").upper()
            fixed_horizon_exists = row[1] is not None
            if fixed_horizon_exists:
                correct = row[2]
            elif decision in ("BUY", "STRONG_BUY", "HOLD"):
                correct = int(paper_return > 0)
            elif decision in ("SELL", "TRIM", "AVOID", "KILL_SIGNAL", "CHASE_RISK_HIGH"):
                correct = int(paper_return < 0)
            else:
                correct = None
            false_reason = None
            if correct == 0:
                false_reason = (
                    f"模擬交易結果 {paper_return:+.2f}% 與 {decision} 訊號方向不一致"
                )
            now_iso = datetime.now(timezone.utc).isoformat()
            conn.execute(
                """
                UPDATE signal_history SET
                    paper_trade_id=?, paper_return_pct=?, paper_r_multiple=?,
                    paper_exit_reason=?, paper_closed_at=?,
                    was_correct=CASE WHEN return_5d IS NULL THEN ? ELSE was_correct END,
                    false_signal_reason=CASE
                        WHEN return_5d IS NULL THEN COALESCE(?, false_signal_reason)
                        ELSE false_signal_reason
                    END,
                    updated_at=?
                WHERE id=?
                """,
                (
                    str(payload.get("paper_trade_id") or "")[:80] or None,
                    paper_return,
                    r_multiple,
                    str(payload.get("paper_exit_reason") or "")[:40] or None,
                    str(payload.get("paper_closed_at") or now_iso)[:64],
                    correct,
                    false_reason,
                    now_iso,
                    record_id,
                ),
            )
            conn.commit()
        return {
            "ok": True,
            "updated": 1,
            "id": record_id,
            "paper_return_pct": round(paper_return, 4),
            "was_correct": correct,
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


# ── Confidence statistics ─────────────────────────────────────────────────────

def get_confidence_stats(
    signal_type: str | None = None, regime: str | None = None
):
    """
    Returns:
      signal_type=None  → list[dict] for every SIGNAL_TYPE
      signal_type=str   → single dict
    """
    try:
        with sqlite3.connect(_DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            regime_value = str(regime or "").upper().strip()
            if signal_type:
                stype = signal_type.upper()
                sql = "SELECT * FROM signal_history WHERE decision=? AND is_demo=0"
                params: list = [stype]
                if regime_value:
                    sql += " AND UPPER(COALESCE(calibration_regime, market_regime, ''))=?"
                    params.append(regime_value)
                sql += " ORDER BY signal_date DESC"
                rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
                result = _compute_stats(stype, rows)
                result["calibration_scope"] = (
                    f"REGIME:{regime_value}" if regime_value else "ALL_REGIMES"
                )
                return result
            else:
                result = []
                for stype in SIGNAL_TYPES:
                    sql = "SELECT * FROM signal_history WHERE decision=? AND is_demo=0"
                    params = [stype]
                    if regime_value:
                        sql += " AND UPPER(COALESCE(calibration_regime, market_regime, ''))=?"
                        params.append(regime_value)
                    sql += " ORDER BY signal_date DESC"
                    rows = [dict(r) for r in conn.execute(sql, params).fetchall()]
                    stats = _compute_stats(stype, rows)
                    stats["calibration_scope"] = (
                        f"REGIME:{regime_value}" if regime_value else "ALL_REGIMES"
                    )
                    result.append(stats)
                return result
    except Exception:
        if signal_type:
            return _empty_stats(signal_type.upper())
        return [_empty_stats(st) for st in SIGNAL_TYPES]


# ── Signal history ────────────────────────────────────────────────────────────

def get_signal_history(limit: int = 50) -> list[dict]:
    try:
        with sqlite3.connect(_DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM signal_history ORDER BY created_at DESC LIMIT ?",
                (max(1, min(500, int(limit))),),
            ).fetchall()
            return [dict(r) for r in rows]
    except Exception:
        return []


# ── Calibration override for top_tier_decision_engine ────────────────────────

def get_calibration_override(decision: str, regime: str = "NEUTRAL") -> dict:
    """
    Returns calibration guidance to apply in top_tier_decision_engine.

    Return fields:
      can_use          : bool  — False when recommendation is DISABLE
      max_decision     : str | None — hard cap (e.g. "WATCH" when BUY confidence low)
      confidence_score : int
      recommendation   : str
      notes            : list[str]
      kill_weight_up   : bool  — True when kill signal confidence is high (→ raise kill weight)
      chase_weight_up  : bool  — True when chase risk confidence is high
    """
    try:
        regime_stats = get_confidence_stats(decision.upper(), regime=regime)
        global_stats = get_confidence_stats(decision.upper())
        stats = (
            regime_stats
            if isinstance(regime_stats, dict)
            and regime_stats.get("independent_sample_size", 0) >= _MIN_TRUST_DAYS
            else global_stats
        )
    except Exception:
        return _no_override()

    if not isinstance(stats, dict):
        return _no_override()

    rec    = stats.get("recommendation", "WATCH")
    conf   = stats.get("confidence_score", 50)
    sample = stats.get(
        "independent_sample_size",
        stats.get("outcome_sample_size", stats.get("sample_size", 0)),
    )
    notes  = list(stats.get("notes", []))
    if stats.get("calibration_scope") == "ALL_REGIMES" and regime:
        notes.append(f"{regime} 分層樣本不足，暫採全市場狀態校準")

    max_decision = None
    can_use      = True

    if rec == "DISABLE":
        can_use      = False
        max_decision = "WATCH"
        notes.append(f"{decision} 訊號已停用 (recommendation=DISABLE)")
    elif decision.upper() in ("BUY", "STRONG_BUY"):
        if conf < 50 and sample >= _MIN_CONFIDENCE_DAYS:
            max_decision = "WATCH"
            notes.append(f"BUY 信心分數 {conf} < 50，降級為 WATCH")
        elif decision.upper() == "STRONG_BUY" and sample < _MIN_KELLY_DAYS:
            max_decision = "BUY"
            notes.append(
                f"STRONG_BUY 獨立交易日樣本 {sample} < {_MIN_KELLY_DAYS}，降級為 BUY"
            )

    kill_stats  = get_confidence_stats("KILL_SIGNAL")
    chase_stats = get_confidence_stats("CHASE_RISK_HIGH")

    kill_weight_up  = (
        isinstance(kill_stats, dict)
        and (kill_stats.get("win_rate_3d") or 0) >= 60
        and kill_stats.get("independent_sample_size", 0) >= _MIN_TRUST_DAYS
    )
    chase_weight_up = (
        isinstance(chase_stats, dict)
        and (chase_stats.get("win_rate_3d") or 0) >= 60
        and chase_stats.get("independent_sample_size", 0) >= _MIN_TRUST_DAYS
    )

    return {
        "can_use":          can_use,
        "max_decision":     max_decision,
        "confidence_score": conf,
        "recommendation":   rec,
        "notes":            notes,
        "kill_weight_up":   kill_weight_up,
        "chase_weight_up":  chase_weight_up,
        "sample_size":      sample,
        "evaluated_size":   stats.get("evaluated_size", 0),
        "independent_sample_size": stats.get("independent_sample_size", 0),
        "raw_evaluated_size": stats.get("raw_evaluated_size", 0),
        "win_rate_1d":      stats.get("win_rate_1d"),
        "win_rate_3d":      stats.get("win_rate_3d"),
        "win_rate_5d":      stats.get("win_rate_5d"),
        "avg_return_5d":    stats.get("avg_return_5d"),
        "avg_relative_return_5d": stats.get("avg_relative_return_5d"),
        "false_signal_rate": stats.get("false_signal_rate"),
        "stop_loss_rate":   stats.get("stop_loss_rate"),
        "probability_5d_pct": stats.get("probability_5d_pct"),
        "probability_sample_size": stats.get("probability_sample_size", 0),
        "credible_interval_95": stats.get("credible_interval_95"),
        "brier_score": stats.get("brier_score"),
        "calibration_gap_pct": stats.get("calibration_gap_pct"),
        "kelly_eligible": stats.get("kelly_eligible", False),
        "kelly_win_rate_lower_bound": stats.get("kelly_win_rate_lower_bound"),
        "kelly_gate": stats.get("kelly_gate", {}),
        "calibration_scope": stats.get("calibration_scope", "ALL_REGIMES"),
        "outcome_model": "NEXT_OPEN_COST_ADJUSTED_V1",
    }


# ── Internal stats computation ────────────────────────────────────────────────

def _compute_stats(signal_type: str, rows: list[dict]) -> dict:
    notes: list[str] = []
    sample_size = len(rows)

    if sample_size == 0:
        return _empty_stats(signal_type)

    with_1d = [r for r in rows if r.get("return_1d") is not None]
    with_3d = [r for r in rows if r.get("return_3d") is not None]
    with_5d = [r for r in rows if r.get("return_5d") is not None]
    with_paper = [r for r in rows if r.get("paper_return_pct") is not None]
    evaluated_rows = [
        r for r in rows
        if r.get("return_5d") is not None or r.get("paper_return_pct") is not None
    ]
    raw_evaluated = len(evaluated_rows)
    evaluated = _independent_count(evaluated_rows)

    # Win rate: signal-type specific definition of "correct"
    def _correct(r, days: int) -> bool | None:
        gross = r.get(f"return_{days}d")
        net = r.get(f"net_return_{days}d")
        if gross is None and net is None:
            return None
        st = signal_type
        if net is not None and st in (
            "BUY", "STRONG_BUY", "SELL", "TRIM", "AVOID",
            "KILL_SIGNAL", "CHASE_RISK_HIGH",
        ):
            return net > 0
        if st in ("BUY", "STRONG_BUY"):
            return gross > 0
        if st in ("SELL", "TRIM", "AVOID"):
            return gross < 0
        if st == "KILL_SIGNAL":
            r3 = r.get("return_3d")
            return (r3 < 0) if r3 is not None else None
        if st == "CHASE_RISK_HIGH":
            r3 = r.get("return_3d")
            return (r3 < 0) if r3 is not None else None
        return None  # WATCH / HOLD — no binary correct/incorrect

    def _win_rate(source_rows: list[dict], days: int) -> float | None:
        judged = [r for r in source_rows if _correct(r, days) is not None]
        if not judged:
            return None
        return round(sum(1 for r in judged if _correct(r, days) is True) / len(judged) * 100, 1)

    wr1 = _win_rate(rows, 1)
    wr3 = _win_rate(rows, 3)
    wr5 = _win_rate(rows, 5)

    judged_5d = [r for r in rows if _correct(r, 5) is not None]
    daily_outcomes = _daily_outcomes(judged_5d, lambda row: _correct(row, 5))
    wins_5d = sum(daily_outcomes)
    posterior = _beta_posterior(wins_5d, len(daily_outcomes))

    brier_rows = _daily_brier_rows(rows, lambda row: _correct(row, 5))
    brier_score = (
        round(sum((prob - outcome) ** 2 for prob, outcome in brier_rows) / len(brier_rows), 4)
        if brier_rows else None
    )
    calibration_gap = (
        round(abs(
            sum(prob for prob, _ in brier_rows) / len(brier_rows)
            - sum(outcome for _, outcome in brier_rows) / len(brier_rows)
        ) * 100, 2)
        if brier_rows else None
    )

    def _paper_correct(r) -> bool | None:
        ret = r.get("paper_return_pct")
        if ret is None:
            return None
        if signal_type in ("BUY", "STRONG_BUY", "HOLD"):
            return ret > 0
        if signal_type in ("SELL", "TRIM", "AVOID", "KILL_SIGNAL", "CHASE_RISK_HIGH"):
            return ret < 0
        return None

    paper_judged = [r for r in with_paper if _paper_correct(r) is not None]
    paper_win_rate = (
        round(sum(1 for r in paper_judged if _paper_correct(r)) / len(paper_judged) * 100, 1)
        if paper_judged else None
    )

    avg_r1   = _avg(r.get("return_1d")          for r in with_1d)
    avg_r3   = _avg(r.get("return_3d")          for r in with_3d)
    avg_r5   = _avg(r.get("return_5d")          for r in with_5d)
    avg_net5 = _avg(r.get("net_return_5d")      for r in with_5d)
    avg_rel5 = _avg(r.get("relative_return_5d") for r in with_5d)
    avg_paper_return = _avg(r.get("paper_return_pct") for r in with_paper)
    avg_paper_r = _avg(r.get("paper_r_multiple") for r in with_paper)

    # False signal rate (from was_correct field)
    judged_rows   = [r for r in rows if r.get("was_correct") is not None]
    false_signals = [r for r in judged_rows if r.get("was_correct") == 0]
    false_rate    = round(len(false_signals) / len(judged_rows) * 100, 1) if judged_rows else 0.0

    # Consecutive false signals (most recent)
    consec_false = 0
    for r in rows:
        if r.get("was_correct") is None:
            continue
        if r.get("was_correct") == 0:
            consec_false += 1
        else:
            break

    stop_rate = round(
        sum(1 for r in rows if r.get("hit_stop_loss")) / sample_size * 100, 1
    )

    # ── Confidence score ──────────────────────────────────────────────────────
    score = 50

    # The fixed 5-day target is the primary calibration objective.  A paper
    # trade can have a different exit horizon, so it is only a fallback when
    # no fixed-horizon outcomes exist; it must never replace them after one
    # convenient paper win.
    calibrated_win_rate = wr5 if wr5 is not None else paper_win_rate
    if calibrated_win_rate is not None:
        if calibrated_win_rate >= 65:   score += 20
        elif calibrated_win_rate >= 55: score += 10
        elif calibrated_win_rate < 35:  score -= 25
        elif calibrated_win_rate < 45:  score -= 15

    if avg_rel5 is not None:
        if avg_rel5 > 1:   score += 10
        elif avg_rel5 < 0: score -= 10

    if stop_rate > 40:  score -= 10
    elif stop_rate > 20: score -= 5

    score -= min(consec_false * 5, 25)
    score  = max(0, min(100, round(score)))

    # ── Hard cap by sample size ───────────────────────────────────────────────
    if evaluated < _MIN_CONFIDENCE_DAYS:
        score = min(score, 40)
        notes.append(
            f"獨立交易日樣本 < {_MIN_CONFIDENCE_DAYS}，信心分數上限 40"
        )
    elif evaluated < _MIN_TRUST_DAYS:
        score = min(score, 60)
        notes.append(
            f"獨立交易日樣本 {evaluated} < {_MIN_TRUST_DAYS}，信心分數上限 60"
        )
    if daily_outcomes:
        low, high = posterior["credible_interval_95"]
        notes.append(
            f"成本後 5 日成功機率 {posterior['probability_pct']:.1f}%（95% 區間 {low:.1f}–{high:.1f}%）"
        )

    # ── Recommendation ────────────────────────────────────────────────────────
    if evaluated < _MIN_CONFIDENCE_DAYS:
        rec = "WATCH"
        notes.append(
            f"獨立交易日樣本不足 {_MIN_CONFIDENCE_DAYS}，暫不可信"
        )
    elif consec_false >= 5 and evaluated >= _MIN_TRUST_DAYS:
        rec = "DISABLE"
        notes.append(f"連續 {consec_false} 次錯誤訊號，建議停用")
    elif score < 35 or (
        evaluated >= 20
        and calibrated_win_rate is not None
        and calibrated_win_rate < 40
    ):
        rec = "DISABLE"
        notes.append("信心分數過低或勝率過差，建議停用")
    elif signal_type in ("BUY", "STRONG_BUY"):
        if calibrated_win_rate is not None and calibrated_win_rate < 50 and evaluated >= 20:
            rec = "REDUCE_WEIGHT"
            notes.append(f"BUY 校準勝率 {calibrated_win_rate:.1f}% < 50%，降低倉位權重")
        elif avg_rel5 is not None and avg_rel5 < 0 and evaluated >= 20:
            rec = "REDUCE_WEIGHT"
            notes.append(f"BUY 平均相對報酬 {avg_rel5:.2f}%，跑輸大盤，降低倉位")
        elif score >= 60 and evaluated >= _MIN_TRUST_DAYS:
            rec = "TRUST"
        else:
            rec = "WATCH"
    elif score >= 60 and evaluated >= _MIN_TRUST_DAYS:
        rec = "TRUST"
    elif score < 50:
        rec = "WATCH"
    else:
        rec = "WATCH"

    # ── Special signal notes ──────────────────────────────────────────────────
    if signal_type == "KILL_SIGNAL" and wr3 is not None:
        if wr3 >= 60:
            notes.append(f"Kill Signal 3日準確率 {wr3:.1f}%，可提升觸發權重")
        else:
            notes.append(f"Kill Signal 3日準確率 {wr3:.1f}%，效果一般")

    if signal_type == "CHASE_RISK_HIGH" and wr3 is not None:
        if wr3 >= 60:
            notes.append(f"Chase Risk 3日有效率 {wr3:.1f}%，追高風險確認有效")

    if signal_type in ("SELL", "TRIM") and with_5d:
        misfires = [r for r in with_5d if (r.get("return_5d") or 0) > 10]
        if misfires:
            mrate = round(len(misfires) / len(with_5d) * 100, 1)
            notes.append(f"賣出後大漲 >10% 案例佔 {mrate:.0f}%，部分可能誤殺")

    credible_low = posterior["credible_interval_95"][0]
    kelly_gate = {
        "minimum_independent_days": _MIN_KELLY_DAYS,
        "independent_days": len(daily_outcomes),
        "credible_lower_bound_above_50": credible_low > 50,
        "brier_at_most": _MAX_KELLY_BRIER,
        "brier_pass": brier_score is not None and brier_score <= _MAX_KELLY_BRIER,
        "calibration_gap_at_most_pct": _MAX_KELLY_GAP_PCT,
        "calibration_gap_pass": (
            calibration_gap is not None and calibration_gap <= _MAX_KELLY_GAP_PCT
        ),
        "positive_cost_adjusted_return": avg_net5 is not None and avg_net5 > 0,
    }
    kelly_eligible = bool(
        len(daily_outcomes) >= _MIN_KELLY_DAYS
        and kelly_gate["credible_lower_bound_above_50"]
        and kelly_gate["brier_pass"]
        and kelly_gate["calibration_gap_pass"]
        and kelly_gate["positive_cost_adjusted_return"]
    )
    if signal_type in ("BUY", "STRONG_BUY") and not kelly_eligible:
        notes.append("Kelly 未通過：僅採固定風險／波動度部位，單檔上限 3%")

    return {
        "signal_type":             signal_type,
        "sample_size":             sample_size,
        "outcome_sample_size":     evaluated,
        "evaluated_size":          evaluated,
        "independent_sample_size": evaluated,
        "raw_evaluated_size":      raw_evaluated,
        "win_rate_1d":             wr1,
        "win_rate_3d":             wr3,
        "win_rate_5d":             wr5,
        "avg_return_1d":           avg_r1,
        "avg_return_3d":           avg_r3,
        "avg_return_5d":           avg_r5,
        "avg_net_return_5d":       avg_net5,
        "avg_relative_return_5d":  avg_rel5,
        "paper_win_rate":          paper_win_rate,
        "avg_paper_return":        avg_paper_return,
        "avg_paper_r_multiple":    avg_paper_r,
        "false_signal_rate":       false_rate,
        "stop_loss_rate":          stop_rate,
        "consecutive_false":       consec_false,
        "confidence_score":        score,
        "recommendation":          rec,
        "probability_5d_pct":      posterior["probability_pct"],
        "probability_sample_size": len(daily_outcomes),
        "credible_interval_95":    posterior["credible_interval_95"],
        "bayesian_prior":          "Beta(1,1)",
        "brier_score":             brier_score,
        "calibration_gap_pct":     calibration_gap,
        "calibration_sample_size": len(brier_rows),
        "kelly_eligible":          kelly_eligible,
        "kelly_win_rate_lower_bound": round(credible_low / 100, 4),
        "kelly_gate":              kelly_gate,
        "outcome_model":           "NEXT_OPEN_COST_ADJUSTED_V1",
        "notes":                   notes,
    }


# ── Helpers ───────────────────────────────────────────────────────────────────

def _independent_key(row: dict, index: int) -> str:
    signal_day = str(row.get("signal_date") or "").strip()[:10]
    if signal_day:
        return f"day:{signal_day}"
    row_id = row.get("id")
    return f"row:{row_id}" if row_id is not None else f"index:{index}"


def _independent_count(rows: list[dict]) -> int:
    return len({_independent_key(row, index) for index, row in enumerate(rows)})


def _daily_outcomes(rows: list[dict], outcome_fn) -> list[float]:
    """Cluster correlated same-day signals into one fractional Bernoulli trial."""
    grouped: dict[str, list[float]] = {}
    for index, row in enumerate(rows):
        outcome = outcome_fn(row)
        if outcome is None:
            continue
        grouped.setdefault(_independent_key(row, index), []).append(
            1.0 if outcome else 0.0
        )
    return [sum(values) / len(values) for values in grouped.values() if values]


def _daily_brier_rows(rows: list[dict], outcome_fn) -> list[tuple[float, float]]:
    grouped: dict[str, list[tuple[float, float]]] = {}
    for index, row in enumerate(rows):
        probability = _normalized_probability(row.get("prediction_probability"))
        outcome = outcome_fn(row)
        if probability is None or outcome is None:
            continue
        grouped.setdefault(_independent_key(row, index), []).append(
            (probability, 1.0 if outcome else 0.0)
        )
    return [
        (
            sum(probability for probability, _ in values) / len(values),
            sum(outcome for _, outcome in values) / len(values),
        )
        for values in grouped.values()
        if values
    ]

def _pct_change(base, price) -> float | None:
    try:
        b, p = float(base), float(price)
        if b > 0:
            return round((p - b) / b * 100, 4)
    except (TypeError, ValueError):
        pass
    return None


def _sub(a, b) -> float | None:
    if a is not None and b is not None:
        return round(a - b, 4)
    return None


def _avg(gen) -> float | None:
    vals = [v for v in gen if v is not None and not (isinstance(v, float) and math.isnan(v))]
    return round(sum(vals) / len(vals), 4) if vals else None


def _determine_was_correct(decision, r1d, r3d, r5d, rel5d, hit_stop) -> int | None:
    d = decision.upper()
    if d in ("BUY", "STRONG_BUY"):
        if r5d is not None:
            return 1 if (r5d > 0 and (rel5d is None or rel5d >= 0)) else 0
    elif d in ("SELL", "TRIM", "AVOID"):
        if r5d is not None:
            return 1 if r5d > 0 else 0
    elif d in ("KILL_SIGNAL",):
        if r3d is not None:
            return 1 if r3d > 0 else 0
    elif d == "CHASE_RISK_HIGH":
        if r3d is not None:
            return 1 if r3d > 0 else 0
    return None


def _determine_false_reason(decision, was_correct, r1d, r3d, r5d, rel5d) -> str | None:
    if was_correct != 0:
        return None
    d = decision.upper()
    if d in ("BUY", "STRONG_BUY"):
        if r5d is not None and r5d < -10:
            return "買進後大跌 >10%（重大誤判）"
        if r5d is not None and r5d < 0:
            return "買進後下跌（訊號失效）"
        if rel5d is not None and rel5d < -5:
            return "跑輸大盤 >5%（相對表現差）"
    elif d in ("SELL", "TRIM"):
        if r5d is not None and r5d > 10:
            return "賣出後大漲 >10%（可能誤殺）"
        if r5d is not None and r5d > 0:
            return "賣出後繼續上漲（過早出場）"
    return "結果不符預期"


def _empty_stats(signal_type: str) -> dict:
    return {
        "signal_type":             signal_type,
        "sample_size":             0,
        "outcome_sample_size":     0,
        "evaluated_size":          0,
        "independent_sample_size": 0,
        "raw_evaluated_size":      0,
        "win_rate_1d":             None,
        "win_rate_3d":             None,
        "win_rate_5d":             None,
        "avg_return_1d":           None,
        "avg_return_3d":           None,
        "avg_return_5d":           None,
        "avg_net_return_5d":       None,
        "avg_relative_return_5d":  None,
        "paper_win_rate":          None,
        "avg_paper_return":        None,
        "avg_paper_r_multiple":    None,
        "false_signal_rate":       0.0,
        "stop_loss_rate":          0.0,
        "consecutive_false":       0,
        "confidence_score":        50,
        "recommendation":          "WATCH",
        "probability_5d_pct":      50.0,
        "probability_sample_size": 0,
        "credible_interval_95":    [2.5, 97.5],
        "bayesian_prior":          "Beta(1,1)",
        "brier_score":             None,
        "calibration_gap_pct":     None,
        "calibration_sample_size": 0,
        "kelly_eligible":          False,
        "kelly_win_rate_lower_bound": 0.025,
        "kelly_gate":              {},
        "calibration_scope":       "ALL_REGIMES",
        "outcome_model":           "NEXT_OPEN_COST_ADJUSTED_V1",
        "notes":                   ["尚無歷史資料"],
    }


def _no_override() -> dict:
    return {
        "can_use":          True,
        "max_decision":     None,
        "confidence_score": 50,
        "recommendation":   "WATCH",
        "notes":            [],
        "kill_weight_up":   False,
        "chase_weight_up":  False,
        "sample_size":      0,
        "evaluated_size":   0,
        "independent_sample_size": 0,
        "raw_evaluated_size": 0,
        "win_rate_1d":      None,
        "win_rate_3d":      None,
        "win_rate_5d":      None,
        "avg_return_5d":    None,
        "avg_relative_return_5d": None,
        "false_signal_rate": 0.0,
        "stop_loss_rate":   0.0,
        "probability_5d_pct": 50.0,
        "probability_sample_size": 0,
        "credible_interval_95": [2.5, 97.5],
        "brier_score": None,
        "calibration_gap_pct": None,
        "kelly_eligible": False,
        "kelly_win_rate_lower_bound": 0.025,
        "kelly_gate": {},
        "calibration_scope": "ALL_REGIMES",
        "outcome_model": "NEXT_OPEN_COST_ADJUSTED_V1",
    }


def _normalized_probability(value) -> float | None:
    try:
        result = float(value)
        if result > 1:
            result /= 100
        if 0 <= result <= 1 and math.isfinite(result):
            return round(result, 6)
    except (TypeError, ValueError, OverflowError):
        pass
    return None


def _betacf(a: float, b: float, x: float) -> float:
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    d = 1.0 - qab * x / qap
    if abs(d) < 3e-14:
        d = 3e-14
    d = 1.0 / d
    h = d
    for m in range(1, 201):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < 3e-14:
            d = 3e-14
        c = 1.0 + aa / c
        if abs(c) < 3e-14:
            c = 3e-14
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < 3e-14:
            d = 3e-14
        c = 1.0 + aa / c
        if abs(c) < 3e-14:
            c = 3e-14
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 3e-10:
            break
    return h


def _regularized_beta(x: float, a: float, b: float) -> float:
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    front = math.exp(
        math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
        + a * math.log(x) + b * math.log1p(-x)
    )
    if x < (a + 1) / (a + b + 2):
        return front * _betacf(a, b, x) / a
    return 1 - front * _betacf(b, a, 1 - x) / b


def _beta_quantile(probability: float, a: float, b: float) -> float:
    low, high = 0.0, 1.0
    for _ in range(70):
        middle = (low + high) / 2
        if _regularized_beta(middle, a, b) < probability:
            low = middle
        else:
            high = middle
    return (low + high) / 2


def _beta_posterior(wins: float, total: int) -> dict:
    total = max(0, int(total))
    wins = max(0.0, min(float(wins), float(total)))
    alpha, beta = 1 + wins, 1 + total - wins
    mean = alpha / (alpha + beta)
    return {
        "probability_pct": round(mean * 100, 2),
        "credible_interval_95": [
            round(_beta_quantile(0.025, alpha, beta) * 100, 2),
            round(_beta_quantile(0.975, alpha, beta) * 100, 2),
        ],
        "wins": wins,
        "trials": total,
    }


def _dated_bars(ohlcv: dict) -> list[dict]:
    timestamps = ohlcv.get("timestamps") or []
    dates = ohlcv.get("dates") or []
    closes = ohlcv.get("closes") or []
    opens = ohlcv.get("opens") or []
    highs = ohlcv.get("highs") or []
    lows = ohlcv.get("lows") or []
    by_day: dict[date, dict] = {}
    for index, raw_close in enumerate(closes):
        close = _positive_float(raw_close)
        day = _parse_day(dates[index]) if index < len(dates) else None
        if day is None and index < len(timestamps):
            day = _timestamp_day(timestamps[index])
        if day is None or close is None:
            continue
        open_price = _positive_float(opens[index]) if index < len(opens) else close
        high = _positive_float(highs[index]) if index < len(highs) else close
        low = _positive_float(lows[index]) if index < len(lows) else close
        by_day[day] = {
            "date": day,
            "open": open_price or close,
            "high": max(high or close, open_price or close, close),
            "low": min(low or close, open_price or close, close),
            "close": close,
        }
    return [by_day[day] for day in sorted(by_day)]


def _dated_closes(ohlcv: dict) -> list[tuple[date, float]]:
    """Backward-compatible close view used by older callers/tests."""
    return [(bar["date"], bar["close"]) for bar in _dated_bars(ohlcv)]


def _timestamp_day(value) -> date | None:
    try:
        if isinstance(value, (int, float)) or str(value).strip().replace(".", "", 1).isdigit():
            timestamp = float(value)
            if timestamp <= 0:
                return None
            return datetime.fromtimestamp(timestamp, tz=timezone.utc).date()
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()
    except (TypeError, ValueError, OSError):
        return None


def _parse_day(value) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _anchor_index(bars: list[tuple[date, float]], signal_day: date) -> int | None:
    anchor = None
    for index, (bar_day, _) in enumerate(bars):
        if bar_day <= signal_day:
            anchor = index
        else:
            break
    return anchor


def _forward_return(bars: list[tuple[date, float]], signal_day: date, horizon: int) -> float | None:
    anchor = _anchor_index(bars, signal_day)
    if anchor is None or anchor + horizon >= len(bars):
        return None
    return _pct_change(bars[anchor][1], bars[anchor + horizon][1])


def _next_bar_index(bars: list[dict], signal_day: date) -> int | None:
    for index, bar in enumerate(bars):
        if bar["date"] > signal_day:
            return index
    return None


def _forward_return_from_next_open(
    bars: list[dict], signal_day: date, horizon: int
) -> float | None:
    entry_idx = _next_bar_index(bars, signal_day)
    if entry_idx is None:
        return None
    exit_idx = entry_idx + int(horizon) - 1
    if exit_idx >= len(bars):
        return None
    return _pct_change(bars[entry_idx]["open"], bars[exit_idx]["close"])


def _round_trip_cost_pct(symbol: str) -> float:
    try:
        from trade_cost import default_params

        params = default_params(symbol)
        result = (
            float(params.get("slippage_pct", 0.001)) * 2
            + float(params.get("commission_buy", 0))
            + float(params.get("commission_sell", 0))
            + float(params.get("transaction_tax", 0))
        ) * 100
        return round(max(0.0, result), 4)
    except Exception:
        return 0.2


def _modeled_entry_fill(raw_open: float, decision: str, symbol: str) -> float:
    try:
        from trade_cost import default_params

        params = default_params(symbol)
        slip = float(params.get("slippage_pct", 0.001))
        direction = -1 if str(decision).upper() in {
            "SELL", "TRIM", "AVOID", "KILL_SIGNAL", "CHASE_RISK_HIGH"
        } else 1
        commission = float(
            params.get("commission_sell" if direction < 0 else "commission_buy", 0)
        )
        fill = raw_open * (1 + direction * (slip + commission))
        return round(fill, 6)
    except Exception:
        return round(raw_open, 6)


def _directional_net_return(
    decision: str, gross_return: float | None, round_trip_cost_pct: float
) -> float | None:
    if gross_return is None:
        return None
    value = str(decision or "").upper()
    if value in {"BUY", "STRONG_BUY"}:
        direction = 1
    elif value in {"SELL", "TRIM", "AVOID", "KILL_SIGNAL", "CHASE_RISK_HIGH"}:
        direction = -1
    else:
        return None
    try:
        return round(direction * float(gross_return) - float(round_trip_cost_pct or 0), 4)
    except (TypeError, ValueError):
        return None


def _directional_excursions(
    bars: list[dict], entry: float, decision: str
) -> tuple[float | None, float | None]:
    if not bars or entry <= 0:
        return None, None
    highs = [bar["high"] for bar in bars if bar.get("high")]
    lows = [bar["low"] for bar in bars if bar.get("low")]
    if not highs or not lows:
        return None, None
    bearish = str(decision or "").upper() in {
        "SELL", "TRIM", "AVOID", "KILL_SIGNAL", "CHASE_RISK_HIGH"
    }
    if bearish:
        favorable = (entry - min(lows)) / entry * 100
        adverse = (entry - max(highs)) / entry * 100
    else:
        favorable = (max(highs) - entry) / entry * 100
        adverse = (min(lows) - entry) / entry * 100
    return round(favorable, 4), round(adverse, 4)


def _positive_float(value) -> float | None:
    try:
        result = float(value)
        if result > 0 and math.isfinite(result):
            return result
    except (TypeError, ValueError):
        pass
    return None
