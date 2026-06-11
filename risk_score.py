"""
風險分數模組 (Risk Score) — 0~100 分
--------------------------------------
⚠️  分數越高 = 風險越大（與其他模組相反）

評估進場風險，包含：
  · ATR 停損距離（波動率）
  · 追高風險（MA 偏離、RSI、5日漲幅）
  · 乖離率過高
  · 出貨訊號計數
  · 最大回撤潛力
  · 支撐壓力結構

輸出：
  risk_score    : 0-100（高分 = 高風險）
  stop_loss     : 建議停損價
  entry_zone    : [下限, 上限]  建議進場區
  watch_zone    : [下限, 上限]  只觀察區
  invalidation  : str  失效條件
"""
from __future__ import annotations

import math
import statistics


def _clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))


def _sma(lst: list[float], n: int) -> float | None:
    valid = [v for v in lst[-n:] if v and v > 0]
    if len(valid) < n // 2:
        return None
    return sum(valid) / len(valid)


def _rsi(closes: list[float], period: int = 14) -> float | None:
    if len(closes) < period + 1:
        return None
    deltas = [closes[i] - closes[i - 1] for i in range(len(closes) - period, len(closes))]
    gains  = sum(d for d in deltas if d > 0)
    losses = sum(-d for d in deltas if d < 0)
    avg_g  = gains  / period
    avg_l  = losses / period
    if avg_l == 0:
        return 100.0 if avg_g > 0 else 50.0
    return round(100 - 100 / (1 + avg_g / avg_l), 1)


