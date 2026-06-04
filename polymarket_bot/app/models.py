"""In-memory domain models shared across modules.

These are lightweight dataclasses used on the asyncio event bus; the persistent
shapes live in schema.sql / repository.py.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Market:
    condition_id: str
    question: str
    slug: Optional[str] = None
    yes_token_id: Optional[str] = None
    no_token_id: Optional[str] = None
    active: bool = True
    closed: bool = False
    end_date: Optional[datetime] = None


@dataclass
class OrderbookTop:
    """Top-of-book state for a single token."""
    condition_id: str
    token_id: str
    best_bid: Optional[float] = None
    best_ask: Optional[float] = None
    bid_size: Optional[float] = None
    ask_size: Optional[float] = None
    last_trade: Optional[float] = None
    updated_at: datetime = field(default_factory=utcnow)

    @property
    def mid_price(self) -> Optional[float]:
        if self.best_bid is not None and self.best_ask is not None:
            return (self.best_bid + self.best_ask) / 2.0
        return self.last_trade


@dataclass
class NewsEvent:
    source: str
    external_id: str
    title: str
    summary: Optional[str] = None
    url: Optional[str] = None
    published_at: Optional[datetime] = None
    ingested_at: datetime = field(default_factory=utcnow)
    id: Optional[int] = None  # set after DB insert


@dataclass
class Classification:
    """Output of the LLM classifier. Never includes a position size."""
    is_important: bool
    market_condition_id: Optional[str]
    direction: str  # 'YES' | 'NO' | 'NONE'
    relevance_score: float
    surprise_score: float
    confidence_score: float
    provider: str
    rationale: str

    @property
    def news_score(self) -> float:
        """Signed magnitude of the news in [-1, 1].

        Positive pushes YES probability up, negative pushes it down.
        Magnitude = relevance * surprise (both 0..1).
        """
        if not self.is_important or self.direction == "NONE":
            return 0.0
        sign = 1.0 if self.direction == "YES" else -1.0
        return sign * self.relevance_score * self.surprise_score


@dataclass
class Signal:
    news_event_id: Optional[int]
    condition_id: str
    token_id: str
    side: str  # 'BUY_YES' | 'SELL_YES'
    mid_price: float
    best_bid: float
    best_ask: float
    fair_price: float
    news_score: float
    edge: float
    confidence: float
    status: str = "generated"
    reject_reason: Optional[str] = None
    id: Optional[int] = None
