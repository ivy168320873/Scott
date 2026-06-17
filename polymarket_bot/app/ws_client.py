"""Polymarket CLOB WebSocket client for real-time orderbook updates.

Maintains an in-memory top-of-book per token (`OrderbookStore`) that the rest
of the system reads synchronously, and periodically persists snapshots.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Dict, List, Optional

import websockets

from . import repository as repo
from .config import get_settings
from .models import OrderbookTop, utcnow

log = logging.getLogger(__name__)


class OrderbookStore:
    """Thread-free in-memory cache of top-of-book keyed by token_id."""

    def __init__(self) -> None:
        self._books: Dict[str, OrderbookTop] = {}
        self._token_to_condition: Dict[str, str] = {}

    def register(self, condition_id: str, token_id: str) -> None:
        self._token_to_condition[token_id] = condition_id
        self._books.setdefault(token_id, OrderbookTop(condition_id=condition_id, token_id=token_id))

    def update(self, top: OrderbookTop) -> None:
        self._books[top.token_id] = top

    def get(self, token_id: str) -> Optional[OrderbookTop]:
        return self._books.get(token_id)

    def all(self) -> List[OrderbookTop]:
        return list(self._books.values())

    def tracked_tokens(self) -> List[str]:
        return list(self._token_to_condition.keys())


class PolymarketWS:
    """Subscribes to the `market` channel and updates the OrderbookStore."""

    def __init__(self, store: OrderbookStore) -> None:
        self.store = store
        self.ws_url = get_settings().clob_ws_url
        self._stop = asyncio.Event()

    async def run(self, token_ids: List[str]) -> None:
        """Connect with auto-reconnect; runs until stop() is called."""
        if not token_ids:
            log.warning("No token_ids to subscribe to; WS not started")
            return
        backoff = 1
        while not self._stop.is_set():
            try:
                async with websockets.connect(self.ws_url, ping_interval=10, ping_timeout=20) as ws:
                    sub = {"assets_ids": token_ids, "type": "market"}
                    await ws.send(json.dumps(sub))
                    log.info("WS subscribed to %d tokens", len(token_ids))
                    backoff = 1
                    async for raw in ws:
                        if self._stop.is_set():
                            break
                        await self._handle(raw)
            except Exception as exc:  # noqa: BLE001
                log.warning("WS disconnected: %s; reconnecting in %ss", exc, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30)

    async def _handle(self, raw: str) -> None:
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return
        events = msg if isinstance(msg, list) else [msg]
        for ev in events:
            etype = ev.get("event_type")
            if etype in ("book", "price_change"):
                await self._apply_book(ev)

    async def _apply_book(self, ev: dict) -> None:
        token_id = ev.get("asset_id") or ev.get("market")
        if not token_id:
            return
        condition_id = self.store._token_to_condition.get(token_id, ev.get("market", ""))
        top = self.store.get(token_id) or OrderbookTop(condition_id=condition_id, token_id=token_id)

        bids = ev.get("bids") or ev.get("buys")
        asks = ev.get("asks") or ev.get("sells")
        if bids:
            try:
                best = max(bids, key=lambda x: float(x["price"]))
                top.best_bid = float(best["price"])
                top.bid_size = float(best.get("size", 0) or 0)
            except (ValueError, KeyError):
                pass
        if asks:
            try:
                best = min(asks, key=lambda x: float(x["price"]))
                top.best_ask = float(best["price"])
                top.ask_size = float(best.get("size", 0) or 0)
            except (ValueError, KeyError):
                pass
        top.updated_at = utcnow()
        self.store.update(top)

    def stop(self) -> None:
        self._stop.set()


async def snapshot_loop(store: OrderbookStore, interval: float = 10.0) -> None:
    """Periodically persist top-of-book snapshots for backtesting."""
    while True:
        await asyncio.sleep(interval)
        for top in store.all():
            if top.best_bid is None and top.best_ask is None:
                continue
            try:
                await repo.save_snapshot(
                    top.condition_id, top.token_id, top.best_bid, top.best_ask,
                    top.mid_price, top.last_trade, top.bid_size, top.ask_size,
                )
            except Exception as exc:  # noqa: BLE001
                log.warning("snapshot save failed: %s", exc)
