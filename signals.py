"""
Real-time entry / exit signal detection engine.
Analyses computed indicators and returns actionable signals with key price levels.
"""
from __future__ import annotations
import math
from datetime import datetime, timezone, timedelta

# ── Market hours helpers ───────────────────────────────────────────────────────

def market_status(symbol: str) -> dict:
    """Return whether the primary market for this symbol is open."""
    now_utc = datetime.now(timezone.utc)
    sym = symbol.upper()

    if sym.endswith(".TW") or sym.endswith(".TWO"):
        # Taiwan Stock Exchange: Mon-Fri 09:00-13:30 CST (UTC+8)
        cst = now_utc + timedelta(hours=8)
        open_t  = cst.replace(hour=9,  minute=0,  second=0, microsecond=0)
        close_t = cst.replace(hour=13, minute=30, second=0, microsecond=0)
        is_open = cst.weekday() < 5 and open_t <= cst <= close_t
        tz_label = "TSE"
        local_time = cst.strftime("%H:%M")
    else:
        # US markets: Mon-Fri 09:30-16:00 ET (UTC-4 or -5)
        # Use UTC-4 (EDT) for simplicity
        et = now_utc - timedelta(hours=4)
        open_t  = et.replace(hour=9,  minute=30, second=0, microsecond=0)
        close_t = et.replace(hour=16, minute=0,  second=0, microsecond=0)
        is_open = et.weekday() < 5 and open_t <= et <= close_t
        tz_label = "NYSE"
        local_time = et.strftime("%H:%M")

    return {"is_open": is_open, "exchange": tz_label, "local_time": local_time}


# ── Individual signal checks ───────────────────────────────────────────────────

def _rsi_signal(rsi: float) -> tuple[int, str] | None:
    """Returns (score, reason). score > 0 = bullish, < 0 = bearish."""
    if rsi is None:
        return None
    if rsi < 30:
        return (2, f"RSI {rsi:.1f} 進入超賣區，反彈機率高")
    if rsi < 40:
        return (1, f"RSI {rsi:.1f} 偏低，多頭動能醞釀")
    if rsi > 70:
        return (-2, f"RSI {rsi:.1f} 進入超買區，回調風險大")
    if rsi > 60:
        return (-1, f"RSI {rsi:.1f} 偏高，注意獲利了結")
    return (0, f"RSI {rsi:.1f} 中性")


def _macd_signal(macd_hist: float, macd_line: float, signal_line: float) -> tuple[int, str] | None:
    if macd_hist is None:
        return None
    if macd_hist > 0 and macd_line > signal_line:
        strength = 2 if macd_hist > abs(macd_line) * 0.1 else 1
        return (strength, f"MACD 柱狀圖為正（{macd_hist:+.4f}），多頭動能")
    if macd_hist < 0 and macd_line < signal_line:
        strength = -2 if abs(macd_hist) > abs(macd_line) * 0.1 else -1
        return (strength, f"MACD 柱狀圖為負（{macd_hist:+.4f}），空頭動能")
    return (0, "MACD 訊號中性")


def _bb_signal(bb_pct_b: float, price: float, bb_upper: float, bb_lower: float) -> tuple[int, str] | None:
    if bb_pct_b is None:
        return None
    if bb_pct_b < 0.1:
        return (2, f"價格貼近布林下軌（{bb_lower:.2f}），超賣反彈訊號")
    if bb_pct_b < 0.25:
        return (1, f"價格在布林下半區，偏低位置")
    if bb_pct_b > 0.9:
        return (-2, f"價格貼近布林上軌（{bb_upper:.2f}），超買壓回訊號")
    if bb_pct_b > 0.75:
        return (-1, f"價格在布林上半區，偏高位置")
    return (0, "布林通道中性位置")


