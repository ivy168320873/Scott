import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from flask import Flask

import top_tier_decision_engine as ttde
from market_intelligence import service
from market_intelligence import sources as intelligence_sources
from market_intelligence.analysis import (
    aggregate_catalysts,
    analyze_articles,
    heuristic_analysis,
)
from market_intelligence.config import IntelligenceConfig
from market_intelligence.reporting import format_html
from market_intelligence.sources import _safe_error, fetch_alpha_vantage
from market_intelligence.storage import (
    get_symbol_catalyst,
    latest_report,
    recent_articles,
    status,
)
from market_intelligence.user_context import extract_user_context
from market_intelligence.web import create_blueprint


def _config(db_path: str, **overrides) -> IntelligenceConfig:
    values = {
        "enabled": True,
        "db_path": db_path,
        "timezone": "Asia/Taipei",
        "poll_minutes": 30,
        "daily_time": "08:30",
        "lookback_hours": 30,
        "max_symbols": 20,
        "max_articles": 60,
        "breaking_importance": 82,
        "dispatch_email": False,
        "dispatch_line": False,
        "anthropic_model": "test-model",
        "finnhub_key": "",
        "alpha_vantage_key": "",
    }
    values.update(overrides)
    return IntelligenceConfig(**values)


def _article(
    key: str = "event-1", *, title: str = "Company earnings beat expectations"
) -> dict:
    return {
        "dedupe_key": key,
        "source_provider": "Test News",
        "publisher": "Primary Source",
        "title": title,
        "summary": "Revenue growth beat expectations and management raised guidance.",
        "url": f"https://example.test/{key}",
        "published_at": datetime.now(timezone.utc).isoformat(),
        "symbols": ["NVDA"],
        "provider_sentiment": 0.5,
        "corroborating_providers": ["Test News", "Second Source"],
        "raw": {},
    }


def test_mobile_portfolio_and_watchlist_keys_are_linked():
    context = extract_user_context(
        {
            "portfolio_v1": [
                {
                    "symbol": "nvda",
                    "shares": 3,
                    "buyPrice": 112.5,
                    "buyDate": "2026-07-01",
                    "status": "open",
                },
                {"symbol": "TSLA", "shares": 1, "buyPrice": 200, "status": "closed"},
            ],
            "radarActive": ["aapl", "NVDA"],
            "radarWatchlist": ["msft"],
            "alertSettings_v1": {"email": "scott@example.test"},
        }
    )

    assert context["holding_symbols"] == ["NVDA"]
    assert context["holdings"][0]["cost"] == 112.5
    assert context["holdings"][0]["qty"] == 3
    assert context["watchlist"] == ["AAPL", "MSFT"]
    assert context["email"] == "scott@example.test"


def test_rules_are_confidence_gated_and_adjustment_is_bounded():
    now = datetime.now(timezone.utc)
    strong = heuristic_analysis(
        _article(),
        holding_symbols={"NVDA"},
        watchlist_symbols=set(),
        now=now,
    )
    weak_article = _article("event-2", title="A short headline about NVDA")
    weak_article.update(summary="", url="", publisher="", corroborating_providers=[])
    weak = heuristic_analysis(
        weak_article,
        holding_symbols={"NVDA"},
        watchlist_symbols=set(),
        now=now,
    )

    assert strong["direction"] == "BULLISH"
    assert 0 < strong["score_adjustment"] <= 8
    assert weak["confidence"] < 55
    assert weak["score_adjustment"] == 0


