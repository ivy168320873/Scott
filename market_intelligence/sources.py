"""Market-news providers with a no-key Yahoo fallback.

Provider responses are normalised into one small, auditable article schema.
The collectors never fabricate articles when a provider fails.
"""

from __future__ import annotations

import hashlib
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import requests

from .config import IntelligenceConfig

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; ScottMarketIntelligence/1.0)",
    "Accept": "application/json",
}
_MACRO_QUERIES = (
    "Federal Reserve inflation interest rates US economy",
    "semiconductor artificial intelligence earnings market",
    "Taiwan stocks economy central bank",
)
_SYMBOL_RE = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,19}$")


def _safe_error(value: object) -> str:
    """Keep provider diagnostics without ever persisting credentials."""
    text = str(value or "")
    text = re.sub(
        r"(?i)([?&](?:token|apikey|api_key|access_key)=)[^&\s]+",
        r"\1[redacted]",
        text,
    )
    text = re.sub(r"(?i)(bearer\s+)[a-z0-9._\-]+", r"\1[redacted]", text)
    return text[:120]


def _clean_url(value: Any) -> str:
    raw = str(value or "").strip()
    try:
        parsed = urlsplit(raw)
    except ValueError:
        return ""
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    # Tracking query strings make otherwise-identical stories look unique.
    query = "&".join(
        part
        for part in parsed.query.split("&")
        if part and not part.lower().startswith(("utm_", "guccounter=", "soc_src="))
    )
    return urlunsplit((parsed.scheme, parsed.netloc.lower(), parsed.path, query, ""))[
        :2000
    ]


def _symbols(values: Any) -> list[str]:
    if isinstance(values, str):
        values = re.split(r"[,\s]+", values)
    if not isinstance(values, list):
        return []
    result = []
    for item in values:
        symbol = str(item or "").upper().strip()
        if _SYMBOL_RE.fullmatch(symbol) and symbol not in result:
            result.append(symbol)
    return result


def _iso_from_epoch(value: Any) -> str:
    try:
        stamp = float(value)
        if stamp > 1_000_000_000_000:
            stamp /= 1000
        return datetime.fromtimestamp(stamp, timezone.utc).isoformat()
    except (TypeError, ValueError, OverflowError, OSError):
        return ""


def _alpha_time(value: Any) -> str:
    raw = str(value or "").strip()
    for fmt in ("%Y%m%dT%H%M%S", "%Y%m%dT%H%M", "%Y%m%d"):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc).isoformat()
        except ValueError:
            continue
    return ""


def _dedupe_key(article: dict) -> str:
    url = _clean_url(article.get("url"))
    if url:
        basis = url.lower()
    else:
        title = re.sub(r"\W+", " ", str(article.get("title") or "").lower()).strip()
        day = str(article.get("published_at") or "")[:10]
        basis = f"{title}|{day}"
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()


def _article(**kwargs) -> dict | None:
    title = re.sub(r"\s+", " ", str(kwargs.get("title") or "")).strip()
    if len(title) < 8:
        return None
    item = {
        "source_provider": str(kwargs.get("source_provider") or "unknown")[:80],
        "publisher": str(kwargs.get("publisher") or "")[:160],
        "title": title[:1000],
        "summary": re.sub(r"\s+", " ", str(kwargs.get("summary") or "")).strip()[:5000],
        "url": _clean_url(kwargs.get("url")),
        "published_at": str(kwargs.get("published_at") or "")[:40],
        "symbols": _symbols(kwargs.get("symbols")),
        "provider_sentiment": kwargs.get("provider_sentiment"),
        "raw": kwargs.get("raw") if isinstance(kwargs.get("raw"), dict) else {},
    }
    item["dedupe_key"] = _dedupe_key(item)
    return item


def _get(session, url: str, *, params: dict, timeout: int = 12):
    getter = session.get if session is not None else requests.get
    response = getter(url, params=params, headers=_HEADERS, timeout=timeout)
    response.raise_for_status()
    return response.json()


