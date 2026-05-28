"""
Enhanced Content Fetcher for the Analyst Debate Engine.

Strategy (in order of preference):
  1. Claude Web Search (primary) — uses Anthropic's servers, bypasses all network blocks,
     returns deeply processed, structured content.
  2. Direct scraping (fallback) — Yahoo Finance, Anue, YouTube RSS, article extraction.

Each fetch also runs a Claude "extraction pass" that distils raw content into the
structured key-point format the debate engine works best with.
"""
from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Optional

import requests

# ── Optional youtube-transcript-api ──────────────────────────────────────────
try:
    from youtube_transcript_api import YouTubeTranscriptApi
    _HAS_YT_TRANSCRIPT = True
except ImportError:
    _HAS_YT_TRANSCRIPT = False

# ── Constants ─────────────────────────────────────────────────────────────────

_GOOAYE_YT_CHANNEL = "UC23rnlQU_qE3cec9x709peA"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8",
    "Accept": "application/json, text/xml, */*",
}

# ── Singleton Anthropic client ────────────────────────────────────────────────

_client = None

def _get_client():
    global _client
    if _client is None:
        key = os.environ.get("ANTHROPIC_API_KEY", "")
        if not key:
            return None
        from anthropic import Anthropic
        _client = Anthropic(api_key=key)
    return _client


# ══════════════════════════════════════════════════════════════════════════════
# PRIMARY: Claude Web Search
# ══════════════════════════════════════════════════════════════════════════════

_PODCAST_SEARCH_PROMPT = """\
請用網路搜尋功能，找出以下資訊並整理成結構化重點：

1. 謝孟恭 Gooaye「股癌」Podcast 最新 3 集的核心內容
   - 每集的標題、集數編號、發佈日期
   - 該集的主要討論主題（美股/台股/總經/個股）
   - 提到的關鍵觀點、數據、看法
   - 謝孟恭對市場的整體看法（偏多/偏空/觀望）

請盡量找到最新的集數內容，以 2026 年的最新資訊為優先。
輸出格式：按集數分段，每段包含日期、標題、核心觀點。\
"""

_INDUSTRY_SEARCH_PROMPT = """\
請用網路搜尋功能，針對以下主題找出最新、最完整的產業分析資訊：

研究主題：{topic}

需要蒐集的資訊：
1. 最新產業動態與重大事件（近 2-4 週）
2. 主要公司財報/法說會重點
3. 供需狀況與庫存週期
4. 分析師評等與目標價調整
5. 總體經濟影響（Fed/利率/匯率）
6. 競爭格局與市占率變化

請給出結構化的重點條列，每點附上資料來源與日期。\
"""

_EXTRACTION_PROMPT = """\
你是資深金融分析助理。請將以下原始資料整理成結構化的投資分析摘要，
供三位 AI 分析師進行辯論使用。

原始資料：
{raw_content}

請輸出：

## 📊 核心數據與事實（5-8 條，每條附日期）

## 🔑 關鍵市場訊號（正面/負面各 2-3 條）

## 💡 謝孟恭觀點摘要（如有相關資料）

## ⚠️ 主要風險因素（2-3 條）

## 📅 時間線（近期重要事件依時序排列）

請用繁體中文，條列清晰，聚焦在對投資決策最有影響的資訊。\
"""


def _claude_web_search(query: str, client, max_tokens: int = 2500) -> str:
    """Run a single Claude web-search query, return combined text output."""
    try:
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=max_tokens,
            tools=[{
                "type": "web_search_20250305",
                "name": "web_search",
                "max_uses": 5,
            }],
            messages=[{"role": "user", "content": query}],
        )
        texts = [b.text for b in resp.content if hasattr(b, "text") and b.text]
        return "\n\n".join(texts)
    except Exception as exc:
        return f"[搜尋失敗: {exc}]"