def test_service_persists_deduplicates_and_builds_portfolio_report(
    tmp_path, monkeypatch
):
    db = str(tmp_path / "intelligence.db")
    config = _config(db)
    article = _article()
    health = {
        "yahoo": {"configured": True, "attempted": 1, "succeeded": 1, "errors": []},
        "finnhub": {"configured": False, "attempted": 0, "succeeded": 0, "errors": []},
        "alpha_vantage": {
            "configured": False,
            "attempted": 0,
            "succeeded": 0,
            "errors": [],
        },
    }
    monkeypatch.setattr(
        service, "collect_news", lambda *_args, **_kwargs: ([dict(article)], health)
    )
    ai_calls = {"count": 0}

    def _ai_create(**_kwargs):
        ai_calls["count"] += 1
        return SimpleNamespace(
            content=[
                SimpleNamespace(
                    text=json.dumps(
                        [
                            {
                                "id": article["dedupe_key"][:16],
                                "direction": "BULLISH",
                                "importance": 90,
                                "confidence": 90,
                                "relevance": 100,
                                "time_horizon": "DAYS",
                                "fact": article["summary"],
                                "inference": "需等待價格確認。",
                                "affected_symbols": ["NVDA"],
                                "score_adjustment": 6,
                            }
                        ]
                    )
                )
            ]
        )

    ai_client = SimpleNamespace(messages=SimpleNamespace(create=_ai_create))
    data = {
        "portfolio_v1": [
            {"symbol": "NVDA", "shares": 2, "buyPrice": 100, "status": "open"}
        ]
    }

    first = service.run_intelligence(
        "manual", user_data=data, config=config, force=True, ai_client=ai_client
    )
    second = service.run_intelligence(
        "manual", user_data=data, config=config, force=True, ai_client=ai_client
    )

    assert first["_run"]["status"] == "SUCCESS"
    assert first["summary"]["new_count"] == 1
    assert first["summary"]["holding_impacts"] == 1
    assert second["summary"]["new_count"] == 0
    assert ai_calls["count"] == 1
    assert second["top_events"][0]["analysis_method"] == "AI_GROUNDED"
    assert status(db)["article_count"] == 1
    assert len(recent_articles(db)) == 1
    assert latest_report(db)["portfolio"]["holding_symbols"] == ["NVDA"]
    catalyst = get_symbol_catalyst(db, "NVDA")
    assert catalyst is not None
    assert -8 <= catalyst["score_adjustment"] <= 8


def test_breaking_dispatch_requires_new_high_relevance_event(tmp_path, monkeypatch):
    db = str(tmp_path / "intelligence.db")
    config = _config(db, dispatch_line=True)
    article = _article(title="NVDA routine company update")
    article["summary"] = "A routine company update without a material financial event."
    health = {
        "yahoo": {"configured": True, "attempted": 1, "succeeded": 1, "errors": []},
        "finnhub": {"configured": False, "attempted": 0, "succeeded": 0, "errors": []},
        "alpha_vantage": {
            "configured": False,
            "attempted": 0,
            "succeeded": 0,
            "errors": [],
        },
    }
    monkeypatch.setattr(
        service, "collect_news", lambda *_args, **_kwargs: ([dict(article)], health)
    )
    monkeypatch.setattr(
        service,
        "queue_report",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("low-importance event must not be dispatched")
        ),
    )

    report = service.run_intelligence(
        "breaking",
        user_data={"portfolio_v1": [{"symbol": "NVDA", "shares": 1, "buyPrice": 1}]},
        config=config,
        force=True,
        dispatch=True,
        ai_client=False,
    )

    assert report["_run"]["status"] == "SUCCESS"
    assert report["delivery"]["queued"] == []
    assert "沒有符合門檻" in report["delivery"]["skipped"][0]["reason"]


def _ohlcv():
    end = datetime.now(timezone.utc)
    closes = [100 + i * 0.2 for i in range(220)]
    return {
        "closes": closes,
        "opens": [price - 0.1 for price in closes],
        "highs": [price + 0.5 for price in closes],
        "lows": [price - 0.5 for price in closes],
        "volumes": [1_000_000 + i * 100 for i in range(220)],
        "timestamps": [
            int((end - timedelta(days=219 - i)).timestamp()) for i in range(220)
        ],
        "is_demo": False,
        "source": "test",
    }


def test_top_tier_news_overlay_cannot_exceed_eight_points(monkeypatch):
    monkeypatch.setattr(ttde, "_HAS_SCE", False)
    monkeypatch.setattr(ttde, "_load_news_catalyst", lambda _symbol: None)
    base = ttde.run_top_tier_decision("NVDA", lambda _symbol: _ohlcv())
    monkeypatch.setattr(
        ttde,
        "_load_news_catalyst",
        lambda _symbol: {
            "direction": "BULLISH",
            "importance": 95,
            "confidence": 95,
            "score_adjustment": 8,
            "applied": True,
        },
    )
    boosted = ttde.run_top_tier_decision("NVDA", lambda _symbol: _ohlcv())

    assert boosted["score_breakdown"]["news_catalyst"] == 8
    assert boosted["top_tier_score"] == min(100, base["top_tier_score"] + 8)
    assert 0 <= boosted["top_tier_score"] <= 100
    assert boosted["news_catalyst"] is not None


def test_catalyst_aggregation_keeps_only_user_universe():
    articles = analyze_articles(
        [_article()],
        holding_symbols=["NVDA"],
        watchlist_symbols=["AAPL"],
        model="test",
        client=False,
    )
    catalysts = aggregate_catalysts(articles, ["NVDA", "AAPL"])

    assert [item["symbol"] for item in catalysts] == ["NVDA"]


