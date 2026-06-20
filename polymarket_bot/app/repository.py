"""Data-access layer. All SQL lives here so the engines stay storage-agnostic.

Every signal and trade is persisted (explainability requirement), including
rejected signals, so the full decision history can be replayed / backtested.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .db import get_pool
from .models import Classification, Market, NewsEvent, Signal


# --------------------------------------------------------------------------- #
# Markets
# --------------------------------------------------------------------------- #
async def upsert_market(m: Market) -> None:
    pool = get_pool()
    await pool.execute(
        """
        INSERT INTO markets (condition_id, question, slug, yes_token_id, no_token_id,
                             active, closed, end_date, updated_at)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8, now())
        ON CONFLICT (condition_id) DO UPDATE SET
            question = EXCLUDED.question,
            slug = EXCLUDED.slug,
            yes_token_id = EXCLUDED.yes_token_id,
            no_token_id = EXCLUDED.no_token_id,
            active = EXCLUDED.active,
            closed = EXCLUDED.closed,
            end_date = EXCLUDED.end_date,
            updated_at = now()
        """,
        m.condition_id, m.question, m.slug, m.yes_token_id, m.no_token_id,
        m.active, m.closed, m.end_date,
    )


async def list_active_markets() -> List[Dict[str, Any]]:
    pool = get_pool()
    rows = await pool.fetch("SELECT * FROM markets WHERE active AND NOT closed ORDER BY question")
    return [dict(r) for r in rows]


# --------------------------------------------------------------------------- #
# News
# --------------------------------------------------------------------------- #
async def insert_news(n: NewsEvent) -> Optional[int]:
    """Insert raw news; returns new id, or None if it was a duplicate."""
    pool = get_pool()
    row = await pool.fetchrow(
        """
        INSERT INTO news_events (source, external_id, title, summary, url, published_at)
        VALUES ($1,$2,$3,$4,$5,$6)
        ON CONFLICT (source, external_id) DO NOTHING
        RETURNING id
        """,
        n.source, n.external_id, n.title, n.summary, n.url, n.published_at,
    )
    return row["id"] if row else None


async def save_classification(news_id: int, c: Classification) -> None:
    pool = get_pool()
    await pool.execute(
        """
        UPDATE news_events SET
            classified = TRUE,
            is_important = $2,
            market_condition_id = $3,
            direction = $4,
            relevance_score = $5,
            surprise_score = $6,
            confidence_score = $7,
            llm_provider = $8,
            llm_rationale = $9,
            classified_at = now()
        WHERE id = $1
        """,
        news_id, c.is_important, c.market_condition_id, c.direction,
        c.relevance_score, c.surprise_score, c.confidence_score,
        c.provider, c.rationale,
    )


async def recent_news(limit: int = 20) -> List[Dict[str, Any]]:
    pool = get_pool()
    rows = await pool.fetch(
        "SELECT * FROM news_events ORDER BY ingested_at DESC LIMIT $1", limit
    )
    return [dict(r) for r in rows]


# --------------------------------------------------------------------------- #
# Orderbook snapshots
# --------------------------------------------------------------------------- #
async def save_snapshot(
    condition_id: str, token_id: str, best_bid, best_ask, mid, last_trade, bid_size, ask_size
) -> None:
    pool = get_pool()
    await pool.execute(
        """
        INSERT INTO orderbook_snapshots
            (condition_id, token_id, best_bid, best_ask, mid_price, last_trade, bid_size, ask_size)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
        """,
        condition_id, token_id, best_bid, best_ask, mid, last_trade, bid_size, ask_size,
    )


# --------------------------------------------------------------------------- #
# Signals
# --------------------------------------------------------------------------- #
async def insert_signal(s: Signal) -> int:
    pool = get_pool()
    row = await pool.fetchrow(
        """
        INSERT INTO signals (news_event_id, condition_id, token_id, side, mid_price,
                             best_bid, best_ask, fair_price, news_score, edge,
                             confidence, status, reject_reason)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)
        RETURNING id
        """,
        s.news_event_id, s.condition_id, s.token_id, s.side, s.mid_price,
        s.best_bid, s.best_ask, s.fair_price, s.news_score, s.edge,
        s.confidence, s.status, s.reject_reason,
    )
    s.id = row["id"]
    return s.id


async def mark_signal_executed(signal_id: int) -> None:
    pool = get_pool()
    await pool.execute("UPDATE signals SET status='executed' WHERE id=$1", signal_id)


async def recent_signals(limit: int = 20) -> List[Dict[str, Any]]:
    pool = get_pool()
    rows = await pool.fetch("SELECT * FROM signals ORDER BY created_at DESC LIMIT $1", limit)
    return [dict(r) for r in rows]


# --------------------------------------------------------------------------- #
# Paper trades
# --------------------------------------------------------------------------- #
async def open_trade(
    signal_id: int, condition_id: str, token_id: str, side: str,
    size_usd: float, shares: float, entry_price: float, entry_fee: float,
) -> int:
    pool = get_pool()
    row = await pool.fetchrow(
        """
        INSERT INTO paper_trades
            (signal_id, condition_id, token_id, side, size_usd, shares,
             entry_price, entry_fee, status)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,'open')
        RETURNING id
        """,
        signal_id, condition_id, token_id, side, size_usd, shares, entry_price, entry_fee,
    )
    return row["id"]


async def close_trade(
    trade_id: int, exit_price: float, exit_fee: float, exit_reason: str,
    pnl: float, holding_seconds: float,
) -> None:
    pool = get_pool()
    await pool.execute(
        """
        UPDATE paper_trades SET
            exit_price = $2, exit_fee = $3, exit_at = now(), exit_reason = $4,
            pnl = $5, holding_seconds = $6, status = 'closed'
        WHERE id = $1
        """,
        trade_id, exit_price, exit_fee, exit_reason, pnl, holding_seconds,
    )


async def open_trades() -> List[Dict[str, Any]]:
    pool = get_pool()
    rows = await pool.fetch("SELECT * FROM paper_trades WHERE status='open'")
    return [dict(r) for r in rows]


async def market_exposure(condition_id: str) -> float:
    pool = get_pool()
    val = await pool.fetchval(
        "SELECT COALESCE(SUM(size_usd),0) FROM paper_trades WHERE status='open' AND condition_id=$1",
        condition_id,
    )
    return float(val or 0.0)


async def realized_pnl_since(since: datetime) -> float:
    pool = get_pool()
    val = await pool.fetchval(
        "SELECT COALESCE(SUM(pnl),0) FROM paper_trades WHERE status='closed' AND exit_at >= $1",
        since,
    )
    return float(val or 0.0)


async def daily_realized_pnl() -> float:
    start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    return await realized_pnl_since(start)


# --------------------------------------------------------------------------- #
# Dashboard aggregates
# --------------------------------------------------------------------------- #
async def performance_stats() -> Dict[str, Any]:
    pool = get_pool()
    row = await pool.fetchrow(
        """
        SELECT
            COUNT(*)                                          AS total_trades,
            COUNT(*) FILTER (WHERE pnl > 0)                   AS wins,
            COALESCE(SUM(pnl), 0)                             AS total_pnl,
            COALESCE(AVG(pnl), 0)                             AS avg_pnl,
            COALESCE(AVG(holding_seconds), 0)                 AS avg_holding
        FROM paper_trades WHERE status='closed'
        """
    )
    total = row["total_trades"] or 0
    wins = row["wins"] or 0
    win_rate = (wins / total) if total else 0.0

    # average edge from executed signals
    avg_edge = await pool.fetchval(
        "SELECT COALESCE(AVG(edge),0) FROM signals WHERE status='executed'"
    )

    max_dd = await _max_drawdown()

    return {
        "total_trades": total,
        "wins": wins,
        "win_rate": win_rate,
        "total_pnl": float(row["total_pnl"]),
        "avg_pnl": float(row["avg_pnl"]),
        "avg_holding_seconds": float(row["avg_holding"]),
        "avg_edge": float(avg_edge or 0.0),
        "max_drawdown": max_dd,
    }


async def _max_drawdown() -> float:
    """Peak-to-trough drawdown of the cumulative realized-PnL equity curve."""
    pool = get_pool()
    rows = await pool.fetch(
        "SELECT pnl FROM paper_trades WHERE status='closed' ORDER BY exit_at ASC"
    )
    equity = 0.0
    peak = 0.0
    max_dd = 0.0
    for r in rows:
        equity += float(r["pnl"] or 0.0)
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
    return max_dd