def _claude_extract(raw: str, client) -> str:
    """Pass raw fetched content through Claude for structured extraction."""
    if not raw or not raw.strip():
        return raw
    try:
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1800,
            system=(
                "你是資深金融分析助理，專門整理財經資料。"
                "請忠實呈現原始資料中的事實，不要加入個人看法。"
            ),
            messages=[{
                "role": "user",
                "content": _EXTRACTION_PROMPT.format(raw_content=raw[:4000]),
            }],
        )
        return resp.content[0].text if resp.content else raw
    except Exception:
        return raw


def fetch_with_claude_search(topic: str) -> dict:
    """
    Primary fetch: use Claude's web search for both podcast and industry content.
    Returns processed, structured content ready for the debate engine.
    """
    client = _get_client()
    if client is None:
        return {"ok": False, "error": "ANTHROPIC_API_KEY 未設定", "report": "", "podcast": ""}

    # Run both searches
    industry_raw = _claude_web_search(
        _INDUSTRY_SEARCH_PROMPT.format(topic=topic), client, max_tokens=2500
    )
    podcast_raw = _claude_web_search(_PODCAST_SEARCH_PROMPT, client, max_tokens=2000)

    # Extraction pass: structure and clean up
    report_structured  = _claude_extract(industry_raw,  client)
    podcast_structured = _claude_extract(podcast_raw,   client)

    return {
        "ok":           True,
        "method":       "claude_websearch",
        "report":       report_structured,
        "podcast":      podcast_structured,
        "report_ok":    bool(report_structured and "失敗" not in report_structured[:20]),
        "podcast_ok":   bool(podcast_structured and "失敗" not in podcast_structured[:20]),
        "report_error": "",
        "podcast_error": "",
        "news_count":   "Claude 搜尋",
        "podcast_items": [],
    }


# ══════════════════════════════════════════════════════════════════════════════
# FALLBACK: Direct Scraping
# ══════════════════════════════════════════════════════════════════════════════

# ── YouTube RSS ───────────────────────────────────────────────────────────────

def _fetch_yt_description_full(video_id: str) -> str:
    """Scrape full description from YouTube video page (ytInitialData)."""
    try:
        r = requests.get(
            f"https://www.youtube.com/watch?v={video_id}",
            headers=_HEADERS, timeout=10,
        )
        if r.status_code != 200:
            return ""
        # shortDescription contains the full description as a JSON-escaped string
        m = re.search(r'"shortDescription":"((?:[^"\\]|\\.)*)"', r.text)
        if m:
            desc = m.group(1).replace("\\n", "\n").replace('\\"', '"').replace("\\\\", "\\")
            return desc[:2500].strip()
        # Fallback: og:description
        m2 = re.search(r'<meta name="description" content="([^"]*)"', r.text)
        return m2.group(1)[:600] if m2 else ""
    except Exception:
        return ""


def _fetch_yt_transcript(video_id: str) -> str:
    """Get YouTube auto-captions via youtube-transcript-api."""
    if not _HAS_YT_TRANSCRIPT:
        return ""
    try:
        for langs in [["zh-TW", "zh-Hant"], ["zh", "zh-Hans"], None]:
            try:
                if langs:
                    segs = YouTubeTranscriptApi.get_transcript(video_id, languages=langs)
                else:
                    tlist = YouTubeTranscriptApi.list_transcripts(video_id)
                    segs  = next(iter(tlist)).fetch()
                text = " ".join(s["text"] for s in segs)
                return text[:3500].strip()
            except Exception:
                continue
        return ""
    except Exception:
        return ""


