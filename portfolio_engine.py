"""
Capital Efficiency Score.
Evaluates whether a portfolio position is being held efficiently,
relative to benchmark performance and capital opportunity cost.
Levels: ADD / HOLD / WATCH / TRIM / ROTATE / STOP_LOSS
"""
from __future__ import annotations
from datetime import date as _date, datetime as _dt, timezone as _tz


def _sma(lst: list, n: int) -> float:
    tail = [v for v in lst[-n:] if v and v > 0]
    return sum(tail) / len(tail) if tail else 0.0


def calc_capital_efficiency(
    holding: dict,
    ohlcv: dict,
    benchmark_return: float | None = None,
) -> dict:
    """
    holding keys: symbol, cost (float), qty (float), buy_date (YYYY-MM-DD),
                  current_price (optional float)
    ohlcv:        normalized dict with closes/highs/lows/volumes
    benchmark_return: benchmark % return over the same holding period (e.g., QQQ).
                      Pass None if unavailable — absolute PnL is used as fallback.
    """
    symbol       = holding.get("symbol", "?")
    cost         = float(holding.get("cost", 0) or 0)
    qty          = float(holding.get("qty",  0) or 0)
    buy_date_str = holding.get("buy_date", "")
    closes       = ohlcv.get("closes", [])

    if not closes or cost <= 0:
        return _empty_result(symbol, "資料不足或未提供成本")

    current = float(holding.get("current_price", 0) or closes[-1])
    pnl_pct = (current - cost) / cost * 100

    # Holding days
    try:
        buy_date     = _dt.strptime(buy_date_str, "%Y-%m-%d").replace(tzinfo=_tz.utc).date()
        holding_days = (_date.today() - buy_date).days
    except Exception:
        holding_days = 30

    holding_days = max(holding_days, 1)

    score: int = 50   # neutral baseline
    reasons:      list[str] = []
    warning_flags: list[str] = []

    # ── 1. Alpha vs benchmark (±20 pts) ──────────────────────────────────────
    if benchmark_return is not None:
        alpha = pnl_pct - benchmark_return
        if   alpha >  20: score += 20; reasons.append(f"跑贏基準 +{alpha:.1f}%（出色超額報酬）")
        elif alpha >  10: score += 14; reasons.append(f"跑贏基準 +{alpha:.1f}%")
        elif alpha >   5: score +=  8; reasons.append(f"略勝基準 +{alpha:.1f}%")
        elif alpha >  -5: score +=  0; reasons.append(f"與基準表現相近（α={alpha:+.1f}%）")
        elif alpha > -10: score -=  8; reasons.append(f"跑輸基準 {alpha:.1f}%（略遜）"); warning_flags.append("跑輸大盤")
        elif alpha > -20: score -= 14; reasons.append(f"跑輸基準 {alpha:.1f}%"); warning_flags.append("明顯跑輸大盤")
        else:             score -= 20; reasons.append(f"嚴重跑輸基準 {alpha:.1f}%"); warning_flags.append("嚴重跑輸大盤")
    else:
        # Absolute-return proxy
        if   pnl_pct >  20: score += 15; reasons.append(f"報酬 +{pnl_pct:.1f}%（優異）")
        elif pnl_pct >  10: score +=  8; reasons.append(f"報酬 +{pnl_pct:.1f}%")
        elif pnl_pct >   0: score +=  3; reasons.append(f"小幅獲利 +{pnl_pct:.1f}%")
        elif pnl_pct >  -8: score -=  5; reasons.append(f"小幅虧損 {pnl_pct:.1f}%")
        else:               score -= 15; reasons.append(f"虧損 {pnl_pct:.1f}%"); warning_flags.append("虧損持倉")

    # ── 2. Current momentum (±20 pts) ────────────────────────────────────────
    n = len(closes)
    if n >= 20:
        ma20 = _sma(closes, 20)
        ma5  = _sma(closes, 5)
        if ma5 > ma20 and current > ma20:
            score += 15; reasons.append("均線多頭排列，動能持續")
        elif current > ma20:
            score +=  5; reasons.append("收在 MA20 之上，趨勢尚可")
        elif current < ma20 * 0.95:
            score -= 15; reasons.append("跌破 MA20 且距離擴大，動能轉弱"); warning_flags.append("動能轉弱")
        else:
            score -=  5; reasons.append("收在 MA20 附近，趨勢中性")

    # ── 3. Annualised return / holding efficiency (±15 pts) ──────────────────
    annual_return = (pnl_pct / holding_days) * 365
    if   annual_return >  50: score += 15; reasons.append(f"年化報酬率 {annual_return:.0f}%（資本效率極高）")
    elif annual_return >  30: score += 10; reasons.append(f"年化報酬率 {annual_return:.0f}%（資本效率高）")
    elif annual_return >  10: score +=  5; reasons.append(f"年化報酬率 {annual_return:.0f}%")
    elif annual_return >  -5: score +=  0
    elif annual_return > -15: score -=  8; reasons.append(f"年化報酬率 {annual_return:.0f}%（偏低）"); warning_flags.append("年化報酬偏低")
    else:                     score -= 15; reasons.append(f"年化報酬率 {annual_return:.0f}%（資本效率極低）"); warning_flags.append("資本效率極低")

    score = min(100, max(0, score))

    if   score >= 75: level = "ADD";       color = "#3fb950"; level_label = "加碼機會"
    elif score >= 55: level = "HOLD";      color = "#58a6ff"; level_label = "繼續持有"
    elif score >= 40: level = "WATCH";     color = "#e3b341"; level_label = "留意觀察"
    elif score >= 25: level = "TRIM";      color = "#f0883e"; level_label = "考慮減倉"
    elif score >= 10: level = "ROTATE";    color = "#bc8cff"; level_label = "換股考量"
    else:             level = "STOP_LOSS"; color = "#f85149"; level_label = "停損出場"

    _ACTIONS = {
        "ADD":       "動能強勁，可考慮加碼",
        "HOLD":      "繼續持有，定期複查",
        "WATCH":     "密切觀察，設定停損線",
        "TRIM":      "考慮分批減倉，鎖定部分獲利",
        "ROTATE":    "資金效率偏低，考慮換股至強勢標的",
        "STOP_LOSS": "建議停損，將資金移至更強標的",
    }

    return {
        "ok":             True,
        "symbol":         symbol,
        "score":          score,
        "level":          level,
        "level_label":    level_label,
        "level_color":    color,
        "reasons":        reasons,
        "suggested_action": _ACTIONS.get(level, ""),
        "warning_flags":  warning_flags,
        "detail": {
            "current_price":    current,
            "cost_basis":       cost,
            "pnl_pct":          round(pnl_pct, 2),
            "holding_days":     holding_days,
            "annual_return":    round(annual_return, 1),
            "position_value":   round(current * qty, 2),
            "benchmark_return": benchmark_return,
        },
    }


def _empty_result(symbol: str, msg: str) -> dict:
    return {
        "ok":             False,
        "symbol":         symbol,
        "score":          None,
        "level":          "HOLD",
        "level_label":    "資料不足",
        "level_color":    "#8b949e",
        "reasons":        [msg],
        "suggested_action": "請確認持倉資料",
        "warning_flags":  [],
        "detail":         {},
    }
