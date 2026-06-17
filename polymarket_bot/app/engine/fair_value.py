"""Fair probability engine.

Implements the decay model from the spec:

    fair_price = current_mid_price + alpha * news_score * exp(-lambda * seconds_since_news)

Where:
  - current_mid_price : top-of-book mid for the YES token (a probability in 0..1)
  - alpha             : max probability shift a maximally-strong news event can apply
  - news_score        : signed strength in [-1, 1] (YES positive, NO negative)
  - lambda            : exponential decay rate per second; the edge fades as the
                        market absorbs the news
  - seconds_since_news: time elapsed since the news was published

The result is clamped to (0, 1) since it represents a probability.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from ..config import get_settings


@dataclass
class FairValueResult:
    mid_price: float
    news_score: float
    seconds_since_news: float
    decay: float
    adjustment: float
    fair_price: float


def compute_fair_price(mid_price: float, news_score: float, seconds_since_news: float) -> FairValueResult:
    s = get_settings()
    alpha = s.fair_alpha
    lam = s.fair_lambda

    decay = math.exp(-lam * max(0.0, seconds_since_news))
    adjustment = alpha * news_score * decay
    fair = mid_price + adjustment
    # Clamp to a valid probability with a tiny epsilon margin.
    fair = max(0.001, min(0.999, fair))

    return FairValueResult(
        mid_price=mid_price,
        news_score=news_score,
        seconds_since_news=seconds_since_news,
        decay=decay,
        adjustment=adjustment,
        fair_price=fair,
    )