def _fetch_gooaye_yt_rss(n: int = 4) -> dict:
    """Fetch Gooaye YouTube RSS and extract deep per-video content."""
    url = f"https://www.youtube.com/feeds/videos.xml?channel_id={_GOOAYE_YT_CHANNEL}"
    try:
        r = requests.get(url, headers=_HEADERS, timeout=12)
        if r.status_code != 200:
            return {"ok": False, "error": f"RSS HTTP {r.status_code}", "content": "", "items": []}

        ns = {
            "atom":  "http://www.w3.org/2005/Atom",
            "yt":    "http://www.youtube.com/xml/schemas/2015",
            "media": "http://search.yahoo.com/mrss/",
        }
        root  = ET.fromstring(r.content)
        items = []
        for entry in root.findall("atom:entry", ns)[:n]:
            title  = (entry.findtext("atom:title", "", ns) or "").strip()
            pub    = (entry.findtext("atom:published", "", ns) or "")[:10]
            vid_id = (entry.findtext("yt:videoId", "", ns) or "").strip()
            desc_el = entry.find(".//media:description", ns)
            short_desc = ((desc_el.text or "").strip()[:300]) if desc_el is not None else ""

            # Try to get full description from video page
            full_desc = _fetch_yt_description_full(vid_id) if vid_id else ""
            # Try transcript
            transcript = _fetch_yt_transcript(vid_id) if vid_id else ""

            items.append({
                "title":      title,
                "date":       pub,
                "video_id":   vid_id,
                "description": full_desc or short_desc,
                "transcript": transcript,
            })

        if not items:
            return {"ok": False, "error": "RSS 無集數", "content": "", "items": []}

        lines = ["=== 謝孟恭 Gooaye 股癌 最新集數 ===\n"]
        for v in items:
            lines.append(f"【{v['date']}】{v['title']}")
            if v["description"]:
                lines.append(f"節目說明：\n{v['description'][:800]}")
            if v["transcript"]:
                lines.append(f"逐字稿摘錄（前 1500 字）：\n{v['transcript'][:1500]}")
            if v["video_id"]:
                lines.append(f"連結：https://youtu.be/{v['video_id']}")
            lines.append("---")

        return {"ok": True, "content": "\n".join(lines), "items": items}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "content": "", "items": []}


# ── Yahoo Finance ─────────────────────────────────────────────────────────────

def _fetch_article_text(url: str) -> str:
    """Extract main text content from a news article URL."""
    if not url or not url.startswith("http"):
        return ""
    try:
        r = requests.get(url, headers=_HEADERS, timeout=8, allow_redirects=True)
        if r.status_code != 200:
            return ""
        html = r.text
        # Strip scripts and styles
        html = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.DOTALL)
        # Extract paragraphs
        paras = re.findall(r"<p[^>]*>(.*?)</p>", html, re.DOTALL)
        texts = []
        for p in paras[:20]:
            clean = re.sub(r"<[^>]+>", "", p).strip()
            if len(clean) > 80:
                texts.append(clean)
        return "\n".join(texts[:10])[:1200]
    except Exception:
        return ""


def _fetch_yahoo_news(topic: str, n: int = 12) -> dict:
    """Yahoo Finance news API with full article text extraction."""
    queries = [topic, f"{topic} earnings outlook", f"{topic} supply chain"]
    seen:    set[str]  = set()
    results: list[dict] = []

    for q in queries[:3]:
        try:
            r = requests.get(
                "https://query1.finance.yahoo.com/v1/finance/search",
                params={"q": q, "newsCount": 8, "quotesCount": 0},
                headers=_HEADERS, timeout=8,
            )
            if r.status_code != 200:
                continue
            for item in r.json().get("news", [])[:8]:
                title = item.get("title", "")
                if not title or title in seen:
                    continue
                seen.add(title)
                ts   = item.get("providerPublishTime", 0)
                date = (
                    datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
                    if ts else ""
                )
                art_url = item.get("link", "")
                results.append({"title": title, "date": date,
                                 "publisher": item.get("publisher", ""),
                                 "url": art_url})
        except Exception:
            continue

    results.sort(key=lambda x: x.get("date", ""), reverse=True)
    results = results[:n]
    if not results:
        return {"ok": False, "error": "Yahoo Finance 無資料", "content": ""}

    lines = [f"=== {topic} 產業新聞 ===\n"]
    for item in results:
        lines.append(f"[{item['date']}] {item['title']}  — {item['publisher']}")
        body = _fetch_article_text(item["url"])
        if body:
            lines.append(f"  內容摘要：{body[:400]}")
        lines.append("")

    return {"ok": True, "content": "\n".join(lines), "count": len(results)}


