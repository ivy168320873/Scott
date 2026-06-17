"""RSS-based news source.

Parsing is done with feedparser in a thread (it is blocking/CPU-bound).
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import datetime, timezone
from time import mktime
from typing import List

import feedparser

from ..models import NewsEvent
from .base import NewsSource

log = logging.getLogger(__name__)


class RSSNewsSource(NewsSource):
    name = "rss"

    def __init__(self, feed_urls: List[str]) -> None:
        self.feed_urls = feed_urls

    async def fetch(self) -> List[NewsEvent]:
        results: List[NewsEvent] = []
        for url in self.feed_urls:
            try:
                parsed = await asyncio.to_thread(feedparser.parse, url)
            except Exception as exc:  # noqa: BLE001
                log.warning("RSS fetch failed for %s: %s", url, exc)
                continue
            for entry in parsed.entries:
                results.append(self._to_event(url, entry))
        return results

    @staticmethod
    def _to_event(feed_url: str, entry) -> NewsEvent:
        link = getattr(entry, "link", "") or ""
        guid = getattr(entry, "id", "") or link or getattr(entry, "title", "")
        external_id = hashlib.sha1(guid.encode("utf-8")).hexdigest()

        published = None
        if getattr(entry, "published_parsed", None):
            published = datetime.fromtimestamp(mktime(entry.published_parsed), tz=timezone.utc)

        summary = getattr(entry, "summary", None)
        return NewsEvent(
            source=f"rss:{feed_url}",
            external_id=external_id,
            title=getattr(entry, "title", "(no title)"),
            summary=summary,
            url=link,
            published_at=published,
        )
