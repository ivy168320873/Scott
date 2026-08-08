"""Persistent paper-trading journal that closes the signal-to-outcome loop."""

from __future__ import annotations

import math
import re
import uuid
from datetime import datetime, timezone
from typing import Callable

from .storage import connect, dumps, loads, utc_now


_SYMBOL_RE = re.compile(r"[A-Z0-9.\-]{1,20}")
_EXIT_REASONS = {"MANUAL", "STOP", "TARGET", "TIME_EXIT", "SIGNAL_EXIT", "RISK_EXIT"}


def init_db(db_path: str | None = None) -> None:
    with connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS paper_trades_v2 (
                id TEXT PRIMARY KEY,
                client_order_id TEXT UNIQUE,
                signal_record_id INTEGER,
                scorecard_id TEXT,
                symbol TEXT NOT NULL,
                status TEXT NOT NULL,
                decision TEXT NOT NULL,
                confidence_score REAL NOT NULL,
                risk_profile TEXT NOT NULL,
                evidence_coverage REAL NOT NULL,
                is_demo INTEGER NOT NULL DEFAULT 0,
                entry_price REAL NOT NULL,
                stop_price REAL NOT NULL,
                target_price REAL NOT NULL,
                qty INTEGER NOT NULL,
                opened_at TEXT NOT NULL,
                current_price REAL NOT NULL,
                unrealized_pnl REAL NOT NULL DEFAULT 0,
                exit_price REAL,
                exit_reason TEXT,
                closed_at TEXT,
                realized_pnl REAL,
                return_pct REAL,
                r_multiple REAL,
                meta_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS pt_status ON paper_trades_v2(status, opened_at);
            CREATE INDEX IF NOT EXISTS pt_symbol ON paper_trades_v2(symbol, status);

            CREATE TABLE IF NOT EXISTS paper_trade_events_v2 (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                price REAL,
                payload_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                FOREIGN KEY(trade_id) REFERENCES paper_trades_v2(id)
            );
            """
        )


def _finite(value, field: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{field} 必須是數字") from exc
    if not math.isfinite(number):
        raise ValueError(f"{field} 必須是有限數字")
    return number


def _trade_dict(row) -> dict:
    result = dict(row)
    result["is_demo"] = bool(result.get("is_demo"))
    result["meta"] = loads(result.pop("meta_json", None), {})
    return result


def _event(conn, trade_id: str, event_type: str, price: float | None, payload=None) -> None:
    conn.execute(
        """
        INSERT INTO paper_trade_events_v2(trade_id,event_type,price,payload_json,created_at)
        VALUES(?,?,?,?,?)
        """,
        (trade_id, event_type, price, dumps(payload or {}), utc_now()),
    )


def open_trade(
    payload: dict,
    risk_gate: dict,
    *,
    db_path: str | None = None,
) -> dict:
    if not isinstance(payload, dict) or not isinstance(risk_gate, dict):
        raise ValueError("payload 與 risk_gate 必須是 JSON object")
    if not risk_gate.get("allowed"):
        blockers = risk_gate.get("blockers") or ["個人風險大腦未通過"]
        raise ValueError("交易被風控阻擋：" + "；".join(str(v) for v in blockers[:5]))
    symbol = str(payload.get("symbol") or "").upper().strip()
    if not _SYMBOL_RE.fullmatch(symbol):
        raise ValueError("股票代號無效")
    decision = str(payload.get("decision") or "BUY").upper().strip()
    if decision not in {"BUY", "STRONG_BUY"}:
        raise ValueError("只有 BUY / STRONG_BUY 可建立模擬交易")
    entry = _finite(payload.get("entry_price"), "entry_price")
    stop = _finite(payload.get("stop_price"), "stop_price")
    target = _finite(payload.get("target_price"), "target_price")
    if not 0 < stop < entry < target:
        raise ValueError("價格必須符合 0 < stop < entry < target")
    try:
        qty = int(payload.get("qty") or risk_gate.get("max_shares") or 0)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("qty 必須是整數") from exc
    max_shares = risk_gate.get("max_shares")
    if max_shares is not None:
        qty = min(qty, int(max_shares))
    if qty <= 0 or qty > 1_000_000_000:
        raise ValueError("風控後 qty 必須至少為 1")

    confidence = _finite(payload.get("confidence_score", 0), "confidence_score")
    if not 0 <= confidence <= 100:
        raise ValueError("confidence_score 必須介於 0 與 100")
    evidence_coverage = _finite(payload.get("evidence_coverage", 0), "evidence_coverage")
    if not 0 <= evidence_coverage <= 1:
        raise ValueError("evidence_coverage 必須介於 0 與 1")
    client_order_id = str(payload.get("client_order_id") or "").strip()[:120] or None
    now = utc_now()
    trade_id = uuid.uuid4().hex
    meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
    init_db(db_path)
    with connect(db_path) as conn:
        if client_order_id:
            existing = conn.execute(
                "SELECT * FROM paper_trades_v2 WHERE client_order_id=?", (client_order_id,)
            ).fetchone()
            if existing:
                result = _trade_dict(existing)
                result["deduplicated"] = True
                return result
        conn.execute(
            """
            INSERT INTO paper_trades_v2(
                id,client_order_id,signal_record_id,scorecard_id,symbol,status,
                decision,confidence_score,risk_profile,evidence_coverage,is_demo,
                entry_price,stop_price,target_price,qty,opened_at,current_price,
                unrealized_pnl,meta_json,created_at,updated_at
            ) VALUES(?,?,?,?,?,'OPEN',?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                trade_id,
                client_order_id,
                payload.get("signal_record_id"),
                str(payload.get("scorecard_id") or "")[:80] or None,
                symbol,
                decision,
                confidence,
                str(payload.get("risk_profile") or "balanced")[:30],
                evidence_coverage,
                int(bool(payload.get("is_demo", False))),
                entry,
                stop,
                target,
                qty,
                now,
                entry,
                0.0,
                dumps(meta),
                now,
                now,
            ),
        )
        _event(
            conn,
            trade_id,
            "OPEN",
            entry,
            {
                "qty": qty,
                "stop": stop,
                "target": target,
                "risk_gate_status": risk_gate.get("status"),
            },
        )
        row = conn.execute("SELECT * FROM paper_trades_v2 WHERE id=?", (trade_id,)).fetchone()
    result = _trade_dict(row)
    result["deduplicated"] = False
    return result


