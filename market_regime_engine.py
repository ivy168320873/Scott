"""
Market Regime Engine — Phase 13B
Determines current market environment and whether it allows aggressive action.

Public API
----------
run_market_regime(ohlcv_fn) -> dict

Regimes:
  RISK_ON    — Full attack: buy/hold/trim all allowed
  NEUTRAL    — Cautious: only selective buys, smaller size
  RISK_OFF   — Defensive: no new buys, trim/watch/sell only
  CRASH_RISK — Emergency: no new positions, risk-manage only
"""
from __future__ import annotations

from datetime import datetime, timezone


# ── Public API ────────────────────────────────────────────────────────────────

def run_market_regime(ohlcv_fn) -> dict:
    """
    ohlcv_fn: callable(symbol: str) -> normalized OHLCV dict
              (must have 'closes', 'volumes', 'is_demo' keys)

    Returns:
      market_regime          : "RISK_ON" | "NEUTRAL" | "RISK_OFF" | "CRASH_RISK"
      market_score           : int 0-100
      risk_budget_multiplier : float 0.0-1.5
      allowed_actions        : list[str]
      reason                 : list[str]   — positive / supporting reasons
      invalid_conditions     : list[str]   — blocking conditions
      indices                : dict        — per-index detail (SPY / QQQ)
      is_demo                : bool
    """
    spy_raw = _safe_fetch(ohlcv_fn, "SPY")
    qqq_raw = _safe_fetch(ohlcv_fn, "QQQ")

    is_demo = spy_raw.get("is_demo", True) or qqq_raw.get("is_demo", True)

    spy = _index_stats(spy_raw, "SPY", ma_periods=[20, 50, 200])
    qqq = _index_stats(qqq_raw, "QQQ", ma_periods=[20, 50])

    reasons:  list[str] = []
    invalid:  list[str] = []

    # ── Unpack key flags ──────────────────────────────────────────────────────
    spy_above_ma200 = spy["above_ma200"]
    spy_above_ma50  = spy["above_ma50"]
    spy_above_ma20  = spy["above_ma20"]
    qqq_above_ma20  = qqq["above_ma20"]

    spy_1d = spy["change_1d_pct"]
    qqq_1d = qqq["change_1d_pct"]

    both_below_ma20 = (spy_above_ma20 is False) and (qqq_above_ma20 is False)
    big_daily_drop  = (qqq_1d is not None and qqq_1d < -2.5) or (spy_1d is not None and spy_1d < -2.5)
    crash_drop      = (qqq_1d is not None and qqq_1d < -3.5) or (spy_1d is not None and spy_1d < -3.5)

    # ── Classify regime ───────────────────────────────────────────────────────
    if spy_above_ma200 is False:
        invalid.append("SPY 跌破 MA200：禁止主動買進（熊市環境）")
        if both_below_ma20 and (big_daily_drop or crash_drop):
            regime = "CRASH_RISK"
            invalid.append("SPY+QQQ 同跌破 MA20 且單日大跌：崩跌風險模式")
        else:
            regime = "RISK_OFF"

    elif both_below_ma20:
        if big_daily_drop:
            regime = "RISK_OFF"
            invalid.append("SPY+QQQ 同時跌破 MA20 且單日大跌：進入防守")
        else:
            regime = "NEUTRAL"
            reasons.append("SPY+QQQ 同時位於 MA20 以下，市場偏弱震盪")

    elif big_daily_drop:
        regime = "NEUTRAL"
        reasons.append(f"QQQ/SPY 單日跌幅超過 2.5%（QQQ {qqq_1d:+.1f}%），暫時防守")

    elif qqq_above_ma20 and spy_above_ma50:
        regime = "RISK_ON"
        reasons.append("QQQ > MA20 且 SPY > MA50：市場處於進攻型環境")
        if spy_above_ma200:
            reasons.append("SPY 站穩 MA200：中長期趨勢健康")

    elif qqq_above_ma20 and not spy_above_ma50:
        regime = "NEUTRAL"
        reasons.append("QQQ 站上 MA20 但 SPY 仍在 MA50 以下：進攻力道不足")

    else:
        regime = "NEUTRAL"
        reasons.append("市場信號混雜，建議謹慎等待確認")

    # ── QQQ < MA20 降低新倉權重（即使 regime 不是 RISK_OFF）────────────────
    if qqq_above_ma20 is False and regime == "RISK_ON":
        regime = "NEUTRAL"
        reasons.append("QQQ 跌破 MA20：降低新倉權重")

    # ── SPY/QQQ 同時跌破 MA20 硬規則 ─────────────────────────────────────────
    if both_below_ma20 and regime == "RISK_ON":
        regime = "NEUTRAL"
        invalid.append("SPY+QQQ 同跌破 MA20：最少降至 NEUTRAL")

    # ── Score ─────────────────────────────────────────────────────────────────
    score = 50
    if spy_above_ma200 is True:  score += 20
    elif spy_above_ma200 is False: score -= 25
    if spy_above_ma50  is True:  score += 15
    elif spy_above_ma50  is False: score -= 10
    if qqq_above_ma20  is True:  score += 20
    elif qqq_above_ma20  is False: score -= 15
    if spy_1d is not None:
        score += max(-5, min(5, spy_1d * 1.5))
    if qqq_1d is not None:
        score += max(-5, min(5, qqq_1d * 1.5))
    score = max(0, min(100, round(score)))

    # ── Allowed actions ───────────────────────────────────────────────────────
    if regime == "CRASH_RISK":
        allowed   = ["WATCH", "TRIM", "SELL", "AVOID"]
        budget    = 0.0
    elif regime == "RISK_OFF":
        allowed   = ["WATCH", "TRIM", "SELL"]
        budget    = 0.2
    elif regime == "NEUTRAL":
        allowed   = ["WATCH", "HOLD", "BUY", "TRIM"]  # cautious buy
        budget    = 0.6
        if big_daily_drop:
            allowed = ["WATCH", "HOLD", "TRIM"]
            budget  = 0.35
    else:  # RISK_ON
        allowed   = ["STRONG_BUY", "BUY", "WATCH", "HOLD", "TRIM", "SELL"]
        budget    = 1.0
        if big_daily_drop:
            allowed = ["WATCH", "HOLD", "TRIM"]
            budget  = 0.5

    return {
        "ok":                    True,
        "market_regime":         regime,
        "market_score":          score,
        "risk_budget_multiplier": round(budget, 2),
        "allowed_actions":       allowed,
        "reason":                reasons,
        "invalid_conditions":    invalid,
        "is_demo":               is_demo,
        "indices": {
            "SPY": spy,
            "QQQ": qqq,
        },
    }