def _ma_signal(price: float, ma_items: list) -> list[tuple[int, str]]:
    results = []
    ma20 = next((m for m in ma_items if "20" in m["label"]), None)
    ma60 = next((m for m in ma_items if "60" in m["label"]), None)
    ma200 = next((m for m in ma_items if "200" in m["label"]), None)

    if ma20:
        if price > ma20["value"]:
            results.append((1, f"價格站上 MA20（{ma20['value']:.2f}），短線多頭"))
        else:
            results.append((-1, f"價格跌破 MA20（{ma20['value']:.2f}），短線空頭"))
    if ma60:
        if price > ma60["value"]:
            results.append((1, f"價格站上 MA60（{ma60['value']:.2f}），中線多頭"))
        else:
            results.append((-1, f"價格跌破 MA60（{ma60['value']:.2f}），中線空頭"))
    if ma200 and ma200["value"]:
        if price > ma200["value"]:
            results.append((1, f"價格站上 MA200（{ma200['value']:.2f}），長線多頭"))
        else:
            results.append((-1, f"價格跌破 MA200（{ma200['value']:.2f}），長線轉空"))
    return results


def _adx_signal(adx: float, di_plus: float, di_minus: float) -> tuple[int, str] | None:
    if adx is None:
        return None
    if adx < 20:
        return (0, f"ADX {adx:.1f} 偏低，市場無明顯趨勢")
    if di_plus > di_minus:
        strength = 2 if adx > 35 else 1
        return (strength, f"ADX {adx:.1f} 趨勢強，+DI>{'-'}DI 多頭方向")
    else:
        strength = -2 if adx > 35 else -1
        return (strength, f"ADX {adx:.1f} 趨勢強，-DI>+DI 空頭方向")


def _stoch_signal(k: float, d: float) -> tuple[int, str] | None:
    if k is None:
        return None
    if k < 20 and d < 20:
        return (2, f"隨機指標 K={k:.1f}/D={d:.1f} 雙雙超賣，反彈機率高")
    if k > 80 and d > 80:
        return (-2, f"隨機指標 K={k:.1f}/D={d:.1f} 雙雙超買，注意回調")
    if k > d and k < 80:
        return (1, f"K 線上穿 D 線（{k:.1f}>{d:.1f}），短線轉多")
    if k < d and k > 20:
        return (-1, f"K 線下穿 D 線（{k:.1f}<{d:.1f}），短線轉空")
    return (0, f"隨機指標中性（K={k:.1f}）")


def _vol_signal(vol_ratio: float) -> tuple[int, str] | None:
    if vol_ratio is None:
        return None
    if vol_ratio >= 2.0:
        return (1, f"成交量爆增（均量 {vol_ratio:.1f}倍），主力動作明顯")
    if vol_ratio >= 1.4:
        return (1, f"成交量放大（均量 {vol_ratio:.1f}倍），價量配合")
    if vol_ratio <= 0.5:
        return (-1, f"成交量大幅萎縮（均量 {vol_ratio:.1f}倍），觀望")
    return (0, f"成交量正常（{vol_ratio:.1f}x）")


# ── Key price levels ───────────────────────────────────────────────────────────

def _key_levels(price: float, ind: dict, ma_items: list) -> dict:
    bb_upper = ind.get("bb_upper") or price * 1.05
    bb_lower = ind.get("bb_lower") or price * 0.95
    bb_mid   = ind.get("bb_mid")   or price

    ma20  = next((m["value"] for m in ma_items if "20"  in m["label"]), None)
    ma60  = next((m["value"] for m in ma_items if "60"  in m["label"]), None)
    ma200 = next((m["value"] for m in ma_items if "200" in m["label"]), None)

    # Supports (below price)
    supports = sorted([v for v in [bb_lower, ma20, ma60, ma200, bb_mid] if v and v < price], reverse=True)
    # Resistances (above price)
    resistances = sorted([v for v in [bb_upper, ma20, ma60, ma200, bb_mid] if v and v > price])

    stop_loss = round(supports[0] * 0.99, 2) if supports else round(price * 0.95, 2)
    risk = price - stop_loss
    tp1 = round(price + risk * 1.5, 2)
    tp2 = round(price + risk * 2.5, 2)
    rr = round(risk / price * 100, 2) if price > 0 else 0

    return {
        "supports":    [round(v, 2) for v in supports[:3]],
        "resistances": [round(v, 2) for v in resistances[:3]],
        "stop_loss":   stop_loss,
        "take_profit": [tp1, tp2],
        "risk_pct":    rr,
        "reward_pct":  round(risk * 1.5 / price * 100, 2) if price > 0 else 0,
    }