def fetch_yahoo(query: str, *, limit: int = 6, session=None) -> list[dict]:
    data = _get(
        session,
        "https://query2.finance.yahoo.com/v1/finance/search",
        params={"q": query, "newsCount": max(1, min(10, limit)), "quotesCount": 0},
    )
    result = []
    for raw in data.get("news", [])[:limit]:
        url = raw.get("link")
        if not url and isinstance(raw.get("clickThroughUrl"), dict):
            url = raw["clickThroughUrl"].get("url")
        item = _article(
            source_provider="Yahoo Finance",
            publisher=raw.get("publisher"),
            title=raw.get("title"),
            summary=raw.get("summary"),
            url=url,
            published_at=_iso_from_epoch(raw.get("providerPublishTime")),
            symbols=raw.get("relatedTickers") or [],
            raw={
                "type": raw.get("type"),
                "uuid": raw.get("uuid"),
            },
        )
        if item:
            result.append(item)
    return result


def fetch_finnhub(
    symbol: str,
    *,
    api_key: str,
    from_date: str,
    to_date: str,
    limit: int = 8,
    session=None,
) -> list[dict]:
    data = _get(
        session,
        "https://finnhub.io/api/v1/company-news",
        params={"symbol": symbol, "from": from_date, "to": to_date, "token": api_key},
    )
    if not isinstance(data, list):
        return []
    result = []
    for raw in data[:limit]:
        related = _symbols(raw.get("related") or symbol)
        if symbol not in related:
            related.insert(0, symbol)
        item = _article(
            source_provider="Finnhub",
            publisher=raw.get("source"),
            title=raw.get("headline"),
            summary=raw.get("summary"),
            url=raw.get("url"),
            published_at=_iso_from_epoch(raw.get("datetime")),
            symbols=related,
            raw={"category": raw.get("category"), "id": raw.get("id")},
        )
        if item:
            result.append(item)
    return result


def fetch_alpha_vantage(
    symbols: list[str] | str,
    *,
    api_key: str,
    limit: int = 8,
    session=None,
) -> list[dict]:
    requested = _symbols(symbols)
    if not requested:
        return []
    data = _get(
        session,
        "https://www.alphavantage.co/query",
        params={
            "function": "NEWS_SENTIMENT",
            "tickers": ",".join(requested),
            "sort": "LATEST",
            "limit": max(1, min(50, limit)),
            "apikey": api_key,
        },
    )
    if data.get("Information") or data.get("Note"):
        raise RuntimeError("Alpha Vantage rate limit or API access error")
    result = []
    for raw in data.get("feed", [])[:limit]:
        tickers = []
        selected_scores = []
        for ticker in raw.get("ticker_sentiment", []) or []:
            ticker_symbol = str(ticker.get("ticker") or "").upper().strip()
            if _SYMBOL_RE.fullmatch(ticker_symbol):
                tickers.append(ticker_symbol)
            if ticker_symbol in requested:
                try:
                    selected_scores.append(float(ticker.get("ticker_sentiment_score")))
                except (TypeError, ValueError):
                    pass
        try:
            overall = float(raw.get("overall_sentiment_score"))
        except (TypeError, ValueError):
            overall = None
        item = _article(
            source_provider="Alpha Vantage",
            publisher=raw.get("source"),
            title=raw.get("title"),
            summary=raw.get("summary"),
            url=raw.get("url"),
            published_at=_alpha_time(raw.get("time_published")),
            symbols=tickers or requested,
            provider_sentiment=(
                max(selected_scores, key=abs) if selected_scores else overall
            ),
            raw={"topics": raw.get("topics") or []},
        )
        if item:
            result.append(item)
    return result