# ── Anue 鉅亨網 ───────────────────────────────────────────────────────────────

def _fetch_anue_news(n: int = 8) -> dict:
    """Anue 鉅亨網 Taiwan stock news (open API)."""
    try:
        r = requests.get(
            "https://api.cnyes.com/media/api/v1/newslist/category/tw-stock",
            params={"limit": n, "page": 1},
            headers=_HEADERS, timeout=8,
        )
        if r.status_code != 200:
            return {"ok": False, "error": f"Anue HTTP {r.status_code}", "content": ""}

        items = r.json().get("items", {}).get("data", [])
        if not items:
            return {"ok": False, "error": "Anue 無資料", "content": ""}

        lines = ["\n=== 鉅亨網台股最新新聞 ===\n"]
        for it in items[:n]:
            title   = it.get("title", "")
            summary = (it.get("summary") or "")[:300]
            ts      = it.get("publishAt", 0)
            date    = (
                datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
                if ts else ""
            )
            lines.append(f"[{date}] {title}")
            if summary:
                lines.append(f"  {summary}")
        return {"ok": True, "content": "\n".join(lines)}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "content": ""}


# ── Macro Context ─────────────────────────────────────────────────────────────

def _fetch_macro_context() -> dict:
    """Broad US + TW macro headlines from Yahoo Finance."""
    seen: set[str] = set()
    lines = ["\n=== 宏觀市場背景 ===\n"]
    for q in ["S&P 500 Federal Reserve", "Taiwan TSMC tech sector"]:
        try:
            r = requests.get(
                "https://query1.finance.yahoo.com/v1/finance/search",
                params={"q": q, "newsCount": 5, "quotesCount": 0},
                headers=_HEADERS, timeout=8,
            )
            if r.status_code != 200:
                continue
            for it in r.json().get("news", [])[:5]:
                t = it.get("title", "")
                if t and t not in seen:
                    seen.add(t)
                    ts   = it.get("providerPublishTime", 0)
                    date = (
                        datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
                        if ts else ""
                    )
                    lines.append(f"[{date}] {t}")
        except Exception:
            continue
    return {"ok": len(lines) > 2, "content": "\n".join(lines)}


def fetch_direct(topic: str) -> dict:
    """
    Fallback fetch: direct scraping from Yahoo Finance, Anue, YouTube RSS.
    """
    news  = _fetch_yahoo_news(topic, n=12)
    macro = _fetch_macro_context()
    yt    = _fetch_gooaye_yt_rss(n=4)
    anue  = _fetch_anue_news(n=8)

    report_parts = []
    if news["ok"]:    report_parts.append(news["content"])
    if anue["ok"]:    report_parts.append(anue["content"])
    if macro["ok"]:   report_parts.append(macro["content"])

    return {
        "ok":           True,
        "method":       "direct_scrape",
        "report":       "\n\n".join(report_parts) or "（直接抓取無結果）",
        "podcast":      yt.get("content", ""),
        "report_ok":    news["ok"] or anue["ok"],
        "podcast_ok":   yt["ok"],
        "report_error": news.get("error", "") if not news["ok"] else "",
        "podcast_error": yt.get("error", "")   if not yt["ok"]   else "",
        "news_count":   news.get("count", 0),
        "podcast_items": yt.get("items", []),
    }


# ══════════════════════════════════════════════════════════════════════════════
# MAIN ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

def fetch_all(topic: str = "半導體 AI 科技") -> dict:
    """
    Fetch industry reports + Gooaye podcast, using the best available method.

    Priority:
      1. Claude web search (requires ANTHROPIC_API_KEY — most complete, always current)
      2. Direct scraping (no API key needed — depends on network accessibility)
    """
    client = _get_client()

    if client is not None:
        # Try Claude web search first
        result = fetch_with_claude_search(topic)
        if result.get("ok") and (result.get("report") or result.get("podcast")):
            return result
        # If Claude search failed, fall through to direct scraping

    return fetch_direct(topic)
