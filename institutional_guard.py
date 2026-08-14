"""Fail-closed readiness gate for institutional-style daily signals."""

from __future__ import annotations

from datetime import datetime

from market_clock import market_session


def assess_signal_readiness(
    symbol: str,
    *,
    ohlcv: dict | None,
    data_quality: dict | None,
    quote: dict | None = None,
    now: datetime | None = None,
) -> dict:
    data = ohlcv if isinstance(ohlcv, dict) else {}
    quality = data_quality if isinstance(data_quality, dict) else {}
    session = market_session(symbol, now=now)
    blockers: list[str] = []
    warnings: list[str] = []

    if quality.get("is_demo"):
        blockers.append("Demo 行情不可產生可執行訊號")
    if quality.get("data_status") == "BAD":
        blockers.append("OHLCV 品質為 BAD")
    elif quality.get("can_trade_decision") is False:
        blockers.append("OHLCV 品質未達可產生交易決策門檻")
    if data.get("last_bar_complete") is False:
        blockers.append("最新日 K 尚未完成")
    if data.get("excluded_incomplete_bar"):
        warnings.append("盤中日 K 已排除；指標只使用完整日 K")

    quote_ok = isinstance(quote, dict) and quote.get("ok") and quote.get("last")
    if session["state"] == "OPEN":
        if not quote_ok:
            blockers.append("盤中缺少可驗證的最新報價")
        elif quote.get("is_stale"):
            blockers.append(
                f"盤中報價過時（{quote.get('age_seconds', '未知')} 秒）"
            )
    elif not quote_ok:
        warnings.append("非交易時段缺少最新報價；僅能做收盤後研究")

    execution_ready = bool(quote_ok and quote.get("execution_ready"))
    max_bullish_decision = "BUY" if not quote_ok else None
    if quote_ok:
        scope = quote.get("feed_scope")
        if scope == "SINGLE_EXCHANGE_IEX":
            warnings.append("IEX 為單一交易所資料，非全市場 SIP／NBBO；禁止升級 STRONG_BUY")
            max_bullish_decision = "BUY"
        elif scope == "DELAYED_SIP":
            warnings.append("SIP 行情為延遲資料；盤中不可視為可執行報價")
            max_bullish_decision = "BUY"
        elif scope == "OVERNIGHT":
            warnings.append("隔夜盤來源不代表正常交易時段 NBBO；僅供研究")
            max_bullish_decision = "BUY"
        elif scope == "REFERENCE_ONLY":
            warnings.append("Yahoo 為參考行情且無 NBBO；可做訊號研究，不可模擬精確成交")
            max_bullish_decision = "BUY"
        if not quote.get("execution_ready"):
            warnings.append("報價未達無延遲 SIP／NBBO 等級，成交滑價只能使用保守模型估算")
            max_bullish_decision = "BUY"

    if blockers:
        status = "BLOCKED"
        max_bullish_decision = "WATCH"
    elif execution_ready:
        status = "READY"
    else:
        status = "RESEARCH_ONLY"

    closes = data.get("closes") or []
    return {
        "ok": not blockers,
        "status": status,
        "symbol": str(symbol or "").upper(),
        "session": session,
        "quote": quote,
        "last_completed_close": closes[-1] if closes else None,
        "last_completed_bar": data.get("last_bar_date")
        or ((data.get("dates") or [None])[-1] if data.get("dates") else None),
        "max_bullish_decision": max_bullish_decision,
        "automated_execution_allowed": False,
        "shadow_fill_model": "NEXT_REGULAR_OPEN_WITH_COSTS",
        "blockers": blockers,
        "warnings": list(dict.fromkeys(warnings)),
        "data_provenance": {
            "ohlcv_source": data.get("source"),
            "provider_chain": data.get("provider_chain") or [],
            "price_basis": data.get("price_basis"),
            "fetched_at": data.get("fetched_at"),
            "quality_flags": data.get("quality_flags") or [],
        },
        "note": "訊號品質與成交品質分開評估；本閘門不授權真實下單。",
    }
