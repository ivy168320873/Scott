"""News ingestion orchestrator.

Polls every configured source on an interval, deduplicates via the DB unique
constraint, and pushes brand-new events onto an asyncio queue for the
classifier to consume.
"""
from __future__ import annotations

import asyncio
import logging
from typing import List

from .. import repository as repo
from ..config import get_settings
from ..models import NewsEvent
from .base import NewsSource
from .rss_source import RSSNewsSource

log = logging.getLogger(__name__)


class NewsIngestor:
    def __init__(self, out_queue: "asyncio.Queue[NewsEvent]", sources: List[NewsSource] | None = None) -> None:
        s = get_settings()
        self.out_queue = out_queue
        self.poll_seconds = s.news_poll_seconds
        self.sources = sources or [RSSNewsSource(s.news_rss_feeds)]

    async def run(self) -> None:
        log.info("News ingestor started (%d sources, %ss interval)",
                 len(self.sources), self.poll_seconds)
        while True:
            try:
                await self._poll_once()
            except Exception as exc:  # noqa: BLE001
                log.exception("news poll error: %s", exc)
            await asyncio.sleep(self.poll_seconds)

    async def _poll_once(self) -> None:
        for source in self.sources:
            events = await source.fetch()
            new_count = 0
            for ev in events:
                news_id = await repo.insert_news(ev)
                if news_id is not None:  # not a duplicate
                    ev.id = news_id
                    await self.out_queue.put(ev)
                    new_count += 1
            if new_count:
                log.info("Ingested %d new items from %s", new_count, source.name)