# ── Overall signal ─────────────────────────────────────────────────────────────

SIGNAL_MAP = {
    (True,  5): ("強力買入", "bullish",      "🚀 多指標共振看多，強烈進場訊號"),
    (True,  4): ("積極買入", "bullish",      "📈 多數指標看多，進場機率高"),
    (True,  3): ("溫和買入", "mild-bullish", "↗ 偏多訊號，可考慮分批進場"),
    (True,  2): ("輕度買入", "mild-bullish", "🔼 略偏多，建議小倉位試水"),
    (True,  1): ("觀察買入", "neutral",      "👀 多頭跡象初現，持續觀察"),
    (False, 1): ("觀察賣出", "neutral",      "👀 空頭跡象初現，持續觀察"),
    (False, 2): ("輕度賣出", "mild-bearish", "🔽 略偏空，考慮減倉"),
    (False, 3): ("溫和賣出", "mild-bearish", "↘ 偏空訊號，建議減碼或出場"),
    (False, 4): ("積極賣出", "bearish",      "📉 多數指標看空，建議出場"),
    (False, 5): ("強力賣出", "bearish",      "🔻 多指標共振看空，強烈出場訊號"),
}


def detect(payload: dict) -> dict:
    ind      = payload.get("indicators", {})
    ma_items = payload.get("ma_analysis", [])
    price    = payload.get("price", 0)
    symbol   = payload.get("symbol", "")
    vol_ratio = payload.get("volume_ratio", 1)

    raw_signals = []

    # Collect all sub-signals
    for fn_result in [
        _rsi_signal(ind.get("rsi")),
        _macd_signal(ind.get("macd_hist"), ind.get("macd"), ind.get("macd_signal")),
        _bb_signal(ind.get("bb_pct_b"), price, ind.get("bb_upper"), ind.get("bb_lower")),
        _adx_signal(ind.get("adx"), ind.get("di_plus"), ind.get("di_minus")),
        _stoch_signal(ind.get("stoch_k"), ind.get("stoch_d")),
        _vol_signal(vol_ratio),
    ]:
        if fn_result:
            raw_signals.append(fn_result)

    raw_signals.extend(_ma_signal(price, ma_items))

    # Tally
    bull = [s for s in raw_signals if s[0] > 0]
    bear = [s for s in raw_signals if s[0] < 0]
    bull_score = sum(s[0] for s in bull)
    bear_score = abs(sum(s[0] for s in bear))

    is_bullish = bull_score >= bear_score
    net_strength = min(max(bull_score if is_bullish else bear_score, 1), 5)

    key = (is_bullish, net_strength)
    signal_name, signal_class, description = SIGNAL_MAP.get(
        key, ("中性觀望", "neutral", "指標分歧，建議觀察")
    )

    # Top reasons (strongest signals first)
    active_signals = sorted(raw_signals, key=lambda x: abs(x[0]), reverse=True)
    reasons_bull = [s[1] for s in active_signals if s[0] > 0][:4]
    reasons_bear = [s[1] for s in active_signals if s[0] < 0][:3]

    levels = _key_levels(price, ind, ma_items)
    mkt    = market_status(symbol)

    # Confluence score 0-100
    total = bull_score + bear_score or 1
    score = round((bull_score / total) * 100) if is_bullish else round((bear_score / total) * 100)

    return {
        "signal":       signal_name,
        "signal_class": signal_class,
        "description":  description,
        "is_bullish":   is_bullish,
        "bull_count":   len(bull),
        "bear_count":   len(bear),
        "bull_score":   bull_score,
        "bear_score":   bear_score,
        "confluence":   score,
        "reasons_bull": reasons_bull,
        "reasons_bear": reasons_bear,
        "levels":       levels,
        "market":       mkt,
        "price":        price,
        "symbol":       symbol.upper(),
        "updated_at":   datetime.now(timezone.utc).strftime("%H:%M:%S UTC"),
    }
