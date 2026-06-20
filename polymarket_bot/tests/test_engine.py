"""Offline unit tests for the deterministic engine pieces (no DB / network).

Run with:  pytest -q   (from the polymarket_bot/ directory)
These cover the explainable, backtestable core: fair value, news_score, and
signal generation.
"""
import math

from app.engine.fair_value import compute_fair_price
from app.engine import signal_engine
from app.models import Classification, OrderbookTop


def test_fair_value_decays_to_mid():
    mid = 0.50
    # Strong positive news at t=0 shifts price up.
    r0 = compute_fair_price(mid, news_score=1.0, seconds_since_news=0.0)
    assert r0.fair_price > mid
    # Far in the future, the adjustment decays toward zero -> back to mid.
    r_late = compute_fair_price(mid, news_score=1.0, seconds_since_news=10_000)
    assert math.isclose(r_late.fair_price, mid, abs_tol=1e-3)


def test_fair_value_clamped_probability():
    r = compute_fair_price(0.99, news_score=1.0, seconds_since_news=0.0)
    assert 0.0 < r.fair_price < 1.0


def test_news_score_sign():
    yes = Classification(True, "c1", "YES", 0.8, 0.5, 0.9, "stub", "")
    no = Classification(True, "c1", "NO", 0.8, 0.5, 0.9, "stub", "")
    none = Classification(False, None, "NONE", 0.0, 0.0, 0.2, "stub", "")
    assert yes.news_score > 0
    assert no.news_score < 0
    assert none.news_score == 0.0


def test_signal_buy_yes_when_underpriced():
    # Ask far below fair value -> BUY_YES
    book = OrderbookTop("c1", "tok", best_bid=0.40, best_ask=0.41)
    cls = Classification(True, "c1", "YES", 1.0, 1.0, 0.9, "stub", "")
    sig = signal_engine.evaluate(
        news_event_id=1, condition_id="c1", yes_book=book,
        classification=cls, seconds_since_news=0.0,
    )
    assert sig is not None
    assert sig.side == "BUY_YES"
    assert sig.edge > 0


def test_signal_none_when_confidence_low():
    book = OrderbookTop("c1", "tok", best_bid=0.40, best_ask=0.41)
    cls = Classification(True, "c1", "YES", 1.0, 1.0, 0.10, "stub", "")
    sig = signal_engine.evaluate(
        news_event_id=1, condition_id="c1", yes_book=book,
        classification=cls, seconds_since_news=0.0,
    )
    assert sig is None


def test_signal_none_when_book_one_sided():
    book = OrderbookTop("c1", "tok", best_bid=None, best_ask=0.41)
    cls = Classification(True, "c1", "YES", 1.0, 1.0, 0.9, "stub", "")
    sig = signal_engine.evaluate(
        news_event_id=1, condition_id="c1", yes_book=book,
        classification=cls, seconds_since_news=0.0,
    )
    assert sig is None