def _atr(highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> float | None:
    """Average True Range"""
    n = min(len(highs), len(lows), len(closes))
    if n < period + 1:
        return None
    trs: list[float] = []
    for i in range(n - period, n):
        hi, lo, prev_c = highs[i], lows[i], closes[i - 1]
        tr = max(hi - lo, abs(hi - prev_c), abs(lo - prev_c))
        trs.append(tr)
    return sum(trs) / len(trs) if trs else None


def _max_drawdown(closes: list[float], window: int = 60) -> float:
    """近 window 日最大回撤 (%)"""
    w = closes[-window:] if len(closes) >= window else closes
    peak = w[0]
    mdd  = 0.0
    for p in w:
        if p > peak:
            peak = p
        dd = (peak - p) / peak * 100 if peak > 0 else 0
        mdd = max(mdd, dd)
    return mdd


def _support_resistance(closes: list[float], highs: list[float], lows: list[float]) -> dict:
    """
    簡易支撐壓力識別
    取近 20 / 60 日的重要高低點。
    """
    price = closes[-1]
    n20_hi = max(highs[-20:]) if len(highs) >= 20 else None
    n20_lo = min(lows[-20:])  if len(lows)  >= 20 else None
    n60_hi = max(highs[-60:]) if len(highs) >= 60 else None
    n60_lo = min(lows[-60:])  if len(lows)  >= 60 else None

    supports    = sorted([v for v in [n20_lo, n60_lo] if v and v < price],   reverse=True)
    resistances = sorted([v for v in [n20_hi, n60_hi] if v and v > price])

    return {
        "supports":    [round(v, 4) for v in supports[:2]],
        "resistances": [round(v, 4) for v in resistances[:2]],
    }


# ── 子分數 ────────────────────────────────────────────────────────────────────

def _score_atr_risk(
    closes: list[float], highs: list[float], lows: list[float]
) -> tuple[float, float | None, list[str]]:
    """
    ATR 波動率風險 (0-25 pts)
    ATR 越大相對股價 → 停損距離越遠 → 風險越高。
    回傳: (pts, atr_value, reasons)
    """
    atr = _atr(highs, lows, closes)
    reasons: list[str] = []
    if atr is None:
        return 12.0, None, []

    price = closes[-1]
    atr_pct = atr / price * 100

    if atr_pct > 8:
        pts = 25; reasons.append(f"ATR {atr_pct:.1f}% 極高，每日波動劇烈，停損空間大")
    elif atr_pct > 5:
        pts = 18; reasons.append(f"ATR {atr_pct:.1f}%，波動偏高，需寬停損")
    elif atr_pct > 3:
        pts = 12; reasons.append(f"ATR {atr_pct:.1f}%，波動正常")
    elif atr_pct > 1.5:
        pts = 6
    else:
        pts = 2;  reasons.append(f"ATR {atr_pct:.1f}%，波動很低（注意流動性）")

    return _clamp(pts, 0, 25), atr, reasons


def _score_chase_risk(closes: list[float]) -> tuple[float, list[str]]:
    """
    追高風險 (0-30 pts)
    MA20 偏離 + 5 日漲幅 + RSI 超買
    """
    pts = 0.0
    reasons: list[str] = []

    ma20 = _sma(closes, 20)
    if ma20 and ma20 > 0:
        dev = (closes[-1] - ma20) / ma20 * 100
        if dev > 20:
            pts += 15; reasons.append(f"距 MA20 偏離 +{dev:.1f}%，嚴重追高")
        elif dev > 15:
            pts += 11; reasons.append(f"距 MA20 偏離 +{dev:.1f}%，偏高")
        elif dev > 10:
            pts += 8;  reasons.append(f"距 MA20 偏離 +{dev:.1f}%")
        elif dev > 5:
            pts += 4

    if len(closes) >= 6 and closes[-6] > 0:
        gain5 = (closes[-1] / closes[-6] - 1) * 100
        if gain5 > 20:
            pts += 10; reasons.append(f"5 日漲幅 +{gain5:.1f}%，短線過熱")
        elif gain5 > 15:
            pts += 7;  reasons.append(f"5 日漲幅 +{gain5:.1f}%，漲勢偏快")
        elif gain5 > 10:
            pts += 4;  reasons.append(f"5 日漲幅 +{gain5:.1f}%")

    rsi = _rsi(closes)
    if rsi:
        if rsi > 80:
            pts += 5; reasons.append(f"RSI {rsi:.0f}，嚴重超買")
        elif rsi > 70:
            pts += 3; reasons.append(f"RSI {rsi:.0f}，超買")

    return _clamp(pts, 0, 30), reasons


def _score_drawdown_risk(closes: list[float]) -> tuple[float, list[str]]:
    """
    歷史最大回撤風險 (0-25 pts)
    近 60 日 MDD 越高 → 風險越高。
    """
    reasons: list[str] = []
    mdd = _max_drawdown(closes, 60)

    if mdd > 40:
        pts = 25; reasons.append(f"近 60 日最大回撤 {mdd:.1f}%，高波動歷史")
    elif mdd > 25:
        pts = 18; reasons.append(f"近 60 日最大回撤 {mdd:.1f}%，回撤幅度不小")
    elif mdd > 15:
        pts = 10; reasons.append(f"近 60 日最大回撤 {mdd:.1f}%，正常範圍")
    elif mdd > 8:
        pts = 5
    else:
        pts = 1;  reasons.append(f"近 60 日回撤 {mdd:.1f}%，波動很低")

    return _clamp(pts, 0, 25), reasons


def _score_support_distance(
    closes: list[float], highs: list[float], lows: list[float], atr: float | None
) -> tuple[float, list[str]]:
    """
    支撐距離風險 (0-20 pts)
    距最近支撐越遠 → 停損越深 → 風險越高。
    """
    reasons: list[str] = []
    price = closes[-1]
    ma20  = _sma(closes, 20)
    ma50  = _sma(closes, 50)
    n20_lo = min(lows[-20:]) if len(lows) >= 20 else None

    supports = sorted([v for v in [ma20, ma50, n20_lo] if v and v < price], reverse=True)
    if not supports:
        return 10.0, ["無法識別支撐，風險評估保守處理"]

    nearest_support = supports[0]
    dist_pct = (price - nearest_support) / price * 100

    if dist_pct > 15:
        pts = 20; reasons.append(f"距最近支撐 {dist_pct:.1f}%，停損需設很深")
    elif dist_pct > 8:
        pts = 14; reasons.append(f"距最近支撐 {dist_pct:.1f}%，停損稍遠")
    elif dist_pct > 4:
        pts = 8
    elif dist_pct > 2:
        pts = 4
    else:
        pts = 1;  reasons.append(f"距最近支撐僅 {dist_pct:.1f}%，停損緊密，低風險")

    return _clamp(pts, 0, 20), reasons


# ── 主函數 ────────────────────────────────────────────────────────────────────

def compute(ohlcv: dict) -> dict:
    """
    計算風險分數及停損 / 進場區間。

    ⚠️  score 越高 = 風險越大

    Parameters
    ----------
    ohlcv : 標準化 OHLCV

    Returns
    -------
    dict
        score          : float  0-100（高分 = 高風險）
        risk_level     : str    LOW / MEDIUM / HIGH / EXTREME
        stop_loss      : float  建議停損價
        entry_zone     : list   [低, 高] 進場建議區間（貼近支撐）
        watch_zone     : list   [低, 高] 只觀察區間（當前價格區間）
        invalidation   : str    失效條件
        sub_scores     : dict
        reasons        : list
        signals        : dict
        confidence     : str
    """
    closes  = [v for v in (ohlcv.get("closes")  or []) if v and v > 0]
    highs   = [v for v in (ohlcv.get("highs")   or []) if v and v > 0]
    lows    = [v for v in (ohlcv.get("lows")    or []) if v and v > 0]
    volumes = [v for v in (ohlcv.get("volumes") or []) if v is not None and v >= 0]

    n = min(len(closes), len(highs), len(lows))
    if n < 14:
        return {
            "score": 60, "risk_level": "HIGH",
            "stop_loss": None, "entry_zone": None, "watch_zone": None,
            "invalidation": "資料不足無法評估",
            "sub_scores": {}, "reasons": ["風險資料不足（需 ≥14 根K線）"],
            "signals": {}, "confidence": "LOW",
        }

    closes = closes[-n:]
    highs  = highs[-n:]
    lows   = lows[-n:]
    price  = closes[-1]

    all_reasons: list[str] = []

    # 子分數 1: ATR (0-25)
    atr_pts, atr_val, atr_r = _score_atr_risk(closes, highs, lows)
    all_reasons.extend(atr_r[:1])

    # 子分數 2: 追高風險 (0-30)
    chase_pts, chase_r = _score_chase_risk(closes)
    all_reasons.extend(chase_r[:2])

    # 子分數 3: 歷史回撤 (0-25)
    dd_pts, dd_r = _score_drawdown_risk(closes)
    all_reasons.extend(dd_r[:1])

    # 子分數 4: 支撐距離 (0-20)
    sup_pts, sup_r = _score_support_distance(closes, highs, lows, atr_val)
    all_reasons.extend(sup_r[:1])

    raw_risk = atr_pts + chase_pts + dd_pts + sup_pts  # max = 100
    risk_score = _clamp(raw_risk)

    # ── 停損 / 進場區計算 ─────────────────────────────────────────────────────
    atr_val = atr_val or price * 0.02
    ma20    = _sma(closes, 20) or price * 0.95
    ma50    = _sma(closes, 50) or price * 0.92
    n20_lo  = min(lows[-20:]) if len(lows) >= 20 else price * 0.92

    # 停損 = max(支撐, ma20 - 0.5×ATR), 再 -1%
    support_floor = max([v for v in [ma20, n20_lo] if v < price], default=price * 0.93)
    stop_loss     = round(max(support_floor - atr_val * 0.5, price * 0.87), 4)

    # 進場區（貼近 MA20 / 當前回測區）
    entry_low  = round(max(stop_loss * 1.015, ma20 * 0.99), 4)
    entry_high = round(min(price, ma20 * 1.03), 4)

    # 觀察區（當前 ±ATR）
    watch_low  = round(price - atr_val * 1.5, 4)
    watch_high = round(price + atr_val * 2.0, 4)

    # 目標價
    risk_amt = price - stop_loss
    target1  = round(price + risk_amt * 1.5, 4)
    target2  = round(price + risk_amt * 3.0, 4)

    # 失效條件
    ma50_val = _sma(closes, 50)
    if ma50_val and price > ma50_val:
        invalidation = f"收盤跌破 MA50（{ma50_val:.2f}）或 {stop_loss:.2f} 停損位"
    else:
        invalidation = f"收盤跌破 {stop_loss:.2f}（停損位）或前低 {n20_lo:.2f}"

    # 風險等級
    if risk_score <= 25:
        risk_level = "LOW"
    elif risk_score <= 50:
        risk_level = "MEDIUM"
    elif risk_score <= 75:
        risk_level = "HIGH"
    else:
        risk_level = "EXTREME"

    confidence = "HIGH" if n >= 60 else "MEDIUM" if n >= 20 else "LOW"
    if ohlcv.get("is_demo"):
        confidence = "LOW"

    sr = _support_resistance(closes, highs, lows)

    return {
        "score":       round(risk_score, 1),
        "risk_level":  risk_level,
        "stop_loss":   stop_loss,
        "entry_zone":  [entry_low, entry_high],
        "watch_zone":  [watch_low, watch_high],
        "target_zone": [target1, target2],
        "invalidation": invalidation,
        "sub_scores": {
            "atr_risk":          round(atr_pts,   1),
            "chase_risk":        round(chase_pts, 1),
            "drawdown_risk":     round(dd_pts,    1),
            "support_distance":  round(sup_pts,   1),
        },
        "reasons":  all_reasons[:5],
        "signals": {
            "extreme_chase":   chase_pts >= 22,
            "high_volatility": atr_pts  >= 18,
            "deep_drawdown":   dd_pts   >= 18,
            "far_from_support": sup_pts >= 14,
        },
        "confidence": confidence,
        "detail": {
            "price":       round(price,  4),
            "atr":         round(atr_val, 4) if atr_val else None,
            "atr_pct":     round(atr_val / price * 100, 2) if atr_val else None,
            "ma20":        round(_sma(closes, 20) or 0, 4),
            "ma50":        round(ma50_val or 0, 4),
            "mdd_60d":     round(_max_drawdown(closes, 60), 1),
            "rsi":         _rsi(closes),
            "supports":    sr["supports"],
            "resistances": sr["resistances"],
        },
    }
