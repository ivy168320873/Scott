"""Paper trading engine.

NEVER places a real order. Simulates fills against live top-of-book with
configurable slippage and fees, records entry/exit/pnl/holding time, and force
-closes positions when the max holding time (90s default) elapses.

Exit logic for v1 is intentionally simple and explainable:
  - timeout : holding time exceeded MAX_HOLDING_SECONDS  -> exit at current mid
  - target  : price has converged to / past fair price   -> take profit
The aim is to measure whether the news-driven mispricing reverts within the
holding window.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Dict

from .. import repository as repo
from ..config import get_settings
from ..models import Signal, utcnow
from ..ws_client import OrderbookStore

log = logging.getLogger(__name__)


class PaperTrader:
    def __init__(self, store: OrderbookStore) -> None:
        s = get_settings()
        assert not s.live_trading_enabled, "LIVE_TRADING_ENABLED must be false in v1"
        self.store = store
        self.slippage = s.paper_slippage_bps / 10_000.0
        self.fee = s.paper_fee_bps / 10_000.0
        self.max_holding = s.max_holding_seconds
        # trade_id -> metadata needed to manage the open position
        self._open: Dict[int, dict] = {}

    # ------------------------------------------------------------------ #
    # Entry
    # ------------------------------------------------------------------ #
    async def execute(self, signal: Signal, size_usd: float) -> int:
        """Open a simulated position from an approved signal."""
        if signal.side == "BUY_YES":
            # Cross the spread: buy at ask, pay slippage upward.
            entry_price = signal.best_ask * (1 + self.slippage)
        else:  # SELL_YES (short-equivalent: sell YES at bid, slippage downward)
            entry_price = signal.best_bid * (1 - self.slippage)
        entry_price = max(0.001, min(0.999, entry_price))

        shares = size_usd / entry_price
        entry_fee = size_usd * self.fee

        trade_id = await repo.open_trade(
            signal_id=signal.id, condition_id=signal.condition_id, token_id=signal.token_id,
            side=signal.side, size_usd=size_usd, shares=shares,
            entry_price=entry_price, entry_fee=entry_fee,
        )
        self._open[trade_id] = {
            "signal": signal,
            "shares": shares,
            "entry_price": entry_price,
            "entry_fee": entry_fee,
            "size_usd": size_usd,
            "fair_price": signal.fair_price,
            "opened_at": utcnow(),
        }
        log.info("PAPER OPEN #%d %s %s shares=%.1f @ %.4f (size $%.0f)",
                 trade_id, signal.side, signal.condition_id, shares, entry_price, size_usd)
        return trade_id

    # ------------------------------------------------------------------ #
    # Position management loop
    # ------------------------------------------------------------------ #
    async def manage_loop(self, interval: float = 1.0) -> None:
        """Continuously evaluate open positions for exit conditions."""
        while True:
            await asyncio.sleep(interval)
            for trade_id in list(self._open.keys()):
                try:
                    await self._maybe_exit(trade_id)
                except Exception as exc:  # noqa: BLE001
                    log.exception("exit check failed for #%d: %s", trade_id, exc)

    async def _maybe_exit(self, trade_id: int) -> None:
        pos = self._open.get(trade_id)
        if not pos:
            return
        signal: Signal = pos["signal"]
        book = self.store.get(signal.token_id)
        mid = book.mid_price if book else None

        held = (utcnow() - pos["opened_at"]).total_seconds()
        reason = None
        if held >= self.max_holding:
            reason = "timeout"
        elif mid is not None:
            # Take profit if price has converged to fair value (mispricing closed).
            if signal.side == "BUY_YES" and mid >= pos["fair_price"]:
                reason = "target"
            elif signal.side == "SELL_YES" and mid <= pos["fair_price"]:
                reason = "target"

        if reason is None:
            return

        await self._close(trade_id, mid, reason, held)

    async def _close(self, trade_id: int, mid, reason: str, held: float) -> None:
        pos = self._open.pop(trade_id, None)
        if not pos:
            return
        signal: Signal = pos["signal"]
        book = self.store.get(signal.token_id)

        # Determine exit price (cross the spread on the way out).
        if signal.side == "BUY_YES":
            raw = (book.best_bid if book and book.best_bid else mid) or pos["entry_price"]
            exit_price = raw * (1 - self.slippage)
        else:
            raw = (book.best_ask if book and book.best_ask else mid) or pos["entry_price"]
            exit_price = raw * (1 + self.slippage)
        exit_price = max(0.001, min(0.999, exit_price))

        shares = pos["shares"]
        exit_fee = shares * exit_price * self.fee

        if signal.side == "BUY_YES":
            gross = (exit_price - pos["entry_price"]) * shares
        else:  # short-equivalent: profit when price falls
            gross = (pos["entry_price"] - exit_price) * shares
        pnl = gross - pos["entry_fee"] - exit_fee

        await repo.close_trade(trade_id, exit_price, exit_fee, reason, pnl, held)
        log.info("PAPER CLOSE #%d %s reason=%s exit=%.4f pnl=%.2f held=%.1fs",
                 trade_id, signal.side, reason, exit_price, pnl, held)

    @property
    def open_count(self) -> int:
        return len(self._open)
