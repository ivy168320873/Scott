"""
Trump Truth Social Stock Signal Tracker

Fetches Trump's latest Truth Social posts and extracts stock/company
mentions with sentiment analysis using Claude AI.

Flow:
  1. Try Truth Social RSS feed directly
  2. Fall back to Claude web_search if RSS blocked
  3. Run Claude extraction pass to parse tickers / sentiment
"""
from __future__ import annotations

import json
import os
import re
import xml.etree.ElementTree as ET

import requests

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/rss+xml, application/xml, text/xml, */*",
    "Accept-Language": "en-US,en;q=0.9",
}

# ── RSS Fetch ─────────────────────────────────────────────────────────────────

def _fetch_truth_social_rss(n: int = 15) -> dict:
    """Attempt to fetch Trump's Truth Social RSS feed."""
    urls = [
        "https://truthsocial.com/@realDonaldTrump.rss",
        "https://rss.app/feeds/trump.xml",          # common aggregator
    ]
    for url in urls:
        try:
            r = requests.get(url, headers=_HEADERS, timeout=10)
            if r.status_code != 200:
                continue
            root = ET.fromstring(r.content)
            ch   = root.find("channel")
            items = (ch or root).findall("item")
            posts = []
            for it in items[:n]:
                title = (it.findtext("title") or "").strip()
                desc  = (it.findtext("description") or "").strip()
                pub   = (it.findtext("pubDate") or "")[:16].strip()
                link  = (it.findtext("link") or "").strip()
                text  = re.sub(r"<[^>]+>", "", desc or title).strip()
                if text:
                    posts.append({"text": text[:600], "date": pub, "url": link})
            if posts:
                return {"ok": True, "posts": posts, "source": "rss"}
        except Exception:
            continue
    return {"ok": False, "posts": [], "source": "rss", "error": "RSS unavailable"}


# ── Claude Web Search Fallback ────────────────────────────────────────────────

def _fetch_via_claude_search(client, n: int = 10) -> dict:
    """Use Claude web_search to find recent Trump posts mentioning stocks."""
    try:
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=2500,
            tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 5}],
            messages=[{"role": "user", "content": (
                "Please search for Donald Trump's most recent Truth Social posts "
                "(last 2 weeks) that mention stocks, companies, industries, or trade policies. "
                "Find at least 10 posts. For each post include: exact date, full text, "
                "any company or ticker mentioned. Focus on: tariffs, energy companies, "
                "defense, tech companies, pharma, trade deals, manufacturing."
            )}],
        )
        texts = [b.text for b in resp.content if hasattr(b, "text") and b.text]
        raw = "\n\n".join(texts)
        return {
            "ok": True,
            "posts": [{"text": raw[:3000], "date": "", "url": "", "_is_summary": True}],
            "source": "claude_search",
            "raw_summary": raw,
        }
    except Exception as exc:
        return {"ok": False, "posts": [], "error": str(exc), "source": "claude_search"}


# ── Signal Extraction ─────────────────────────────────────────────────────────

_EXTRACT_PROMPT = """\
你是一位專業的川普政策股市影響分析師，擅長解讀川普言論對個股的短中長期影響。
請分析以下川普 Truth Social 貼文（或相關報導），提取所有對股票有影響的訊號並進行深度分析。

貼文內容：
{posts}

請輸出 JSON 陣列（只輸出純 JSON，不要 markdown code block，不要任何說明文字）：
[
  {{
    "ticker":        "股票代碼（如 TSLA）或 null",
    "company":       "公司名稱（繁體中文）",
    "sector":        "產業：科技/能源/國防/金融/製藥/汽車/鋼鐵/電商/其他",
    "sentiment":     "bullish / bearish / neutral",
    "signal":        "endorsement / tariff_threat / policy_benefit / deal / sanction / warning / mention",
    "context":       "貼文重點（繁體中文，40字以內）",
    "confidence":    1到10的整數（此訊號可靠性）,
    "price_up_prob": 0到100的整數（股價上漲機率 %，bullish=60-90，bearish=10-40，neutral=45-55）,
    "reasoning":     "為何此訊號影響股價的分析（繁體中文，60字以內，包含邏輯鏈：川普此言論→政策效果→產業影響→股價方向）",
    "time_horizon":  "短期（1週）/ 中期（1個月）/ 長期（3個月+）",
    "history_note":  "歷史上類似川普訊號的市場反應（繁體中文，30字以內，例如：2018年鋼鐵關稅後X鋼鐵股漲20%）"
  }}
]

分析規則：
- 只列出明確提及的公司、股票或受影響產業，不要憑空猜測
- tariff_threat = 加關稅威脅 → bearish，price_up_prob 15-35
- policy_benefit = 能源出口許可/製造業回流/減稅 → bullish，price_up_prob 60-80
- endorsement = 川普明確稱讚或背書 → bullish，price_up_prob 65-85
- deal = 商業協議/投資協議 → bullish，price_up_prob 60-75
- sanction = 制裁/黑名單 → bearish，price_up_prob 5-25
- warning = 負面點名（boycott/fake/bad）→ bearish，price_up_prob 10-30
- mention = 中性提及 → neutral，price_up_prob 48-55
- 如果完全沒有明確公司/股票，回傳空陣列 []
- history_note 若無相關歷史資料，填「歷史案例待查」
"""


def _extract_signals(posts: list, client) -> list:
    """Run Claude extraction to parse tickers and sentiment from post texts."""
    if not posts:
        return []
    combined = "\n\n---\n\n".join(
        f"[{p.get('date','')}] {p['text'][:500]}" for p in posts[:12]
    )
    try:
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=2500,
            messages=[{"role": "user", "content": _EXTRACT_PROMPT.format(posts=combined)}],
        )
        raw = resp.content[0].text if resp.content else "[]"
        m = re.search(r"\[.*\]", raw, re.DOTALL)
        if m:
            signals = json.loads(m.group(0))
            return [s for s in signals if s.get("company")]
    except Exception:
        pass
    return []


# ── Main Entry Point ──────────────────────────────────────────────────────────

def fetch_and_analyze() -> dict:
    """
    Fetch Trump's Truth Social posts and extract stock signals.
    Returns structured result ready for the API endpoint.
    """
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    client = None
    if key:
        from anthropic import Anthropic
        client = Anthropic(api_key=key)

    # Step 1: Get posts
    result = _fetch_truth_social_rss(n=15)
    if not result["ok"] or not result["posts"]:
        if client:
            result = _fetch_via_claude_search(client, n=10)
        else:
            return {
                "ok": False,
                "error": "RSS unavailable and ANTHROPIC_API_KEY not set",
                "signals": [], "posts": [], "source": "none",
            }

    # Step 2: Extract signals
    signals = []
    if client and result.get("posts"):
        signals = _extract_signals(result["posts"], client)

    return {
        "ok":           True,
        "source":       result.get("source", "unknown"),
        "post_count":   len(result.get("posts", [])),
        "signal_count": len(signals),
        "signals":      signals,
        "raw_summary":  result.get("raw_summary", ""),
        "posts":        [
            {"text": p["text"][:300], "date": p.get("date",""), "url": p.get("url","")}
            for p in result.get("posts", [])
            if not p.get("_is_summary")
        ][:8],
    }
