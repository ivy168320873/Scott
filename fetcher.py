"""
Auto-fetcher for the Analyst Debate Engine.

Sources:
  1. 謝孟恭 Gooaye 股癌 Podcast  ── YouTube RSS feed (channel UC23rnlQU_qE3cec9x709peA)
  2. Industry / sector news       ── Yahoo Finance search API
  3. Broad market context         ── Yahoo Finance macro news
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import requests

# ── Constants ─────────────────────────────────────────────────────────────────

_GOOAYE_YT_CHANNEL = "UC23rnlQU_qE3cec9x709peA"   # 謝孟恭 YouTube channel ID

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8",
    "Accept": "application/json, text/plain, */*",
}

_YT_NS = {
    "atom":  "http://www.w3.org/2005/Atom",
    "yt":    "http://www.youtube.com/xml/schemas/2015",
    "media": "http://search.yahoo.com/mrss/",
}

# ── 1. 謝孟恭 Gooaye Podcast (YouTube RSS) ────────────────────────────────────

def fetch_gooaye_podcast(n: int = 5) -> dict:
    """
    Fetch latest N videos from 謝孟恭's Gooaye YouTube channel via RSS.
    Returns formatted text ready for LLM consumption.
    """
    url = f"https://www.youtube.com/feeds/videos.xml?channel_id={_GOOAYE_YT_CHANNEL}"
    try:
        r = requests.get(url, headers=_HEADERS, timeout=12)
        if r.status_code != 200:
            return {
                "ok": False,
                "error": f"YouTube RSS 回應 HTTP {r.status_code}",
                "content": "", "items": [],
            }

        root  = ET.fromstring(r.content)
        items = []
        for entry in root.findall("atom:entry", _YT_NS)[:n]:
            title   = (entry.findtext("atom:title",     "", _YT_NS) or "").strip()
            pub     = (entry.findtext("atom:published",  "", _YT_NS) or "")[:10]
            vid_id  = (entry.findtext("yt:videoId",      "", _YT_NS) or "").strip()
            desc_el = entry.find(".//media:description", _YT_NS)
            desc    = ((desc_el.text or "").strip()[:600]) if desc_el is not None else ""
            items.append({"title": title, "date": pub, "description": desc, "video_id": vid_id})

        if not items:
            return {"ok": False, "error": "RSS feed 無集數資料", "content": "", "items": []}

        lines = ["=== 謝孟恭 Gooaye 股癌 最新 Podcast 集數 ===\n"]
        for v in items:
            lines.append(f"【{v['date']}】{v['title']}")
            if v["description"]:
                lines.append(f"  節目說明：{v['description'][:450]}")
            if v["video_id"]:
                lines.append(f"  YouTube: https://youtu.be/{v['video_id']}")
            lines.append("")

        return {
            "ok":      True,
            "content": "\n".join(lines),
            "items":   items,
            "source":  "YouTube RSS",
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc), "content": "", "items": []}


# ── 2. Industry / Sector News (Yahoo Finance) ─────────────────────────────────

def fetch_industry_news(topic: str, n: int = 12) -> dict:
    """
    Fetch recent financial news for a given topic/sector via Yahoo Finance.
    Runs up to 3 query variants and deduplicates results.
    """
    base = topic.strip() or "半導體 AI 科技"
    # Build variant queries for broader coverage
    queries = [base]
    if not any(kw in base.lower() for kw in ["market", "stock", "economy", "股市"]):
        queries.append(f"{base} stock outlook")
        queries.append(f"{base} industry trend")

    seen:    set[str] = set()
    results: list[dict] = []

    for q in queries[:3]:
        try:
            resp = requests.get(
                "https://query1.finance.yahoo.com/v1/finance/search",
                params={"q": q, "newsCount": 8, "quotesCount": 0},
                headers=_HEADERS,
                timeout=8,
            )
            if resp.status_code != 200:
                continue
            for item in resp.json().get("news", [])[:8]:
                title = item.get("title", "")
                if not title or title in seen:
                    continue
                seen.add(title)
                ts   = item.get("providerPublishTime", 0)
                date = (
                    datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
                    if ts else ""
                )
                results.append({
                    "title":     title,
                    "publisher": item.get("publisher", ""),
                    "date":      date,
                })
        except Exception:
            continue

    results.sort(key=lambda x: x.get("date", ""), reverse=True)
    results = results[:n]

    if not results:
        return {"ok": False, "error": "Yahoo Finance 無相關新聞", "content": ""}

    lines = [f"=== {base} 最新產業新聞 ===\n"]
    for item in results:
        lines.append(f"[{item['date']}] {item['title']}  ─ {item['publisher']}")

    return {"ok": True, "content": "\n".join(lines), "count": len(results)}


# ── 3. Macro / Market Context ─────────────────────────────────────────────────

def fetch_market_context(n: int = 6) -> dict:
    """Fetch broad US + TW macro context headlines."""
    queries = [
        "S&P 500 Federal Reserve interest rate economy",
        "Taiwan stock TSMC tech earnings",
    ]
    seen:  set[str]  = set()
    lines: list[str] = ["\n=== 宏觀市場背景 ===\n"]

    for q in queries:
        try:
            resp = requests.get(
                "https://query1.finance.yahoo.com/v1/finance/search",
                params={"q": q, "newsCount": n, "quotesCount": 0},
                headers=_HEADERS,
                timeout=8,
            )
            if resp.status_code != 200:
                continue
            for it in resp.json().get("news", [])[:n]:
                title = it.get("title", "")
                if not title or title in seen:
                    continue
                seen.add(title)
                ts   = it.get("providerPublishTime", 0)
                date = (
                    datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
                    if ts else ""
                )
                lines.append(f"[{date}] {title}")
        except Exception:
            continue

    if len(lines) <= 2:
        return {"ok": False, "content": ""}

    return {"ok": True, "content": "\n".join(lines)}


# ── 4. Combined Entry Point ───────────────────────────────────────────────────

def fetch_all(topic: str = "半導體 AI 科技", n_news: int = 12, n_podcast: int = 5) -> dict:
    """
    Main entry: fetch industry news + macro context + Gooaye podcast.
    Returns content strings ready to feed the debate engine.
    """
    news    = fetch_industry_news(topic, n_news)
    macro   = fetch_market_context(n=6)
    podcast = fetch_gooaye_podcast(n_podcast)

    report_parts: list[str] = []
    if news["ok"] and news["content"]:
        report_parts.append(news["content"])
    if macro["ok"] and macro["content"]:
        report_parts.append(macro["content"])

    return {
        "report":        "\n\n".join(report_parts),
        "podcast":       podcast.get("content", ""),
        "report_ok":     news["ok"],
        "podcast_ok":    podcast["ok"],
        "report_error":  news.get("error", ""),
        "podcast_error": podcast.get("error", ""),
        "podcast_items": podcast.get("items", []),
        "news_count":    news.get("count", 0),
        "topic":         topic,
    }