def _is_recent(article: dict, cutoff: datetime) -> bool:
    raw = str(article.get("published_at") or "")
    if not raw:
        return True
    try:
        published = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)
        return published >= cutoff
    except ValueError:
        return True


def collect_news(
    symbols: list[str],
    config: IntelligenceConfig,
    *,
    session=None,
    now: datetime | None = None,
    include_slow_sources: bool = True,
) -> tuple[list[dict], dict]:
    """Collect and deduplicate news from configured providers."""
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=config.lookback_hours)
    symbols = _symbols(symbols)[: config.max_symbols]
    provider_symbols = symbols[:12]

    jobs: list[tuple[str, str, object]] = []
    for query in _MACRO_QUERIES:
        jobs.append(
            ("yahoo", query, lambda q=query: fetch_yahoo(q, limit=5, session=session))
        )
    for symbol in provider_symbols:
        jobs.append(
            ("yahoo", symbol, lambda s=symbol: fetch_yahoo(s, limit=5, session=session))
        )

    from_date = cutoff.date().isoformat()
    to_date = now.date().isoformat()
    if config.finnhub_key:
        for symbol in provider_symbols:
            jobs.append(
                (
                    "finnhub",
                    symbol,
                    lambda s=symbol: fetch_finnhub(
                        s,
                        api_key=config.finnhub_key,
                        from_date=from_date,
                        to_date=to_date,
                        session=session,
                    ),
                )
            )
    if config.alpha_vantage_key and include_slow_sources and provider_symbols:
        # NEWS_SENTIMENT accepts multiple tickers. Use one daily/manual request
        # instead of spending scarce free-plan quota on every breaking poll.
        alpha_symbols = provider_symbols[:5]
        jobs.append(
            (
                "alpha_vantage",
                ",".join(alpha_symbols),
                lambda: fetch_alpha_vantage(
                    alpha_symbols,
                    api_key=config.alpha_vantage_key,
                    limit=min(20, config.max_articles),
                    session=session,
                ),
            )
        )

    health = {
        "yahoo": {"configured": True, "attempted": 0, "succeeded": 0, "errors": []},
        "finnhub": {
            "configured": bool(config.finnhub_key),
            "attempted": 0,
            "succeeded": 0,
            "errors": [],
        },
        "alpha_vantage": {
            "configured": bool(config.alpha_vantage_key),
            "attempted": 0,
            "succeeded": 0,
            "errors": [],
        },
    }
    collected: list[dict] = []
    with ThreadPoolExecutor(max_workers=min(8, max(1, len(jobs)))) as pool:
        pending = {}
        for provider, label, fn in jobs:
            health[provider]["attempted"] += 1
            pending[pool.submit(fn)] = (provider, label)
        for future in as_completed(pending):
            provider, label = pending[future]
            try:
                items = future.result()
                health[provider]["succeeded"] += 1
                collected.extend(items)
            except Exception as exc:  # noqa: BLE001 - isolate independent providers
                if len(health[provider]["errors"]) < 3:
                    health[provider]["errors"].append(f"{label}: {_safe_error(exc)}")

    deduped: dict[str, dict] = {}
    for article in collected:
        if not _is_recent(article, cutoff):
            continue
        key = article["dedupe_key"]
        if key in deduped:
            merged = deduped[key]
            merged["symbols"] = _symbols(
                (merged.get("symbols") or []) + (article.get("symbols") or [])
            )
            if len(article.get("summary") or "") > len(merged.get("summary") or ""):
                merged["summary"] = article["summary"]
            providers = set(
                merged.get("corroborating_providers") or [merged["source_provider"]]
            )
            providers.add(article["source_provider"])
            merged["corroborating_providers"] = sorted(providers)
        else:
            article["corroborating_providers"] = [article["source_provider"]]
            deduped[key] = article

    articles = sorted(
        deduped.values(),
        key=lambda item: item.get("published_at") or "",
        reverse=True,
    )[: config.max_articles]
    return articles, health
