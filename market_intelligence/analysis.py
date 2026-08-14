"""Grounded, explainable news analysis and catalyst aggregation."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from typing import Any

_POSITIVE = {
    "beat",
    "beats",
    "surge",
    "record",
    "upgrade",
    "raises guidance",
    "approval",
    "contract",
    "order",
    "partnership",
    "growth",
    "profit",
    "buyback",
    "dividend",
    "勝預期",
    "上調",
    "成長",
    "獲利",
    "訂單",
    "合作",
    "核准",
    "回購",
    "創高",
}
_NEGATIVE = {
    "miss",
    "misses",
    "plunge",
    "downgrade",
    "cuts guidance",
    "lawsuit",
    "probe",
    "investigation",
    "offering",
    "dilution",
    "bankruptcy",
    "recall",
    "layoff",
    "sanction",
    "遜預期",
    "下調",
    "虧損",
    "調查",
    "訴訟",
    "增資",
    "稀釋",
    "破產",
    "裁員",
    "制裁",
}
_HIGH_IMPACT = {
    "earnings": 18,
    "guidance": 18,
    "revenue": 10,
    "eps": 12,
    "merger": 22,
    "acquisition": 22,
    "bankruptcy": 30,
    "offering": 16,
    "federal reserve": 18,
    "inflation": 14,
    "cpi": 16,
    "jobs report": 14,
    "rate hike": 18,
    "rate cut": 18,
    "tariff": 16,
    "sanction": 18,
    "財報": 18,
    "財測": 18,
    "營收": 10,
    "併購": 22,
    "破產": 30,
    "增資": 16,
    "聯準會": 18,
    "通膨": 14,
    "升息": 18,
    "降息": 18,
    "關稅": 16,
}
_DIRECTION = {"BULLISH", "BEARISH", "NEUTRAL", "MIXED"}
_HORIZON = {"INTRADAY", "DAYS", "WEEKS", "LONG_TERM"}


def _clamp(value: Any, minimum: int = 0, maximum: int = 100, default: int = 50) -> int:
    try:
        number = round(float(value))
    except (TypeError, ValueError, OverflowError):
        number = default
    return max(minimum, min(maximum, number))


def _contains_cjk(value: str) -> bool:
    return bool(re.search(r"[\u3400-\u9fff]", value or ""))


def _age_hours(published_at: str, now: datetime) -> float:
    try:
        published = datetime.fromisoformat(str(published_at).replace("Z", "+00:00"))
        if published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)
        return max(0.0, (now - published).total_seconds() / 3600)
    except (TypeError, ValueError):
        return 24.0


def heuristic_analysis(
    article: dict,
    *,
    holding_symbols: set[str],
    watchlist_symbols: set[str],
    now: datetime | None = None,
) -> dict:
    now = now or datetime.now(timezone.utc)
    text = f"{article.get('title', '')} {article.get('summary', '')}".lower()
    symbols = [str(s).upper() for s in article.get("symbols") or []]
    holdings_hit = [s for s in symbols if s in holding_symbols]
    watchlist_hit = [s for s in symbols if s in watchlist_symbols]

    positive_hits = sum(1 for word in _POSITIVE if word in text)
    negative_hits = sum(1 for word in _NEGATIVE if word in text)
    provider_sentiment = article.get("provider_sentiment")
    raw_meta = article.get("raw") if isinstance(article.get("raw"), dict) else {}
    filing_risk = str(raw_meta.get("risk_category") or "")
    try:
        provider_value = float(provider_sentiment)
        if provider_value >= 0.15:
            positive_hits += 1
        elif provider_value <= -0.15:
            negative_hits += 1
    except (TypeError, ValueError):
        provider_value = None

    if positive_hits and negative_hits:
        direction = "MIXED"
        direction_sign = 0
    elif positive_hits > negative_hits:
        direction = "BULLISH"
        direction_sign = 1
    elif negative_hits > positive_hits:
        direction = "BEARISH"
        direction_sign = -1
    else:
        direction = "NEUTRAL"
        direction_sign = 0
    if filing_risk == "DILUTION_RISK":
        direction = "BEARISH"
        direction_sign = -1

    importance = 36
    for keyword, boost in _HIGH_IMPACT.items():
        if keyword in text:
            importance += boost
    if holdings_hit:
        importance += 14
    elif watchlist_hit:
        importance += 8
    if len(article.get("corroborating_providers") or []) > 1:
        importance += 8
    if filing_risk == "DILUTION_RISK":
        importance += 24
    elif filing_risk in {"INSIDER_TRANSACTION", "STRUCTURED_FILING"}:
        importance += 12
    age = _age_hours(article.get("published_at", ""), now)
    if age <= 2:
        importance += 8
    elif age <= 8:
        importance += 4
    importance = _clamp(importance)

    confidence = 48
    if article.get("url"):
        confidence += 7
    if article.get("summary"):
        confidence += 7
    if article.get("publisher"):
        confidence += 5
    if raw_meta.get("is_primary_source"):
        confidence += 18
    confidence += min(12, (len(article.get("corroborating_providers") or []) - 1) * 8)
    confidence = _clamp(confidence)

    relevance = 30
    if holdings_hit:
        relevance = 100
    elif watchlist_hit:
        relevance = 80
    elif symbols:
        relevance = 45

    # Catalyst influence is deliberately bounded and confidence-gated.  News
    # can refine a score, never replace price/risk evidence.
    magnitude = max(0, min(8, round((importance - 45) / 7)))
    score_adjustment = direction_sign * magnitude if confidence >= 55 else 0
    if direction == "MIXED":
        score_adjustment = 0

    fact = str(article.get("summary") or article.get("title") or "")[:700]
    if direction == "BULLISH":
        inference = "若消息獲後續數據或公司公告確認，短線可能形成正向催化。"
    elif direction == "BEARISH":
        inference = "若消息持續發酵，短線波動與下行風險可能提高。"
    elif direction == "MIXED":
        inference = "消息同時包含正負面因素，需等待價格與後續公告確認方向。"
    else:
        inference = "目前不足以判斷明確多空方向，先列入觀察。"

    return {
        "title_zh": article.get("title", "")
        if _contains_cjk(article.get("title", ""))
        else "",
        "summary_zh": fact if _contains_cjk(fact) else "",
        "direction": direction,
        "importance": importance,
        "confidence": confidence,
        "relevance": relevance,
        "time_horizon": "DAYS" if importance >= 60 else "WEEKS",
        "fact": fact,
        "inference": inference,
        "affected_symbols": holdings_hit
        + [s for s in watchlist_hit if s not in holdings_hit],
        "score_adjustment": score_adjustment,
        "risk_flags": [filing_risk] if filing_risk else [],
        "analysis_method": "RULES",
    }


def _extract_json_array(text: str) -> list[dict]:
    text = str(text or "").strip()
    if text.startswith("```"):
        text = re.sub(
            r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE | re.DOTALL
        )
    start = text.find("[")
    end = text.rfind("]")
    if start < 0 or end <= start:
        return []
    try:
        value = json.loads(text[start : end + 1])
    except (TypeError, ValueError):
        return []
    return (
        [item for item in value if isinstance(item, dict)]
        if isinstance(value, list)
        else []
    )


def _ai_client():
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not key:
        return None
    try:
        import anthropic

        return anthropic.Anthropic(api_key=key)
    except Exception:  # noqa: BLE001 - optional provider must fail closed
        return None


def _ai_batch(
    articles: list[dict],
    *,
    universe: list[str],
    client,
    model: str,
) -> dict[str, dict]:
    payload = []
    for article in articles:
        payload.append(
            {
                "id": article["dedupe_key"][:16],
                "title": article.get("title", ""),
                "summary": article.get("summary", ""),
                "publisher": article.get("publisher", ""),
                "published_at": article.get("published_at", ""),
                "symbols": article.get("symbols") or [],
            }
        )
    prompt = (
        "你是 Scott 市場情報分類器。只能根據輸入新聞文字分析，不可補充未提供的事實。"
        "事實與推論必須分開；affected_symbols 只能選自 user_universe。"
        "每篇輸出一筆 JSON，id 必須原樣保留。direction 僅能是 BULLISH、BEARISH、"
        "NEUTRAL、MIXED；time_horizon 僅能是 INTRADAY、DAYS、WEEKS、LONG_TERM。"
        "importance、confidence、relevance 為 0-100；score_adjustment 為 -8 到 8，"
        "只有高信心且與標的直接相關時才可非零。輸出純 JSON 陣列，不要 markdown。\n"
        f"user_universe={json.dumps(universe, ensure_ascii=False)}\n"
        "欄位：id,title_zh,summary_zh,direction,importance,confidence,relevance,"
        "time_horizon,fact,inference,affected_symbols,score_adjustment,risk_flags。\n"
        f"articles={json.dumps(payload, ensure_ascii=False)}"
    )
    message = client.messages.create(
        model=model,
        max_tokens=3000,
        temperature=0,
        messages=[{"role": "user", "content": prompt}],
    )
    text = "\n".join(
        str(block.text) for block in message.content if getattr(block, "text", None)
    )
    return {
        str(item.get("id")): item
        for item in _extract_json_array(text)
        if item.get("id")
    }


def _merge_ai(base: dict, ai: dict, universe: set[str]) -> dict:
    if not isinstance(ai, dict):
        return base
    merged = dict(base)
    direction = str(ai.get("direction") or "").upper()
    if direction in _DIRECTION:
        merged["direction"] = direction
    horizon = str(ai.get("time_horizon") or "").upper()
    if horizon in _HORIZON:
        merged["time_horizon"] = horizon
    for field in ("importance", "confidence", "relevance"):
        merged[field] = _clamp(ai.get(field), default=merged[field])
    for field, maximum in (
        ("title_zh", 1000),
        ("summary_zh", 1200),
        ("fact", 1000),
        ("inference", 1000),
    ):
        value = str(ai.get(field) or "").strip()
        if value:
            merged[field] = value[:maximum]
    affected = []
    for symbol in ai.get("affected_symbols") or []:
        symbol = str(symbol or "").upper().strip()
        if symbol in universe and symbol not in affected:
            affected.append(symbol)
    merged["affected_symbols"] = affected
    adjustment = _clamp(ai.get("score_adjustment"), -8, 8, base["score_adjustment"])
    if (
        merged["confidence"] < 60
        or merged["direction"] in {"NEUTRAL", "MIXED"}
        or not affected
    ):
        adjustment = 0
    if merged["direction"] == "BULLISH":
        adjustment = abs(adjustment)
    elif merged["direction"] == "BEARISH":
        adjustment = -abs(adjustment)
    merged["score_adjustment"] = adjustment
    flags = ai.get("risk_flags") or []
    merged["risk_flags"] = [str(flag)[:200] for flag in flags[:5] if str(flag).strip()]
    merged["analysis_method"] = "AI_GROUNDED"
    return merged


def analyze_articles(
    articles: list[dict],
    *,
    holding_symbols: list[str],
    watchlist_symbols: list[str],
    model: str,
    client=None,
    now: datetime | None = None,
) -> list[dict]:
    """Attach validated analysis to each article, with deterministic fallback."""
    now = now or datetime.now(timezone.utc)
    holdings = {str(s).upper() for s in holding_symbols}
    watchlist = {str(s).upper() for s in watchlist_symbols}
    universe = list(dict.fromkeys([*holding_symbols, *watchlist_symbols]))
    for article in articles:
        article["analysis"] = heuristic_analysis(
            article,
            holding_symbols=holdings,
            watchlist_symbols=watchlist,
            now=now,
        )

    client = client if client is not None else _ai_client()
    if not client or not articles:
        return articles

    # Prioritise the items most likely to affect the user's decisions and cap
    # AI usage; all remaining articles keep a transparent rules analysis.
    candidates = sorted(
        articles,
        key=lambda item: (
            item["analysis"]["relevance"],
            item["analysis"]["importance"],
        ),
        reverse=True,
    )[:18]
    ai_results: dict[str, dict] = {}
    for start in range(0, len(candidates), 9):
        try:
            ai_results.update(
                _ai_batch(
                    candidates[start : start + 9],
                    universe=universe,
                    client=client,
                    model=model,
                )
            )
        except Exception:  # noqa: BLE001, S112 - retain deterministic fallback
            continue
    universe_set = set(universe)
    for article in articles:
        key = article["dedupe_key"][:16]
        if key in ai_results:
            article["analysis"] = _merge_ai(
                article["analysis"], ai_results[key], universe_set
            )
    return articles


def aggregate_catalysts(articles: list[dict], universe: list[str]) -> list[dict]:
    """Aggregate article-level evidence into bounded per-symbol adjustments."""
    buckets: dict[str, list[dict]] = {str(symbol).upper(): [] for symbol in universe}
    buckets["_MARKET"] = []
    for article in articles:
        analysis = article.get("analysis") or {}
        affected = [
            str(symbol).upper()
            for symbol in (
                analysis.get("affected_symbols") or article.get("symbols") or []
            )
            if str(symbol).upper() in buckets
        ]
        if affected:
            for symbol in set(affected):
                buckets[symbol].append(article)
        elif analysis.get("importance", 0) >= 60:
            buckets["_MARKET"].append(article)

    catalysts = []
    for symbol, items in buckets.items():
        if not items:
            continue
        items = sorted(
            items,
            key=lambda item: (
                item.get("analysis", {}).get("importance", 0),
                item.get("analysis", {}).get("confidence", 0),
            ),
            reverse=True,
        )[:5]
        weighted = total_weight = 0.0
        for item in items:
            analysis = item["analysis"]
            weight = max(1.0, analysis["importance"] * analysis["confidence"] / 100)
            weighted += analysis["score_adjustment"] * weight
            total_weight += weight
        adjustment = (
            max(-8.0, min(8.0, round(weighted / total_weight, 1)))
            if total_weight
            else 0
        )
        confidence = round(
            sum(item["analysis"]["confidence"] for item in items) / len(items)
        )
        if confidence < 60:
            adjustment = 0
        direction = (
            "BULLISH" if adjustment > 0 else "BEARISH" if adjustment < 0 else "NEUTRAL"
        )
        lead = items[0]
        lead_analysis = lead["analysis"]
        catalysts.append(
            {
                "symbol": symbol,
                "direction": direction,
                "score_adjustment": adjustment,
                "importance": max(item["analysis"]["importance"] for item in items),
                "confidence": confidence,
                "summary": lead_analysis.get("summary_zh")
                or lead_analysis.get("inference")
                or lead.get("title", ""),
                "headlines": [
                    item["analysis"].get("title_zh") or item.get("title", "")
                    for item in items
                ],
                "source_urls": list(
                    dict.fromkeys(
                        item.get("url", "") for item in items if item.get("url")
                    )
                ),
            }
        )
    return catalysts


def build_report(
    *,
    run_type: str,
    articles: list[dict],
    catalysts: list[dict],
    user_context: dict,
    provider_health: dict,
    new_keys: set[str],
    generated_at: str | None = None,
) -> dict:
    generated_at = generated_at or datetime.now(timezone.utc).isoformat()
    catalyst_map = {item["symbol"]: item for item in catalysts}
    ranked = sorted(
        articles,
        key=lambda item: (
            item.get("analysis", {}).get("relevance", 0),
            item.get("analysis", {}).get("importance", 0),
            item.get("published_at", ""),
        ),
        reverse=True,
    )
    top_events = []
    for item in ranked[:12]:
        analysis = item.get("analysis") or {}
        top_events.append(
            {
                "id": item.get("dedupe_key"),
                "title": analysis.get("title_zh") or item.get("title"),
                "original_title": item.get("title"),
                "summary": analysis.get("summary_zh")
                or item.get("summary")
                or analysis.get("inference"),
                "fact": analysis.get("fact"),
                "inference": analysis.get("inference"),
                "direction": analysis.get("direction"),
                "importance": analysis.get("importance"),
                "confidence": analysis.get("confidence"),
                "relevance": analysis.get("relevance"),
                "affected_symbols": analysis.get("affected_symbols")
                or item.get("symbols")
                or [],
                "publisher": item.get("publisher") or item.get("source_provider"),
                "url": item.get("url"),
                "published_at": item.get("published_at"),
                "is_new": item.get("dedupe_key") in new_keys,
                "analysis_method": analysis.get("analysis_method"),
            }
        )

    portfolio_impacts = [
        catalyst_map[symbol]
        for symbol in user_context.get("holding_symbols") or []
        if symbol in catalyst_map
    ]
    watchlist_impacts = [
        catalyst_map[symbol]
        for symbol in user_context.get("watchlist") or []
        if symbol in catalyst_map
    ][:10]
    actions = []
    for item in portfolio_impacts:
        if item["direction"] == "BEARISH" and item["importance"] >= 70:
            actions.append(
                {
                    "priority": "HIGH",
                    "symbol": item["symbol"],
                    "action": "檢查原停損與部位風險，不因單一新聞直接下單。",
                    "reason": item["summary"],
                }
            )
        elif item["direction"] == "BULLISH" and item["importance"] >= 75:
            actions.append(
                {
                    "priority": "MEDIUM",
                    "symbol": item["symbol"],
                    "action": "等待價格、成交量與正式公告確認催化劑。",
                    "reason": item["summary"],
                }
            )
    if not actions:
        actions.append(
            {
                "priority": "LOW",
                "symbol": "MARKET",
                "action": "目前沒有需要立即處理的高可信度持股事件。",
                "reason": "情報仍保留在系統供後續查證。",
            }
        )

    gaps = []
    if not user_context.get("symbols"):
        gaps.append("尚未同步持股或自選股，報告只能提供市場層級情報。")
    if not provider_health.get("finnhub", {}).get("configured"):
        gaps.append("FINNHUB_KEY 未設定；目前由 Yahoo Finance 補足公司新聞。")
    if not provider_health.get("alpha_vantage", {}).get("configured"):
        gaps.append("ALPHA_VANTAGE_KEY 未設定；缺少第二個新聞情緒來源交叉驗證。")
    if not provider_health.get("sec_edgar", {}).get("configured"):
        gaps.append("SEC_USER_AGENT 未設定；美股 S-3／424B5 稀釋與 Form 4 監控尚未啟用。")
    if not os.environ.get("ANTHROPIC_API_KEY", "").strip():
        gaps.append("ANTHROPIC_API_KEY 未設定；目前只使用可解釋規則分類。")
    successful_providers = [
        name for name, value in provider_health.items() if value.get("succeeded", 0) > 0
    ]
    if not successful_providers:
        gaps.append("本次所有新聞來源均未成功，不能形成市場結論。")

    bullish = sum(1 for event in top_events if event["direction"] == "BULLISH")
    bearish = sum(1 for event in top_events if event["direction"] == "BEARISH")
    high_importance = sum(1 for event in top_events if (event["importance"] or 0) >= 75)
    return {
        "ok": bool(articles),
        "run_type": run_type,
        "generated_at": generated_at,
        "summary": {
            "article_count": len(articles),
            "new_count": len(new_keys),
            "bullish_count": bullish,
            "bearish_count": bearish,
            "high_importance_count": high_importance,
            "holding_impacts": len(portfolio_impacts),
        },
        "top_events": top_events,
        "portfolio_impacts": portfolio_impacts,
        "watchlist_impacts": watchlist_impacts,
        "market_catalyst": catalyst_map.get("_MARKET"),
        "actions": actions[:5],
        "provider_health": provider_health,
        "data_gaps": gaps,
        "disclaimer": (
            "此報告將已取得的新聞與使用者持股連結，事實與 AI 推論分開顯示。"
            "分數調整上限為 ±8，不能取代價格、風控或人工查證，也不會觸發真實下單。"
        ),
    }
