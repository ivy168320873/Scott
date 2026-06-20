"""LLM news classifier.

Responsibility boundary (hard rule): the LLM ONLY classifies and summarises.
It returns importance, the affected market, a YES/NO direction and three
scores. It NEVER returns a position size or a dollar amount — sizing lives in
the risk manager / paper trader.

Three providers:
  - "anthropic": Claude API
  - "openai":    OpenAI API
  - "stub":      deterministic keyword heuristic (offline / CI / no API key)
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List

from ..config import get_settings
from ..models import Classification, NewsEvent

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a financial news classifier for a Polymarket prediction-market trading bot.
You DO NOT make trading decisions and you NEVER suggest position sizes or dollar amounts.
Given a news item and a list of candidate prediction markets, return STRICT JSON:
{
  "is_important": bool,            // could this news move any listed market in the next minutes?
  "market_condition_id": str|null, // condition_id of the single most-affected market, else null
  "direction": "YES"|"NO"|"NONE",  // which outcome the news supports
  "relevance_score": float,        // 0..1 how directly the news bears on that market
  "surprise_score": float,         // 0..1 how unexpected / new the information is
  "confidence_score": float,       // 0..1 your confidence in this classification
  "rationale": str                 // one concise sentence explaining the call
}
Only choose a market_condition_id from the provided candidates. If nothing fits, set is_important=false, direction="NONE".
Return ONLY the JSON object, no prose."""


def _build_user_prompt(news: NewsEvent, markets: List[Dict[str, Any]]) -> str:
    market_lines = "\n".join(
        f'- condition_id={m["condition_id"]} :: {m["question"]}' for m in markets
    ) or "(no candidate markets)"
    return (
        f"NEWS TITLE: {news.title}\n"
        f"NEWS SUMMARY: {news.summary or '(none)'}\n"
        f"PUBLISHED: {news.published_at}\n\n"
        f"CANDIDATE MARKETS:\n{market_lines}\n"
    )


class LLMClassifier:
    def __init__(self) -> None:
        s = get_settings()
        self.provider = s.llm_provider.lower()
        self._settings = s
        self._client = None
        if self.provider == "anthropic" and s.anthropic_api_key:
            import anthropic
            self._client = anthropic.AsyncAnthropic(api_key=s.anthropic_api_key)
        elif self.provider == "openai" and s.openai_api_key:
            import openai
            self._client = openai.AsyncOpenAI(api_key=s.openai_api_key)
        else:
            if self.provider not in ("stub",):
                log.warning("LLM provider '%s' selected but no API key; falling back to stub",
                            self.provider)
            self.provider = "stub"

    async def classify(self, news: NewsEvent, markets: List[Dict[str, Any]]) -> Classification:
        try:
            if self.provider == "anthropic":
                return await self._classify_anthropic(news, markets)
            if self.provider == "openai":
                return await self._classify_openai(news, markets)
            return self._classify_stub(news, markets)
        except Exception as exc:  # noqa: BLE001
            log.warning("classification failed (%s); using stub: %s", self.provider, exc)
            return self._classify_stub(news, markets)

    # ------------------------------------------------------------------ #
    # Providers
    # ------------------------------------------------------------------ #
    async def _classify_anthropic(self, news, markets) -> Classification:
        msg = await self._client.messages.create(
            model=self._settings.anthropic_model,
            max_tokens=512,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": _build_user_prompt(news, markets)}],
        )
        text = "".join(block.text for block in msg.content if block.type == "text")
        return self._parse(text, "anthropic")

    async def _classify_openai(self, news, markets) -> Classification:
        resp = await self._client.chat.completions.create(
            model=self._settings.openai_model,
            max_tokens=512,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": _build_user_prompt(news, markets)},
            ],
        )
        return self._parse(resp.choices[0].message.content, "openai")

    def _classify_stub(self, news: NewsEvent, markets: List[Dict[str, Any]]) -> Classification:
        """Deterministic offline fallback: token-overlap keyword matcher.

        Lets the whole pipeline run end-to-end without any API key. Useful for
        CI and local smoke tests; not meant to be predictive.
        """
        text = f"{news.title} {news.summary or ''}".lower()
        words = {w.strip('.,!?:;"\'()') for w in text.split() if len(w) > 3}

        best_market = None
        best_overlap = 0
        for m in markets:
            q_words = {w.strip('.,!?:;"\'()') for w in m["question"].lower().split() if len(w) > 3}
            overlap = len(words & q_words)
            if overlap > best_overlap:
                best_overlap, best_market = overlap, m

        if not best_market or best_overlap < 2:
            return Classification(False, None, "NONE", 0.0, 0.0, 0.2, "stub",
                                  "No candidate market matched the headline tokens.")

        bullish = {"win", "wins", "approve", "approved", "pass", "passes", "surge", "up", "rise",
                   "agree", "deal", "success", "victory", "lead", "gain"}
        bearish = {"lose", "loses", "reject", "rejected", "fail", "fails", "drop", "down", "fall",
                   "crash", "defeat", "delay", "ban", "block"}
        direction = "YES"
        if words & bearish and not (words & bullish):
            direction = "NO"

        relevance = min(1.0, best_overlap / 5.0)
        return Classification(
            is_important=True,
            market_condition_id=best_market["condition_id"],
            direction=direction,
            relevance_score=round(relevance, 3),
            surprise_score=0.5,
            confidence_score=round(0.4 + 0.1 * best_overlap, 3),
            provider="stub",
            rationale=f"Keyword overlap ({best_overlap}) with '{best_market['question'][:60]}'.",
        )

    # ------------------------------------------------------------------ #
    @staticmethod
    def _parse(text: str, provider: str) -> Classification:
        text = text.strip()
        # strip markdown fences if present
        if text.startswith("```"):
            text = text.split("```")[1].lstrip("json").strip()
        data = json.loads(text)

        def clamp(x, lo=0.0, hi=1.0):
            try:
                return max(lo, min(hi, float(x)))
            except (TypeError, ValueError):
                return 0.0

        direction = str(data.get("direction", "NONE")).upper()
        if direction not in ("YES", "NO", "NONE"):
            direction = "NONE"

        return Classification(
            is_important=bool(data.get("is_important", False)),
            market_condition_id=data.get("market_condition_id") or None,
            direction=direction,
            relevance_score=clamp(data.get("relevance_score")),
            surprise_score=clamp(data.get("surprise_score")),
            confidence_score=clamp(data.get("confidence_score")),
            provider=provider,
            rationale=str(data.get("rationale", ""))[:1000],
        )
