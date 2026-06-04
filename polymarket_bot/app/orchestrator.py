"""Wires every module together on a single asyncio event loop.

Data flow:

    RSS/news --> NewsIngestor --(queue)--> classify (LLM) --> fair value
              --> signal engine --> risk manager --> paper trader

    Polymarket WS --> OrderbookStore  (read by fair value / signal / trader)

All long-running pieces are asyncio tasks started in `start()` and cancelled in
`stop()` (called from the FastAPI lifespan).
"""
from __future__ import annotations

import asyncio
import logging
from typing import List, Optional

from . import repository as repo
from .classifier.llm_classifier import LLMClassifier
from .clob_client import ClobClient
from .config import get_settings
from .engine import signal_engine
from .engine.paper_trader import PaperTrader
from .engine.risk_manager import RiskManager
from .models import NewsEvent, utcnow
from .news.ingestor import NewsIngestor
from .ws_client import OrderbookStore, PolymarketWS, snapshot_loop

log = logging.getLogger(__name__)


class Orchestrator:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.store = OrderbookStore()
        self.clob = ClobClient()
        self.classifier = LLMClassifier()
        self.risk = RiskManager()
        self.trader = PaperTrader(self.store)
        self.news_queue: "asyncio.Queue[NewsEvent]" = asyncio.Queue()
        self.ingestor = NewsIngestor(self.news_queue)
        self.ws: Optional[PolymarketWS] = None
        self._tasks: List[asyncio.Task] = []
        self._markets: List[dict] = []

    # ------------------------------------------------------------------ #
    async def start(self) -> None:
        assert not self.settings.live_trading_enabled, "v1 is paper-only; LIVE_TRADING_ENABLED must be false"
        await self._bootstrap_markets()

        token_ids = [t for t in self.store.tracked_tokens()]
        self.ws = PolymarketWS(self.store)

        # Seed REST snapshots so signals can fire before the first WS tick.
        await self._seed_books()

        self._tasks = [
            asyncio.create_task(self.ws.run(token_ids), name="ws"),
            asyncio.create_task(snapshot_loop(self.store), name="snapshots"),
            asyncio.create_task(self.ingestor.run(), name="news"),
            asyncio.create_task(self._classify_loop(), name="classify"),
            asyncio.create_task(self.trader.manage_loop(), name="trader"),
        ]
        log.info("Orchestrator started with %d markets / %d tokens",
                 len(self._markets), len(token_ids))

    async def stop(self) -> None:
        if self.ws:
            self.ws.stop()
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        await self.clob.close()
        log.info("Orchestrator stopped")

    # ------------------------------------------------------------------ #
    async def _bootstrap_markets(self) -> None:
        markets = []
        if self.settings.tracked_markets:
            for ident in self.settings.tracked_markets:
                m = await self.clob.get_market(ident)
                if m:
                    markets.append(m)
        if not markets:
            markets = await self.clob.discover_markets(self.settings.auto_track_top_n)

        for m in markets:
            await repo.upsert_market(m)
            if m.yes_token_id:
                self.store.register(m.condition_id, m.yes_token_id)
            if m.no_token_id:
                self.store.register(m.condition_id, m.no_token_id)
        self._markets = await repo.list_active_markets()

    async def _seed_books(self) -> None:
        for m in self._markets:
            if m.get("yes_token_id"):
                top = await self.clob.get_orderbook_top(m["condition_id"], m["yes_token_id"])
                top.last_trade = await self.clob.get_last_trade_price(m["yes_token_id"])
                self.store.update(top)

    # ------------------------------------------------------------------ #
    async def _classify_loop(self) -> None:
        """Consume ingested news, classify, then run the signal pipeline."""
        while True:
            news: NewsEvent = await self.news_queue.get()
            try:
                await self._process_news(news)
            except Exception as exc:  # noqa: BLE001
                log.exception("processing news #%s failed: %s", news.id, exc)
            finally:
                self.news_queue.task_done()

    async def _process_news(self, news: NewsEvent) -> None:
        markets = await repo.list_active_markets()
        classification = await self.classifier.classify(news, markets)
        if news.id is not None:
            await repo.save_classification(news.id, classification)

        if not classification.is_important or not classification.market_condition_id:
            return

        # Find the YES token for the affected market.
        market = next((m for m in markets if m["condition_id"] == classification.market_condition_id), None)
        if not market or not market.get("yes_token_id"):
            return
        yes_book = self.store.get(market["yes_token_id"])
        if not yes_book:
            return

        seconds_since = 0.0
        if news.published_at:
            seconds_since = max(0.0, (utcnow() - news.published_at).total_seconds())

        signal = signal_engine.evaluate(
            news_event_id=news.id,
            condition_id=market["condition_id"],
            yes_book=yes_book,
            classification=classification,
            seconds_since_news=seconds_since,
        )
        if signal is None:
            return

        # Risk check -> persist signal with the outcome (explainability).
        allowed, size_usd, reason = await self.risk.check(signal.condition_id)
        if not allowed:
            signal.status = "rejected"
            signal.reject_reason = reason
            await repo.insert_signal(signal)
            log.info("Signal rejected by risk: %s", reason)
            return

        await repo.insert_signal(signal)
        trade_id = await self.trader.execute(signal, size_usd)
        await repo.mark_signal_executed(signal.id)
        log.info("Executed signal #%s as paper trade #%s", signal.id, trade_id)