# ── Internal helpers ──────────────────────────────────────────────────────────

def _safe_fetch(fn, symbol: str) -> dict:
    try:
        result = fn(symbol)
        return result if result and isinstance(result, dict) else {}
    except Exception:
        return {}


def _sma(lst: list, n: int) -> float | None:
    valid = [v for v in lst[-n:] if v and v > 0]
    if len(valid) < n:
        return None
    return sum(valid) / len(valid)


def _index_stats(ohlcv: dict, symbol: str, ma_periods: list) -> dict:
    closes = ohlcv.get("closes", [])
    n = len(closes)

    price   = closes[-1]  if n >= 1 else None
    prev    = closes[-2]  if n >= 2 else None
    prev_5  = closes[-6]  if n >= 6 else None
    chg_1d  = round((price - prev) / prev * 100, 2) if price and prev and prev > 0 else None
    chg_5d  = round((price - prev_5) / prev_5 * 100, 2) if price and prev_5 and prev_5 > 0 else None

    stats: dict = {
        "symbol":       symbol,
        "price":        round(price, 2) if price else None,
        "change_1d_pct": chg_1d,
        "change_5d_pct": chg_5d,
        "is_demo":      ohlcv.get("is_demo", True),
        "source":       ohlcv.get("source", "unknown"),
        "bar_count":    n,
    }

    for period in ma_periods:
        key    = f"ma{period}"
        above  = f"above_ma{period}"
        ma_val = _sma(closes, period)
        stats[key]   = round(ma_val, 2) if ma_val else None
        stats[above] = (price > ma_val) if (price and ma_val) else None

    return stats
