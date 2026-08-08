"""Evidence construction for decisions and a code-level AI answer guard."""

from __future__ import annotations

import re
from datetime import datetime, timezone


_MARKET_TERMS = (
    "股票", "股價", "買", "賣", "進場", "出場", "停損", "目標價", "估值",
    "本益比", "eps", "營收", "財報", "技術面", "籌碼", "法人", "融資", "融券",
    "回測", "動能", "趨勢", "上漲", "下跌", "預測", "風險", "持倉", "大盤",
    "stock", "price", "buy", "sell", "valuation", "forecast", "backtest",
)
_TICKER_RE = re.compile(r"(?:\$[A-Z]{1,6}|\b[A-Z]{2,6}(?:\.TW)?\b|\b\d{4}\.TW\b)")
_NON_TICKERS = {"AI", "API", "CSS", "HTML", "HTTP", "JSON", "LINE", "SQL", "TWSE", "URL"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_number(value, default=0.0) -> float:
    try:
        number = float(value)
        return number if number == number and abs(number) != float("inf") else default
    except (TypeError, ValueError, OverflowError):
        return default


def build_decision_evidence(symbol: str, ohlcv: dict, decision: dict) -> list[dict]:
    closes = ohlcv.get("closes", []) if isinstance(ohlcv, dict) else []
    volumes = ohlcv.get("volumes", []) if isinstance(ohlcv, dict) else []
    timestamps = ohlcv.get("timestamps", []) if isinstance(ohlcv, dict) else []
    observed_at = str(timestamps[-1]) if timestamps else _now()
    source = str((ohlcv or {}).get("source") or "market data provider")[:100]
    is_demo = bool((ohlcv or {}).get("is_demo", False))
    price = _safe_number(closes[-1]) if closes else 0.0
    ma20 = sum(_safe_number(v) for v in closes[-20:]) / 20 if len(closes) >= 20 else None
    avg_volume = (
        sum(_safe_number(v) for v in volumes[-20:]) / 20 if len(volumes) >= 20 else None
    )
    last_volume = _safe_number(volumes[-1]) if volumes else None
    chase = decision.get("chase_risk") if isinstance(decision.get("chase_risk"), dict) else {}
    quality = decision.get("data_quality") if isinstance(decision.get("data_quality"), dict) else {}
    entries = [
        {
            "category": "price",
            "source": source,
            "statement": f"{symbol} 最新可用收盤價 {price:.4f}；資料品質 {quality.get('data_status', 'UNKNOWN')}",
            "value": price,
            "observed_at": observed_at,
            "is_demo": is_demo,
        },
        {
            "category": "trend",
            "source": "top_tier_decision_engine",
            "statement": (
                f"綜合趨勢決策 {decision.get('decision', 'WATCH')}，分數 "
                f"{_safe_number(decision.get('top_tier_score'), 0):.0f}/100"
                + (f"；MA20 {ma20:.4f}" if ma20 is not None else "；MA20 資料不足")
            ),
            "value": decision.get("top_tier_score"),
            "observed_at": observed_at,
            "is_demo": is_demo,
        },
        {
            "category": "volume",
            "source": source,
            "statement": (
                f"最新成交量 {last_volume:.0f}，20期均量 {avg_volume:.0f}"
                if last_volume is not None and avg_volume is not None
                else "成交量資料不足，未將量能視為已確認"
            ),
            "value": last_volume,
            "observed_at": observed_at,
            "is_demo": is_demo,
        },
        {
            "category": "risk",
            "source": "risk_engine",
            "statement": (
                f"追高風險 {chase.get('score', 'N/A')}/100（{chase.get('level', 'UNKNOWN')}）"
            ),
            "value": chase.get("score"),
            "observed_at": observed_at,
            "is_demo": is_demo,
        },
        {
            "category": "market",
            "source": "market_regime_engine",
            "statement": (
                f"市場環境 {decision.get('market_regime', 'NEUTRAL')}，市場分數 "
                f"{decision.get('market_score', 'N/A')}"
            ),
            "value": decision.get("market_score"),
            "observed_at": observed_at,
            "is_demo": is_demo,
        },
    ]
    return entries


def requires_market_evidence(message: str) -> bool:
    text = str(message or "").strip()
    lowered = text.lower()
    if any(term in lowered for term in _MARKET_TERMS):
        return True
    matches = [match.group(0).replace("$", "") for match in _TICKER_RE.finditer(text)]
    return any(value not in _NON_TICKERS for value in matches)


def tool_evidence(name: str, tool_input: dict, output: str, is_error: bool = False) -> dict:
    compact = re.sub(r"\s+", " ", str(output or "")).strip()
    return {
        "tool": str(name)[:80],
        "query": {
            str(key)[:50]: str(value)[:100]
            for key, value in (tool_input or {}).items()
        },
        "retrieved_at": _now(),
        "is_error": bool(is_error),
        "is_demo": "demo" in compact.lower() or "模擬資料" in compact,
        "excerpt": compact[:240],
    }


def enforce_market_evidence(
    user_message: str,
    reply: str,
    evidence: list[dict] | None,
) -> dict:
    evidence = [item for item in (evidence or []) if isinstance(item, dict)]
    successful = [item for item in evidence if not item.get("is_error")]
    required = requires_market_evidence(user_message)
    if required and not successful:
        guarded_reply = (
            "我目前沒有取得可驗證的行情或分析工具結果，因此不會直接給出買賣、估值或預測判斷。"
            "請稍後重試；在資料成功取得前，以觀望為準。\n\n"
            "這不構成投資建議。"
        )
        return {
            "reply": guarded_reply,
            "required": True,
            "blocked": True,
            "evidence": evidence,
        }

    guarded_reply = str(reply or "").strip()
    if successful and "資料依據：" not in guarded_reply:
        lines = ["", "資料依據："]
        for item in successful[:6]:
            query = "、".join(f"{k}={v}" for k, v in (item.get("query") or {}).items())
            suffix = f"（{query}）" if query else ""
            demo = "［Demo］" if item.get("is_demo") else ""
            lines.append(f"- {item.get('tool', 'tool')}{suffix} {demo} · {item.get('retrieved_at', '')[:19]} UTC")
        guarded_reply += "\n" + "\n".join(lines)
    if any(item.get("is_demo") for item in successful) and "模擬資料" not in guarded_reply:
        guarded_reply += "\n\n⚠️ 其中含 Demo／模擬資料，不可作為交易決策。"
    return {
        "reply": guarded_reply,
        "required": required,
        "blocked": False,
        "evidence": evidence,
    }
