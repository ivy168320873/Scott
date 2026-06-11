"""
趨勢分數模組 (Trend Score) — 0~100 分
---------------------------------------
評估股價趨勢結構強度，包含：
  · 均線排列（MA5/10/20/50/200）
  · 均線多頭堆疊
  · 創新高（20d / 60d / 120d）
  · 突破前高或箱型區間

輸入：標準化 OHLCV dict
輸出：{ score, sub_scores, signals, reasons, confidence, detail }
"""
from __future__ import annotations

import statistics


# ── 內部工具 ──────────────────────────────────────────────────────────────────

def _sma(closes: list[float], n: int) -> float | None:
    """Simple moving average；若資料不足回傳 None。"""
    valid = [v for v in closes[-n:] if v and v > 0]
    if len(valid) < max(1, n // 2):
        return None
    return sum(valid) / len(valid)


def _ema(closes: list[float], n: int) -> float | None:
    """Exponential moving average。"""
    valid = [v for v in closes if v and v > 0]
    if len(valid) < n:
        return None
    k = 2 / (n + 1)
    ema = sum(valid[:n]) / n
    for p in valid[n:]:
        ema = p * k + ema * (1 - k)
    return ema


def _clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))


def _new_high(closes: list[float], period: int) -> bool:
    """最新收盤是否創 period 日新高（不含今日）。"""
    if len(closes) < period + 1:
        return False
    return closes[-1] >= max(closes[-(period + 1):-1])


def _consolidation_range(closes: list[float], period: int = 20) -> tuple[float, float] | None:
    """計算近 period 日的箱型區間（高點, 低點）。"""
    window = [v for v in closes[-period:] if v and v > 0]
    if len(window) < period // 2:
        return None
    hi = max(window)
    lo = min(window)
    if lo <= 0:
        return None
    # 若高低差 < 15%，視為箱型整理
    if (hi - lo) / lo < 0.15:
        return hi, lo
    return None


# ── 子分數計算 ─────────────────────────────────────────────────────────────────

def _score_ma_position(price: float, mas: dict[int, float | None]) -> tuple[float, list[str]]:
    """
    價格站上均線得分 (0-30 pts)
    MA5=5, MA10=5, MA20=8, MA50=7, MA200=5
    """
    pts = 0.0
    reasons: list[str] = []
    weights = {5: 5, 10: 5, 20: 8, 50: 7, 200: 5}
    for period, w in weights.items():
        ma = mas.get(period)
        if ma is None:
            continue
        if price > ma:
            pts += w
            reasons.append(f"收盤 {price:.2f} 站上 MA{period} {ma:.2f}")
        else:
            reasons.append(f"收盤 {price:.2f} 跌破 MA{period} {ma:.2f}")
    return _clamp(pts, 0, 30), reasons


def _score_ma_alignment(mas: dict[int, float | None]) -> tuple[float, list[str]]:
    """
    均線多頭排列得分 (0-20 pts)
    完整多頭排列（MA5>MA10>MA20>MA50>MA200）最高 20 分。
    """
    ordered_periods = [5, 10, 20, 50, 200]
    available = [(p, mas[p]) for p in ordered_periods if mas.get(p) is not None]
    if len(available) < 2:
        return 0.0, ["均線資料不足，無法判斷多頭排列"]

    pairs_total = len(available) - 1
    pairs_bull  = 0
    reasons: list[str] = []

    for i in range(len(available) - 1):
        p1, v1 = available[i]
        p2, v2 = available[i + 1]
        if v1 > v2:
            pairs_bull += 1
        else:
            reasons.append(f"MA{p1} {v1:.2f} < MA{p2} {v2:.2f}，排列受損")

    ratio = pairs_bull / pairs_total
    pts   = round(ratio * 20, 1)

    if pairs_bull == pairs_total:
        reasons.insert(0, f"完整多頭排列（{'/'.join(str(p) for p, _ in available)}），趨勢最強")
    elif pairs_bull > pairs_total / 2:
        reasons.insert(0, f"部分多頭排列（{pairs_bull}/{pairs_total} 層），趨勢偏多")

    return _clamp(pts, 0, 20), reasons


def _score_new_highs(closes: list[float]) -> tuple[float, list[str]]:
    """
    創新高得分 (0-20 pts)
    20日=8, 60日=7, 120日=5
    """
    pts = 0.0
    reasons: list[str] = []
    checks = [(20, 8, "20 日"), (60, 7, "60 日"), (120, 5, "120 日")]
    for period, w, label in checks:
        if _new_high(closes, period):
            pts += w
            reasons.append(f"創 {label} 新高（{closes[-1]:.2f}），動能強勁")
        else:
            if len(closes) > period:
                dist = (max(closes[-(period + 1):-1]) - closes[-1]) / closes[-1] * 100
                if dist < 3:
                    reasons.append(f"距 {label} 高點僅 {dist:.1f}%，接近突破")
    return _clamp(pts, 0, 20), reasons


