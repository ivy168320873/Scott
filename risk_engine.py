"""
Chase Risk Score (0-100): How risky is it to buy/chase at current price?
0-25  = LOW      (低追價風險，可謹慎介入)
26-50 = MEDIUM   (中等追價風險，建議等拉回)
51-75 = HIGH     (高追價風險，暫緩追價)
76-100 = EXTREME (極度追高，切勿追價)
"""
from __future__ import annotations


def _sma(lst: list, n: int) -> float:
    tail = [v for v in lst[-n:] if v and v > 0]
    return sum(tail) / len(tail) if tail else 0.0


def _rsi_simple(closes: list, period: int = 14) -> float | None:
    if len(closes) < period + 1:
        return None
    deltas = [closes[i] - closes[i - 1] for i in range(len(closes) - period, len(closes))]
    gains  = sum(d for d in deltas if d > 0)
    losses = sum(-d for d in deltas if d < 0)
    avg_gain = gains  / period
    avg_loss = losses / period
    if avg_loss == 0:
        return 100.0 if avg_gain > 0 else 50.0
    rs = avg_gain / avg_loss
    return round(100 - 100 / (1 + rs), 1)


def calc_chase_risk(ohlcv: dict) -> dict:
    """
    Calculate Chase Risk Score from normalized OHLCV dict.
    ohlcv must have: closes, opens, highs, lows, volumes (lists).
    """
    closes  = ohlcv.get("closes",  [])
    opens   = ohlcv.get("opens",   [])
    highs   = ohlcv.get("highs",   [])
    lows    = ohlcv.get("lows",    [])
    volumes = ohlcv.get("volumes", [])

    n = min(len(closes), len(opens), len(highs), len(lows), len(volumes))
    if n < 20:
        return _empty_result("資料不足（需要至少 20 天 K 線）")

    closes  = closes[-n:];  opens   = opens[-n:]
    highs   = highs[-n:];   lows    = lows[-n:]
    volumes = volumes[-n:]

    score = 0
    reasons: list[str] = []
    warning_flags: list[str] = []

    # ── 1. MA20 deviation (0-30 pts) ─────────────────────────────────────────
    ma20     = _sma(closes, 20)
    ma20_dev = (closes[-1] - ma20) / ma20 * 100 if ma20 > 0 else 0.0
    if   ma20_dev > 20: ma20_pts = 30; reasons.append(f"收盤距 MA20 偏離 +{ma20_dev:.1f}%（嚴重偏高）"); warning_flags.append("嚴重偏離MA20")
    elif ma20_dev > 15: ma20_pts = 22; reasons.append(f"收盤距 MA20 偏離 +{ma20_dev:.1f}%（明顯偏高）")
    elif ma20_dev > 10: ma20_pts = 16; reasons.append(f"收盤距 MA20 偏離 +{ma20_dev:.1f}%（偏高）")
    elif ma20_dev >  5: ma20_pts =  8; reasons.append(f"收盤距 MA20 偏離 +{ma20_dev:.1f}%（略偏高）")
    else:               ma20_pts =  0
    score += ma20_pts

    # ── 2. MA60 deviation (0-20 pts) ─────────────────────────────────────────
    ma60_dev: float | None = None
    if n >= 60:
        ma60     = _sma(closes, 60)
        ma60_dev = (closes[-1] - ma60) / ma60 * 100 if ma60 > 0 else 0.0
        if   ma60_dev > 30: ma60_pts = 20; reasons.append(f"收盤距 MA60 偏離 +{ma60_dev:.1f}%（極度偏高）"); warning_flags.append("嚴重偏離MA60")
        elif ma60_dev > 20: ma60_pts = 14; reasons.append(f"收盤距 MA60 偏離 +{ma60_dev:.1f}%（明顯偏高）")
        elif ma60_dev > 10: ma60_pts =  8; reasons.append(f"收盤距 MA60 偏離 +{ma60_dev:.1f}%（偏高）")
        else:               ma60_pts =  0
        score += ma60_pts
    else:
        ma60 = None

    # ── 3. 5-day gain (0-20 pts) ─────────────────────────────────────────────
    gain5: float | None = None
    if n >= 6 and closes[-6]:
        gain5 = (closes[-1] - closes[-6]) / closes[-6] * 100
        if   gain5 > 20: g5_pts = 20; reasons.append(f"近 5 日漲幅 +{gain5:.1f}%（短線過熱）"); warning_flags.append("短線過熱")
        elif gain5 > 15: g5_pts = 16; reasons.append(f"近 5 日漲幅 +{gain5:.1f}%（漲勢偏快）")
        elif gain5 > 10: g5_pts = 12; reasons.append(f"近 5 日漲幅 +{gain5:.1f}%（強勢上漲）")
        elif gain5 >  5: g5_pts =  6; reasons.append(f"近 5 日漲幅 +{gain5:.1f}%")
        else:            g5_pts =  0
        score += g5_pts

    # ── 4. RSI overbought (0-15 pts) ─────────────────────────────────────────
    rsi = _rsi_simple(closes)
    if rsi is not None:
        if   rsi > 80: rsi_pts = 15; reasons.append(f"RSI {rsi:.0f}（嚴重超買）"); warning_flags.append("嚴重超買")
        elif rsi > 75: rsi_pts = 11; reasons.append(f"RSI {rsi:.0f}（超買）");     warning_flags.append("超買")
        elif rsi > 70: rsi_pts =  7; reasons.append(f"RSI {rsi:.0f}（偏高）")
        elif rsi > 65: rsi_pts =  3; reasons.append(f"RSI {rsi:.0f}（略偏高）")
        else:          rsi_pts =  0
        score += rsi_pts

    # ── 5. 爆量長上影 (0 or 10 pts) ──────────────────────────────────────────
    avg20_vol = _sma(volumes, 20)
    body      = abs(closes[-1] - opens[-1])
    u_shad    = highs[-1] - max(closes[-1], opens[-1])
    if avg20_vol > 0 and volumes[-1] > avg20_vol * 2.0 and body > 0 and u_shad > body * 1.5:
        score += 10
        reasons.append("爆量長上影線（量大收縮，可能為主力出貨訊號）")
        warning_flags.append("爆量長上影")

    # ── 6. High-but-weak close (0 or 5 pts) ──────────────────────────────────
    intraday_range = highs[-1] - lows[-1]
    if intraday_range > 0:
        close_pos = (closes[-1] - lows[-1]) / intraday_range
        if close_pos < 0.2 and highs[-1] > _sma(highs, 5) * 1.02:
            score += 5
            reasons.append(f"沖高後收低（收盤位在當日區間低 {close_pos * 100:.0f}%）")
            warning_flags.append("沖高收低")

    score = min(100, max(0, score))

    if   score <= 25: level = "LOW";     level_label = "低追價風險";  color = "#3fb950"
    elif score <= 50: level = "MEDIUM";  level_label = "中等追價風險"; color = "#e3b341"
    elif score <= 75: level = "HIGH";    level_label = "高追價風險";  color = "#f0883e"
    else:             level = "EXTREME"; level_label = "切勿追高";    color = "#f85149"

    action_map = {
        "LOW":     "風險可控，可依訊號介入",
        "MEDIUM":  "建議等待拉回 MA20 後介入，或小量試單",
        "HIGH":    "追價風險高，建議等待整理回測後再進場",
        "EXTREME": "極度追高，建議完全迴避，等待大幅回調再評估",
    }

    if not reasons:
        reasons.append(f"收盤距 MA20 偏離 {ma20_dev:+.1f}%，RSI {rsi:.0f}" if rsi else f"追價風險評估完成，評分 {score}")

    return {
        "ok":             True,
        "score":          score,
        "level":          level,
        "level_label":    level_label,
        "level_color":    color,
        "reasons":        reasons,
        "suggested_action": action_map[level],
        "warning_flags":  warning_flags,
        "detail": {
            "close":        closes[-1],
            "ma20":         round(ma20, 2),
            "ma20_dev_pct": round(ma20_dev, 1),
            "ma60_dev_pct": round(ma60_dev, 1) if ma60_dev is not None else None,
            "gain5_pct":    round(gain5, 1) if gain5 is not None else None,
            "rsi":          rsi,
        },
    }


def _empty_result(msg: str) -> dict:
    return {
        "ok":             False,
        "score":          None,
        "level":          "UNKNOWN",
        "level_label":    "資料不足",
        "level_color":    "#8b949e",
        "reasons":        [msg],
        "suggested_action": "無法評估，請確認股票代碼",
        "warning_flags":  [],
        "detail":         {},
    }