def get_trade(trade_id: str, *, db_path: str | None = None) -> dict | None:
    init_db(db_path)
    with connect(db_path) as conn:
        row = conn.execute("SELECT * FROM paper_trades_v2 WHERE id=?", (trade_id,)).fetchone()
    return _trade_dict(row) if row else None


def get_trade_by_client_order_id(
    client_order_id: str, *, db_path: str | None = None
) -> dict | None:
    init_db(db_path)
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM paper_trades_v2 WHERE client_order_id=?",
            (str(client_order_id)[:120],),
        ).fetchone()
    return _trade_dict(row) if row else None


def list_trades(
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
                "SELECT * FROM paper_trades_v2 WHERE status=? ORDER BY opened_at DESC LIMIT ?",
                (str(status).upper(), limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM paper_trades_v2 ORDER BY opened_at DESC LIMIT ?", (limit,)
            ).fetchall()
    return [_trade_dict(row) for row in rows]


def close_trade(
    trade_id: str,
    exit_price,
    reason: str = "MANUAL",
    *,
    outcome_hook: Callable[[dict], object] | None = None,
    db_path: str | None = None,
) -> dict:
    price = _finite(exit_price, "exit_price")
    if price <= 0:
        raise ValueError("exit_price 必須大於 0")
    reason = str(reason or "MANUAL").upper().strip()
    if reason not in _EXIT_REASONS:
        raise ValueError("不支援的 exit reason")
    init_db(db_path)
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM paper_trades_v2 WHERE id=?", (str(trade_id),)
        ).fetchone()
        if not row:
            raise ValueError("找不到模擬交易")
        if row["status"] != "OPEN":
            result = _trade_dict(row)
            result["already_closed"] = True
            return result
        entry = float(row["entry_price"])
        stop = float(row["stop_price"])
        qty = int(row["qty"])
        pnl = (price - entry) * qty
        return_pct = (price / entry - 1) * 100
        initial_risk = max((entry - stop) * qty, 0.0)
        r_multiple = pnl / initial_risk if initial_risk > 0 else None
        now = utc_now()
        conn.execute(
            """
            UPDATE paper_trades_v2
            SET status='CLOSED', current_price=?, unrealized_pnl=0,
                exit_price=?, exit_reason=?, closed_at=?, realized_pnl=?,
                return_pct=?, r_multiple=?, updated_at=?
            WHERE id=? AND status='OPEN'
            """,
            (
                price,
                price,
                reason,
                now,
                round(pnl, 4),
                round(return_pct, 4),
                round(r_multiple, 4) if r_multiple is not None else None,
                now,
                trade_id,
            ),
        )
        _event(
            conn,
            trade_id,
            "CLOSE",
            price,
            {"reason": reason, "realized_pnl": pnl, "return_pct": return_pct},
        )
        closed = conn.execute(
            "SELECT * FROM paper_trades_v2 WHERE id=?", (trade_id,)
        ).fetchone()
    result = _trade_dict(closed)
    result["already_closed"] = False
    if outcome_hook is not None:
        try:
            hook_result = outcome_hook(result)
            if isinstance(hook_result, dict) and not hook_result.get("ok", True):
                raise RuntimeError(hook_result.get("error") or "outcome hook failed")
            result["outcome_synced"] = True
        except Exception as exc:
            result["outcome_synced"] = False
            result["outcome_sync_error"] = str(exc)[:300]
    return result