def _score_breakout(closes: list[float], highs: list[float]) -> tuple[float, list[str]]:
    """
    突破前高 / 箱型得分 (0-30 pts)
    含假突破懲罰。
    """
    pts = 0.0
    reasons: list[str] = []

    price = closes[-1]
    n = len(closes)

    # 3 個月前高突破（約 65 日）
    if n >= 70:
        lookback_high = max(closes[-70:-5])
        if price > lookback_high * 1.005:
            pts += 15
            reasons.append(f"突破近 65 日前高 {lookback_high:.2f}，有效放量突破")

    # 箱型整理突破
    box = _consolidation_range(closes, 20)
    if box:
        box_hi, box_lo = box
        if price > box_hi * 1.005:
            pts += 12
            reasons.append(f"突破 20 日箱型上緣 {box_hi:.2f}，盤整結束")
        elif price < box_lo * 0.995:
            pts -= 10
            reasons.append(f"跌破 20 日箱型下緣 {box_lo:.2f}，盤整向下突破")

    # 假突破偵測：今日收盤明顯低於當日高點
    if n >= 3 and len(highs) >= 3:
        today_high  = highs[-1]
        today_close = closes[-1]
        body_range  = today_high - min(closes[-1], closes[-2])
        upper_shadow = today_high - today_close
        if body_range > 0 and upper_shadow / body_range > 0.6:
            pts -= 8
            reasons.append(f"日線長上影線（{upper_shadow / today_close * 100:.1f}% 回吐），假突破風險")

    return _clamp(pts, 0, 30), reasons


# ── 主函數 ────────────────────────────────────────────────────────────────────

def compute(ohlcv: dict) -> dict:
    """
    計算趨勢分數。

    Parameters
    ----------
    ohlcv : dict  標準化 OHLCV，需含 closes / highs / lows / volumes / timestamps

    Returns
    -------
    dict
        score        : float  0-100
        sub_scores   : dict   各子分數明細
        reasons      : list   說明文字
        signals      : dict   布林訊號
        confidence   : str    HIGH / MEDIUM / LOW
        detail       : dict   均線數值、創高情況等
    """
    closes  = [v for v in (ohlcv.get("closes")  or []) if v and v > 0]
    highs   = [v for v in (ohlcv.get("highs")   or []) if v and v > 0]
    lows    = [v for v in (ohlcv.get("lows")    or []) if v and v > 0]
    volumes = [v for v in (ohlcv.get("volumes") or []) if v is not None and v >= 0]

    n = min(len(closes), len(highs), len(lows))
    if n < 20:
        return {
            "score": 40, "sub_scores": {}, "reasons": ["資料不足（需 ≥20 根K線）"],
            "signals": {}, "confidence": "LOW", "detail": {},
        }

    closes  = closes[-n:]
    highs   = highs[-n:]
    price   = closes[-1]

    # 計算均線
    periods = [5, 10, 20, 50, 200]
    mas: dict[int, float | None] = {p: _sma(closes, p) for p in periods}

    all_reasons: list[str] = []

    # 子分數 1: 均線位置 (0-30)
    ma_pos_pts, ma_pos_r = _score_ma_position(price, mas)
    all_reasons.extend(ma_pos_r[:3])

    # 子分數 2: 均線排列 (0-20)
    ma_ali_pts, ma_ali_r = _score_ma_alignment(mas)
    all_reasons.extend(ma_ali_r[:2])

    # 子分數 3: 創新高 (0-20)
    new_hi_pts, new_hi_r = _score_new_highs(closes)
    all_reasons.extend(new_hi_r[:2])

    # 子分數 4: 突破 (0-30)
    bkout_pts, bkout_r = _score_breakout(closes, highs)
    all_reasons.extend(bkout_r[:2])

    raw = ma_pos_pts + ma_ali_pts + new_hi_pts + bkout_pts
    # 原始最高 = 30+20+20+30 = 100，直接使用
    score = _clamp(raw)

    # Confidence: 資料越多越可靠
    if n >= 200:
        confidence = "HIGH"
    elif n >= 60:
        confidence = "MEDIUM"
    else:
        confidence = "LOW"

    # 若 demo 資料，降低信心
    if ohlcv.get("is_demo"):
        confidence = "LOW"

    return {
        "score": round(score, 1),
        "sub_scores": {
            "ma_position":  round(ma_pos_pts, 1),
            "ma_alignment": round(ma_ali_pts, 1),
            "new_high":     round(new_hi_pts, 1),
            "breakout":     round(bkout_pts,  1),
        },
        "reasons":  all_reasons[:6],
        "signals": {
            "above_ma20":    mas[20] is not None and price > (mas[20] or 0),
            "above_ma50":    mas[50] is not None and price > (mas[50] or 0),
            "above_ma200":   mas[200] is not None and price > (mas[200] or 0),
            "new_high_20d":  _new_high(closes, 20),
            "new_high_60d":  _new_high(closes, 60),
            "new_high_120d": _new_high(closes, 120),
            "bull_alignment": ma_ali_pts >= 16,
        },
        "confidence": confidence,
        "detail": {
            "price": round(price, 4),
            "ma5":   round(mas[5],   4) if mas[5]   else None,
            "ma10":  round(mas[10],  4) if mas[10]  else None,
            "ma20":  round(mas[20],  4) if mas[20]  else None,
            "ma50":  round(mas[50],  4) if mas[50]  else None,
            "ma200": round(mas[200], 4) if mas[200] else None,
        },
    }
