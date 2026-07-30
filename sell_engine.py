"""
Sell Decision Engine.
Evaluates multiple exit conditions and returns a prioritized sell decision.
Decisions (in priority order): STOP_LOSS > SELL > ROTATE > TRIM > WATCH > NONE
"""
from __future__ import annotations

import math

from ohlcv_utils import sanitize_ohlcv


def _sma(lst: list, n: int) -> float:
    tail = [v for v in lst[-n:] if v and v > 0]
    return sum(tail) / len(tail) if tail else 0.0


def calc_sell_decision(
    ohlcv: dict,
    cost: float,
    holding_days: int | None = 0,
    stop_pct: float = 8.0,
    trail_pct: float = 15.0,
    profit_target_pct: float = 20.0,
) -> dict:
    """
    ohlcv:             normalized dict with closes/opens/highs/lows/volumes
    cost:              average cost basis (> 0)
    holding_days:      calendar days held
    stop_pct:          fixed stop-loss % below cost
    trail_pct:         trailing stop % from peak close
    profit_target_pct: partial-profit trigger % above cost
    """
    # 清洗缺值，避免 None 讓比較拋 TypeError、NaN 讓決策靜默變成「繼續持有」
    ohlcv = sanitize_ohlcv(ohlcv) or {}

    # holding_days 可為 None（買進日期缺漏）；歸零使時間停損不觸發，
    # 避免每個呼叫端各自防護時漏掉。
    if holding_days is None:
        holding_days = 0

    closes  = ohlcv.get("closes",  [])
    volumes = ohlcv.get("volumes", [])
    n = len(closes)

    # cost 可能為 None／NaN／字串（來自使用者輸入或外部資料），
    # NaN <= 0 為 False 會讓後續計算全部變成 NaN 卻回報 ok=True。
    try:
        cost = float(cost)
    except (TypeError, ValueError):
        return _empty_result("資料不足或未提供成本")
    if n < 5 or not math.isfinite(cost) or cost <= 0:
        return _empty_result("資料不足或未提供成本")

    current = closes[-1]
    pnl_pct = (current - cost) / cost * 100

    triggered: list[str] = []
    reasons:   list[str] = []
    warning_flags: list[str] = []

    stop_price   = cost * (1 - stop_pct / 100)
    profit_price = cost * (1 + profit_target_pct / 100)
    max_close    = max(closes)
    trail_price  = max_close * (1 - trail_pct / 100)
    ma20         = _sma(closes, 20)
    ma5          = _sma(closes, 5)

    # ── 1. Fixed stop-loss ────────────────────────────────────────────────────
    if current <= stop_price:
        triggered.append("STOP_LOSS")
        reasons.append(f"觸發固定停損：現價 {current:.2f} ≤ 停損價 {stop_price:.2f}（成本 -{stop_pct:.0f}%）")
        warning_flags.append("停損觸發")

    # ── 2. Trailing stop (only when we've had gains) ──────────────────────────
    if current <= trail_price and max_close > cost * 1.05:
        triggered.append("SELL")
        reasons.append(
            f"觸發移動停利：從高點 {max_close:.2f} 回落超過 {trail_pct:.0f}%"
            f"（停利線 {trail_price:.2f}）"
        )
        warning_flags.append("移動停利")

    # ── 3. Partial profit target ──────────────────────────────────────────────
    if current >= profit_price:
        days_above = sum(1 for c in closes[-10:] if c >= profit_price)
        if days_above >= 2:
            triggered.append("TRIM")
            reasons.append(f"已達獲利目標 +{profit_target_pct:.0f}%，建議分批獲利了結")
        else:
            triggered.append("WATCH")
            reasons.append(f"接近獲利目標 +{profit_target_pct:.0f}%，留意量縮或上影訊號")

    # ── 4. Time stop ──────────────────────────────────────────────────────────
    if holding_days > 90 and pnl_pct < 0:
        triggered.append("ROTATE")
        reasons.append(f"持有 {holding_days} 天仍虧損 {pnl_pct:.1f}%，建議換股")
        warning_flags.append("長期套牢")
    elif holding_days > 60 and pnl_pct < 5:
        triggered.append("WATCH")
        reasons.append(f"持有 {holding_days} 天報酬僅 {pnl_pct:+.1f}%，資金效率偏低")
        warning_flags.append("時間成本偏高")

    # ── 5. Relative / trend weakness ─────────────────────────────────────────
    if ma5 > 0 and ma20 > 0:
        if current < ma20 and ma5 < ma20 and pnl_pct < -5:
            triggered.append("SELL")
            reasons.append("跌破 MA20 且 5 日均線走弱，趨勢轉弱")
            warning_flags.append("趨勢轉弱")
        elif current < ma20:
            triggered.append("WATCH")
            reasons.append(f"收盤跌破 MA20（{ma20:.2f}），需觀察能否回升")

    # ── Final decision (priority order) ──────────────────────────────────────
    priority = ["STOP_LOSS", "SELL", "ROTATE", "TRIM", "WATCH", "NONE"]
    decision = "NONE"
    for p in priority:
        if p in triggered:
            decision = p
            break

    if not reasons:
        if pnl_pct >= 0:
            reasons.append(f"持倉獲利 {pnl_pct:+.1f}%，無明顯賣出訊號")
        else:
            reasons.append(f"持倉虧損 {pnl_pct:+.1f}%，尚未觸發停損線")

    _LABELS  = {"NONE": "無動作", "WATCH": "觀察", "TRIM": "減倉",
                "SELL": "賣出",   "STOP_LOSS": "停損", "ROTATE": "換股"}
    _COLORS  = {"NONE": "#3fb950", "WATCH": "#58a6ff", "TRIM": "#e3b341",
                "SELL": "#f0883e", "STOP_LOSS": "#f85149", "ROTATE": "#bc8cff"}
    _ACTIONS = {
        "NONE":      "繼續持有，無操作訊號",
        "WATCH":     "留意觀察，可設定提醒",
        "TRIM":      "建議分批減倉 30–50%",
        "SELL":      "建議賣出或大幅減倉",
        "STOP_LOSS": "觸發停損，應立即出場",
        "ROTATE":    "建議換股，將資金轉向強勢標的",
    }
    _URGENCY = {"NONE": 0, "WATCH": 25, "TRIM": 50, "SELL": 70, "STOP_LOSS": 100, "ROTATE": 60}

    return {
        "ok":             True,
        "engine":         "sell_decision",
        "score":          _URGENCY.get(decision, 0),
        "level":          decision,
        "level_label":    _LABELS.get(decision, decision),
        "level_color":    _COLORS.get(decision, "#8b949e"),
        "reasons":        reasons,
        "suggested_action": _ACTIONS.get(decision, ""),
        "warning_flags":  warning_flags,
        # sell_decision-specific ──────────────────────────────────────────────
        "decision":         decision,
        "decision_label":   _LABELS.get(decision, decision),
        "decision_color":   _COLORS.get(decision, "#8b949e"),
        "detail": {
            "current_price":  current,
            "cost_basis":     cost,
            "pnl_pct":        round(pnl_pct, 2),
            "holding_days":   holding_days,
            "stop_price":     round(stop_price, 2),
            "trail_price":    round(trail_price, 2),
            "profit_price":   round(profit_price, 2),
            "max_close":      round(max_close, 2),
            "ma20":           round(ma20, 2) if ma20 else None,
        },
    }


def _empty_result(msg: str) -> dict:
    return {
        "ok":             False,
        "engine":         "sell_decision",
        "score":          0,
        "level":          "NONE",
        "level_label":    "資料不足",
        "level_color":    "#8b949e",
        "decision":       "NONE",
        "decision_label": "資料不足",
        "decision_color": "#8b949e",
        "reasons":        [msg],
        "suggested_action": "請確認持倉資料與成本",
        "warning_flags":  [],
        "detail":         {},
    }
