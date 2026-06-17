"""Signal engine.

Compares the fair price against live top-of-book to find transient mispricing:

  - fair_price - best_ask > minimum_edge  AND  confidence > threshold
        -> BUY_YES   (we can buy the YES token cheaper than fair value)

  - best_bid - fair_price > minimum_edge  AND  confidence > threshold
        -> SELL_YES  (the bid is richer than fair value; sell / short-equivalent)

Every evaluated opportunity that crosses the edge threshold becomes a Signal
record (status 'generated'); the risk manager decides whether it executes.
"""
from __future__ import annotations

import logging
from typing import Optional

from ..config import get_settings
from ..models import Classification, OrderbookTop, Signal
from .fair_value import compute_fair_price

log = logging.getLogger(__name__)


def evaluate(
    *,
    news_event_id: Optional[int],
    condition_id: str,
    yes_book: OrderbookTop,
    classification: Classification,
    seconds_since_news: float,
) -> Optional[Signal]:
    """Return a Signal if an edge exists, else None."""
    s = get_settings()

    mid = yes_book.mid_price
    if mid is None or yes_book.best_bid is None or yes_book.best_ask is None:
        return None  # need a two-sided book to measure edge

    confidence = classification.confidence_score
    news_score = classification.news_score
    fv = compute_fair_price(mid, news_score, seconds_since_news)
    fair = fv.fair_price

    # Confidence gate first (cheap).
    if confidence < s.confidence_threshold:
        return None

    buy_edge = fair - yes_book.best_ask
    sell_edge = yes_book.best_bid - fair

    side = None
    edge = 0.0
    if buy_edge > s.minimum_edge and buy_edge >= sell_edge:
        side, edge = "BUY_YES", buy_edge
    elif sell_edge > s.minimum_edge:
        side, edge = "SELL_YES", sell_edge

    if side is None:
        return None

    log.info("Signal %s on %s edge=%.4f fair=%.4f bid=%.4f ask=%.4f conf=%.2f",
             side, condition_id, edge, fair, yes_book.best_bid, yes_book.best_ask, confidence)

    return Signal(
        news_event_id=news_event_id,
        condition_id=condition_id,
        token_id=yes_book.token_id,
        side=side,
        mid_price=mid,
        best_bid=yes_book.best_bid,
        best_ask=yes_book.best_ask,
        fair_price=fair,
        news_score=news_score,
        edge=edge,
        confidence=confidence,
        status="generated",
    )