def _bar_values(value) -> tuple[float, float, float, float]:
    if isinstance(value, dict):
        close = _finite(value.get("close", value.get("price")), "close")
        open_price = _finite(value.get("open", close), "open")
        high = _finite(value.get("high", close), "high")
        low = _finite(value.get("low", close), "low")
        if min(open_price, high, low, close) <= 0 or high < low:
            raise ValueError("OHLC 價格無效")
        return open_price, high, low, close
    price = _finite(value, "price")
    if price <= 0:
        raise ValueError("price 必須大於 0")
    return price, price, price, price


def mark_to_market(
    prices: dict,
    *,
    outcome_hook: Callable[[dict], object] | None = None,
    db_path: str | None = None,
) -> dict:
    """Update open positions; stop is assumed before target within one bar."""
    if not isinstance(prices, dict) or len(prices) > 500:
        raise ValueError("prices 必須是最多 500 筆的 JSON object")
    normalised = {
        str(symbol).upper().strip(): _bar_values(value)
        for symbol, value in prices.items()
        if _SYMBOL_RE.fullmatch(str(symbol).upper().strip())
    }
    updated: list[dict] = []
    closed: list[dict] = []
    for trade in list_trades(status="OPEN", limit=500, db_path=db_path):
        bar = normalised.get(trade["symbol"])
        if not bar:
            continue
        open_price, high, low, close = bar
        if low <= trade["stop_price"]:
            fill = min(open_price, trade["stop_price"])
            closed.append(
                close_trade(
                    trade["id"], fill, "STOP", outcome_hook=outcome_hook, db_path=db_path
                )
            )
            continue
        if high >= trade["target_price"]:
            closed.append(
                close_trade(
                    trade["id"],
                    trade["target_price"],
                    "TARGET",
                    outcome_hook=outcome_hook,
                    db_path=db_path,
                )
            )
            continue
        unrealized = (close - trade["entry_price"]) * trade["qty"]
        now = utc_now()
        with connect(db_path) as conn:
            conn.execute(
                """
                UPDATE paper_trades_v2
                SET current_price=?, unrealized_pnl=?, updated_at=?
                WHERE id=? AND status='OPEN'
                """,
                (close, round(unrealized, 4), now, trade["id"]),
            )
            _event(conn, trade["id"], "MARK", close, {"high": high, "low": low})
            row = conn.execute(
                "SELECT * FROM paper_trades_v2 WHERE id=?", (trade["id"],)
            ).fetchone()
        updated.append(_trade_dict(row))
    return {
        "ok": True,
        "updated": updated,
        "closed": closed,
        "updated_count": len(updated),
        "closed_count": len(closed),
    }


def performance_summary(db_path: str | None = None) -> dict:
    trades = list_trades(status="CLOSED", limit=500, db_path=db_path)
    open_trades = list_trades(status="OPEN", limit=500, db_path=db_path)
    pnl_values = [float(trade.get("realized_pnl") or 0) for trade in reversed(trades)]
    returns = [float(trade.get("return_pct") or 0) for trade in trades]
    r_values = [float(trade["r_multiple"]) for trade in trades if trade.get("r_multiple") is not None]
    wins = sum(1 for value in pnl_values if value > 0)
    equity = peak = drawdown = 0.0
    for pnl in pnl_values:
        equity += pnl
        peak = max(peak, equity)
        drawdown = min(drawdown, equity - peak)
    return {
        "closed_trades": len(trades),
        "open_trades": len(open_trades),
        "win_rate": round(wins / len(trades) * 100, 2) if trades else None,
        "realized_pnl": round(sum(pnl_values), 2),
        "unrealized_pnl": round(sum(float(t.get("unrealized_pnl") or 0) for t in open_trades), 2),
        "average_return_pct": round(sum(returns) / len(returns), 3) if returns else None,
        "average_r_multiple": round(sum(r_values) / len(r_values), 3) if r_values else None,
        "max_closed_trade_drawdown": round(abs(drawdown), 2),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "disclaimer": "模擬成交不代表真實滑價、流動性或未來績效。",
    }