def test_ai_output_cannot_inject_symbols_outside_user_universe():
    article = _article()
    response = [
        {
            "id": article["dedupe_key"][:16],
            "direction": "BULLISH",
            "importance": 99,
            "confidence": 99,
            "relevance": 99,
            "time_horizon": "DAYS",
            "fact": "supplied fact",
            "inference": "model inference",
            "affected_symbols": ["TSLA"],
            "score_adjustment": 8,
        }
    ]
    client = SimpleNamespace(
        messages=SimpleNamespace(
            create=lambda **_kwargs: SimpleNamespace(
                content=[SimpleNamespace(text=json.dumps(response))]
            )
        )
    )

    analyzed = analyze_articles(
        [article],
        holding_symbols=["NVDA"],
        watchlist_symbols=[],
        model="test",
        client=client,
    )[0]["analysis"]

    assert analyzed["affected_symbols"] == []
    assert analyzed["score_adjustment"] == 0


def test_dashboard_blueprint_renders_and_exposes_safe_status(tmp_path):
    root = Path(__file__).resolve().parents[1]
    db = str(tmp_path / "web.db")
    app = Flask(__name__, template_folder=str(root / "templates"))
    app.register_blueprint(
        create_blueprint(
            db_path=db,
            load_user_data=lambda: {"radarWatchlist": ["AAPL"]},
            rate_limit=lambda *_args: None,
        )
    )
    client = app.test_client()

    page = client.get("/intelligence")
    api = client.get("/api/intelligence/status")

    assert page.status_code == 200
    assert "市場情報中樞" in page.get_data(as_text=True)
    assert api.status_code == 200
    payload = api.get_json()
    assert payload["portfolio"]["watchlist"] == ["AAPL"]
    assert "finnhub_key" not in payload["config"]


def test_email_html_escapes_text_and_rejects_unsafe_links():
    report = {
        "run_type": "daily",
        "summary": {},
        "top_events": [
            {
                "title": "<script>alert(1)</script>",
                "fact": "fact",
                "inference": "inference",
                "url": "javascript:alert(1)",
            }
        ],
    }

    rendered = format_html(report)

    assert "<script>" not in rendered
    assert "&lt;script&gt;" in rendered
    assert "javascript:" not in rendered


def test_provider_errors_redact_api_credentials():
    error = _safe_error(
        "403 https://finnhub.io/api/v1/company-news?symbol=AAPL&token=secret-token"
    )

    assert "secret-token" not in error
    assert "[redacted]" in error


def test_alpha_vantage_batches_symbols_into_one_request():
    calls = []
    payload = {
        "feed": [
            {
                "title": "Chip companies publish material business update",
                "summary": "A supplied summary.",
                "source": "Example Wire",
                "url": "https://example.test/chips",
                "time_published": "20260814T010000",
                "overall_sentiment_score": "0.1",
                "ticker_sentiment": [
                    {"ticker": "NVDA", "ticker_sentiment_score": "0.2"},
                    {"ticker": "AAPL", "ticker_sentiment_score": "-0.4"},
                ],
            }
        ]
    }

    class _Response:
        def raise_for_status(self):
            return None

        def json(self):
            return payload

    def _get(_url, **kwargs):
        calls.append(kwargs["params"])
        return _Response()

    articles = fetch_alpha_vantage(
        ["NVDA", "AAPL"], api_key="test-key", session=SimpleNamespace(get=_get)
    )

    assert len(calls) == 1
    assert calls[0]["tickers"] == "NVDA,AAPL"
    assert articles[0]["symbols"] == ["NVDA", "AAPL"]
    assert articles[0]["provider_sentiment"] == -0.4


def test_breaking_collection_does_not_spend_alpha_quota(monkeypatch, tmp_path):
    config = _config(str(tmp_path / "news.db"), alpha_vantage_key="configured")
    monkeypatch.setattr(intelligence_sources, "fetch_yahoo", lambda *_a, **_kw: [])
    monkeypatch.setattr(
        intelligence_sources,
        "fetch_alpha_vantage",
        lambda *_a, **_kw: (_ for _ in ()).throw(
            AssertionError("breaking poll must skip Alpha Vantage")
        ),
    )

    _articles, health = intelligence_sources.collect_news(
        ["NVDA"], config, include_slow_sources=False
    )

    assert health["alpha_vantage"]["configured"] is True
    assert health["alpha_vantage"]["attempted"] == 0
