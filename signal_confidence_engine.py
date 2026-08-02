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
    price_1d                REAL,
    price_3d                REAL,
    price_5d                REAL,
    return_1d               REAL,
    return_3d               REAL,
    return_5d               REAL,
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
    is_demo                 INTEGER DEFAULT 0,
    created_at              TEXT    NOT NULL,
    updated_at              TEXT
);
CREATE INDEX IF NOT EXISTS sh_decision ON signal_history(decision);
CREATE INDEX IF NOT EXISTS sh_symbol   ON signal_history(symbol);
CREATE INDEX IF NOT EXISTS sh_date     ON signal_history(signal_date);
"""


# ── DB init ───────────────────────────────────────────────────────────────────

def init_db(db_path: str = _DB_PATH) -> None:
    global _DB_PATH
    _DB_PATH = db_path
    try:
        with _LOCK, sqlite3.connect(_DB_PATH) as conn:
            conn.executescript(_DDL)
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

    row = (
        sym, signal_date, decision,
        payload.get("top_tier_score"),
        payload.get("market_regime"),
        payload.get("market_score"),
        payload.get("data_quality_status"),
        payload.get("chase_risk_score"),
        payload.get("sell_signal"),
        int(bool(payload.get("kill_signal_triggered", False))),
        payload.get("sector_leadership"),
        payload.get("position_size_level"),
        payload.get("entry_price"),
        None, None, None,   # price_1d/3d/5d
        None, None, None,   # return_1d/3d/5d
        None, None, None,   # benchmark_return_1d/3d/5d
        None, None, None,   # relative_return_1d/3d/5d
        None, None,         # MFE, MAE
        int(bool(payload.get("hit_stop_loss", False))),
        None,               # was_correct
        None,               # false_signal_reason
        int(bool(payload.get("is_demo", False))),
        now_iso,
        None,               # updated_at
    )

    try:
        with _LOCK, sqlite3.connect(_DB_PATH) as conn:
            cur = conn.execute("""
                INSERT INTO signal_history (
                    symbol, signal_date, decision,
                    top_tier_score, market_regime, market_score, data_quality_status,
                    chase_risk_score, sell_signal, kill_signal_triggered, sector_leadership,
                    position_size_level, entry_price,
                    price_1d, price_3d, price_5d,
                    return_1d, return_3d, return_5d,
                    benchmark_return_1d, benchmark_return_3d, benchmark_return_5d,
                    relative_return_1d, relative_return_3d, relative_return_5d,
                    max_favorable_excursion, max_adverse_excursion,
                    hit_stop_loss, was_correct, false_signal_reason,
                    is_demo, created_at, updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, row)
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
                    "SELECT entry_price, decision, kill_signal_triggered FROM signal_history WHERE id=?",
                    (rec_id,),
                ).fetchone()
                if not base:
                    errors.append(f"Record id={rec_id} not found")
                    continue

                entry_price, decision, kill_trig = base
                entry = float(entry_price) if entry_price else None

                p1d = u.get("price_1d")
                p3d = u.get("price_3d")
                p5d = u.get("price_5d")

                r1d = _pct_change(entry, p1d)
                r3d = _pct_change(entry, p3d)
                r5d = _pct_change(entry, p5d)

                b1d = u.get("benchmark_return_1d")
                b3d = u.get("benchmark_return_3d")
                b5d = u.get("benchmark_return_5d")

                rel1d = _sub(r1d, b1d)
                rel3d = _sub(r3d, b3d)
                rel5d = _sub(r5d, b5d)

                hit_stop = int(bool(u.get("hit_stop_loss", False)))
                mfe = u.get("max_favorable_excursion")
                mae = u.get("max_adverse_excursion")

                was_correct = _determine_was_correct(decision, r1d, r3d, r5d, rel5d, hit_stop)
                false_reason = _determine_false_reason(decision, was_correct, r1d, r3d, r5d, rel5d)

                conn.execute("""
                    UPDATE signal_history SET
                        price_1d=?, price_3d=?, price_5d=?,
                        return_1d=?, return_3d=?, return_5d=?,
                        benchmark_return_1d=?, benchmark_return_3d=?, benchmark_return_5d=?,
                        relative_return_1d=?, relative_return_3d=?, relative_return_5d=?,
                        max_favorable_excursion=?, max_adverse_excursion=?,
                        hit_stop_loss=?, was_correct=?, false_signal_reason=?,
                        updated_at=?
                    WHERE id=?
                """, (
                    p1d, p3d, p5d,
                    r1d, r3d, r5d,
                    b1d, b3d, b5d,
                    rel1d, rel3d, rel5d,
                    mfe, mae,
                    hit_stop, was_correct, false_reason,
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

    bars = _dated_closes(ohlcv)
    if len(bars) < 2:
        return {"ok": True, "updated": 0, "skipped": "insufficient_bars"}
    benchmark_bars = _dated_closes(benchmark_ohlcv or {})

    try:
        with sqlite3.connect(_DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            pending = [dict(row) for row in conn.execute(
                """SELECT id, signal_date, entry_price
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
        anchor = _anchor_index(bars, signal_day)
        if anchor is None or anchor + 1 >= len(bars):
            continue

        entry = _positive_float(row.get("entry_price")) or bars[anchor][1]
        update: dict = {"id": row["id"]}
        for horizon in (1, 3, 5):
            idx = anchor + horizon
            if idx < len(bars):
                update[f"price_{horizon}d"] = bars[idx][1]
                benchmark_return = _forward_return(benchmark_bars, signal_day, horizon)
                if benchmark_return is not None:
                    update[f"benchmark_return_{horizon}d"] = benchmark_return

        forward = [price for _, price in bars[anchor + 1:min(len(bars), anchor + 6)]]
        if forward and entry > 0:
            update["max_favorable_excursion"] = round((max(forward) - entry) / entry * 100, 4)
            update["max_adverse_excursion"] = round((min(forward) - entry) / entry * 100, 4)
        updates.append(update)

    if not updates:
        return {"ok": True, "updated": 0, "pending": len(pending), "errors": []}
    result = update_outcomes(updates)
    result["pending"] = len(pending)
    return result


# ── Confidence statistics ─────────────────────────────────────────────────────

def get_confidence_stats(signal_type: str | None = None):
    """
    Returns:
      signal_type=None  → list[dict] for every SIGNAL_TYPE
      signal_type=str   → single dict
    """
    try:
        with sqlite3.connect(_DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            if signal_type:
                stype = signal_type.upper()
                rows = [dict(r) for r in conn.execute(
                    "SELECT * FROM signal_history WHERE decision=? AND is_demo=0 ORDER BY signal_date DESC",
                    (stype,),
                ).fetchall()]
                return _compute_stats(stype, rows)
            else:
                result = []
                for stype in SIGNAL_TYPES:
                    rows = [dict(r) for r in conn.execute(
                        "SELECT * FROM signal_history WHERE decision=? AND is_demo=0 ORDER BY signal_date DESC",
                        (stype,),
                    ).fetchall()]
                    result.append(_compute_stats(stype, rows))
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
        stats = get_confidence_stats(decision.upper())
    except Exception:
        return _no_override()

    if not isinstance(stats, dict):
        return _no_override()

    rec    = stats.get("recommendation", "WATCH")
    conf   = stats.get("confidence_score", 50)
    sample = stats.get("sample_size", 0)
    notes  = list(stats.get("notes", []))

    max_decision = None
    can_use      = True

    if rec == "DISABLE":
        can_use      = False
        max_decision = "WATCH"
        notes.append(f"{decision} 訊號已停用 (recommendation=DISABLE)")
    elif decision.upper() in ("BUY", "STRONG_BUY"):
        if conf < 50 and sample >= 5:
            max_decision = "WATCH"
            notes.append(f"BUY 信心分數 {conf} < 50，降級為 WATCH")
        elif decision.upper() == "STRONG_BUY" and sample < 20:
            max_decision = "BUY"
            notes.append(f"STRONG_BUY 樣本數 {sample} < 20，降級為 BUY")

    kill_stats  = get_confidence_stats("KILL_SIGNAL")
    chase_stats = get_confidence_stats("CHASE_RISK_HIGH")

    kill_weight_up  = (
        isinstance(kill_stats, dict)
        and (kill_stats.get("win_rate_3d") or 0) >= 60
        and kill_stats.get("sample_size", 0) >= 5
    )
    chase_weight_up = (
        isinstance(chase_stats, dict)
        and (chase_stats.get("win_rate_3d") or 0) >= 60
        and chase_stats.get("sample_size", 0) >= 5
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
        "win_rate_1d":      stats.get("win_rate_1d"),
        "win_rate_3d":      stats.get("win_rate_3d"),
        "win_rate_5d":      stats.get("win_rate_5d"),
        "avg_return_5d":    stats.get("avg_return_5d"),
        "avg_relative_return_5d": stats.get("avg_relative_return_5d"),
        "false_signal_rate": stats.get("false_signal_rate"),
        "stop_loss_rate":   stats.get("stop_loss_rate"),
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
    evaluated = len(with_5d)

    # Win rate: signal-type specific definition of "correct"
    def _correct(r, days: int) -> bool | None:
        ret = r.get(f"return_{days}d")
        if ret is None:
            return None
        st = signal_type
        if st in ("BUY", "STRONG_BUY"):
            return ret > 0
        if st in ("SELL", "TRIM", "AVOID"):
            return ret < 0
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

    avg_r1   = _avg(r.get("return_1d")          for r in with_1d)
    avg_r3   = _avg(r.get("return_3d")          for r in with_3d)
    avg_r5   = _avg(r.get("return_5d")          for r in with_5d)
    avg_rel5 = _avg(r.get("relative_return_5d") for r in with_5d)

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

    if wr5 is not None:
        if wr5 >= 65:   score += 20
        elif wr5 >= 55: score += 10
        elif wr5 < 35:  score -= 25
        elif wr5 < 45:  score -= 15

    if avg_rel5 is not None:
        if avg_rel5 > 1:   score += 10
        elif avg_rel5 < 0: score -= 10

    if stop_rate > 40:  score -= 10
    elif stop_rate > 20: score -= 5

    score -= min(consec_false * 5, 25)
    score  = max(0, min(100, round(score)))

    # ── Hard cap by sample size ───────────────────────────────────────────────
    if sample_size < 5:
        score = min(score, 40)
        notes.append("樣本數 < 5，信心分數上限 40")
    elif sample_size < 20:
        score = min(score, 60)
        notes.append(f"樣本數 {sample_size} < 20，信心分數上限 60")

    # ── Recommendation ────────────────────────────────────────────────────────
    if sample_size < 5:
        rec = "WATCH"
        notes.append("樣本數不足 5，暫不可信")
    elif consec_false >= 5 and sample_size >= 20:
        rec = "DISABLE"
        notes.append(f"連續 {consec_false} 次錯誤訊號，建議停用")
    elif score < 35 or (sample_size >= 10 and wr5 is not None and wr5 < 40):
        rec = "DISABLE"
        notes.append("信心分數過低或勝率過差，建議停用")
    elif signal_type in ("BUY", "STRONG_BUY"):
        if wr5 is not None and wr5 < 50 and sample_size >= 10:
            rec = "REDUCE_WEIGHT"
            notes.append(f"BUY 5日勝率 {wr5:.1f}% < 50%，降低倉位權重")
        elif avg_rel5 is not None and avg_rel5 < 0 and sample_size >= 10:
            rec = "REDUCE_WEIGHT"
            notes.append(f"BUY 平均相對報酬 {avg_rel5:.2f}%，跑輸大盤，降低倉位")
        elif score >= 60:
            rec = "TRUST"
        else:
            rec = "WATCH"
    elif score >= 60:
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

    return {
        "signal_type":             signal_type,
        "sample_size":             sample_size,
        "evaluated_size":          evaluated,
        "win_rate_1d":             wr1,
        "win_rate_3d":             wr3,
        "win_rate_5d":             wr5,
        "avg_return_1d":           avg_r1,
        "avg_return_3d":           avg_r3,
        "avg_return_5d":           avg_r5,
        "avg_relative_return_5d":  avg_rel5,
        "false_signal_rate":       false_rate,
        "stop_loss_rate":          stop_rate,
        "consecutive_false":       consec_false,
        "confidence_score":        score,
        "recommendation":          rec,
        "notes":                   notes,
    }


# ── Helpers ───────────────────────────────────────────────────────────────────

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
            return 1 if (r5d > 0 and (rel5d is None or rel5d >= -2)) else 0
    elif d in ("SELL", "TRIM", "AVOID"):
        if r5d is not None:
            return 1 if r5d < 0 else 0
    elif d in ("KILL_SIGNAL",):
        if r3d is not None:
            return 1 if r3d < 0 else 0
    elif d == "CHASE_RISK_HIGH":
        if r3d is not None:
            return 1 if r3d < 0 else 0
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
        "evaluated_size":          0,
        "win_rate_1d":             None,
        "win_rate_3d":             None,
        "win_rate_5d":             None,
        "avg_return_1d":           None,
        "avg_return_3d":           None,
        "avg_return_5d":           None,
        "avg_relative_return_5d":  None,
        "false_signal_rate":       0.0,
        "stop_loss_rate":          0.0,
        "consecutive_false":       0,
        "confidence_score":        50,
        "recommendation":          "WATCH",
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
        "win_rate_1d":      None,
        "win_rate_3d":      None,
        "win_rate_5d":      None,
        "avg_return_5d":    None,
        "avg_relative_return_5d": None,
        "false_signal_rate": 0.0,
        "stop_loss_rate":   0.0,
    }


def _dated_closes(ohlcv: dict) -> list[tuple[date, float]]:
    timestamps = ohlcv.get("timestamps") or []
    closes = ohlcv.get("closes") or []
    by_day: dict[date, float] = {}
    for raw_ts, raw_close in zip(timestamps, closes):
        close = _positive_float(raw_close)
        day = _timestamp_day(raw_ts)
        if day is not None and close is not None:
            by_day[day] = close
    return sorted(by_day.items(), key=lambda item: item[0])


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


def _positive_float(value) -> float | None:
    try:
        result = float(value)
        if result > 0 and math.isfinite(result):
            return result
    except (TypeError, ValueError):
        pass
    return None
