"""
量能分數模組 (Volume Score) — 0~100 分
---------------------------------------
評估成交量健康度，包含：
  · 量能強度 vs 5d/20d 均量
  · 有效放量突破
  · 出貨訊號偵測（爆量長上影、價跌量增）
  · 量能趨勢（持續放大 vs 持續縮小）
  · 區分健康買盤 vs 主力出貨

重要：volume_score 高 = 量能健康，低 = 量能警示。
"""
from __future__ import annotations

import statistics


def _sma(lst: list[float], n: int) -> float | None:
    valid = [v for v in lst[-n:] if v is not None and v >= 0]
    if len(valid) < max(1, n // 2):
        return None
    return sum(valid) / len(valid)


def _clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))


def _vol_ratio(volumes: list[float], ref_period: int) -> float | None:
    """當日成交量 / 前 N 日均量。"""
    avg = _sma(volumes[:-1], ref_period)
    if avg and avg > 0 and volumes[-1] >= 0:
        return volumes[-1] / avg
    return None


def _score_volume_strength(volumes: list[float]) -> tuple[float, list[str]]:
    """
    量能強度得分 (0-30 pts)
    · vs 5日均量 (0-15)
    · vs 20日均量 (0-15)
    """
    pts = 0.0
    reasons: list[str] = []

    r5 = _vol_ratio(volumes, 5)
    if r5 is not None:
        if r5 >= 2.5:
            pts += 15; reasons.append(f"成交量爆增 {r5:.1f}× 5日均量，主力積極介入")
        elif r5 >= 1.8:
            pts += 12; reasons.append(f"成交量明顯放大 {r5:.1f}× 5日均量")
        elif r5 >= 1.3:
            pts += 8;  reasons.append(f"成交量放大 {r5:.1f}× 5日均量")
        elif r5 >= 0.8:
            pts += 4;  reasons.append(f"成交量正常 {r5:.1f}× 5日均量")
        else:
            reasons.append(f"成交量萎縮 {r5:.1f}× 5日均量，籌碼沉澱或觀望")

    r20 = _vol_ratio(volumes, 20)
    if r20 is not None:
        if r20 >= 2.0:
            pts += 15; reasons.append(f"大幅超越 20日均量 {r20:.1f}×，長線主力進場")
        elif r20 >= 1.5:
            pts += 10; reasons.append(f"超越 20日均量 {r20:.1f}×")
        elif r20 >= 1.0:
            pts += 5
        else:
            reasons.append(f"低於 20日均量 {r20:.1f}×，量能不足")

    return _clamp(pts, 0, 30), reasons


def _score_effective_breakout(
    closes: list[float], opens: list[float],
    highs: list[float], lows: list[float],
    volumes: list[float],
) -> tuple[float, list[str]]:
    """
    有效放量突破得分 (0-25 pts)
    條件：價格創近高 + 量大於均量 + 收盤偏強
    """
    pts = 0.0
    reasons: list[str] = []
    n = len(closes)
    if n < 21:
        return 0.0, []

    price   = closes[-1]
    avg20v  = _sma(volumes[:-1], 20) or 1
    cur_vol = volumes[-1]
    r20     = cur_vol / avg20v

    # 近 20 日最高收盤
    recent_high = max(closes[-21:-1])

    # 收盤位置（當日區間內）
    intra_range = highs[-1] - lows[-1]
    close_pos   = (closes[-1] - lows[-1]) / intra_range if intra_range > 0 else 0.5

    breakout = price > recent_high * 1.002

    if breakout and r20 >= 1.5 and close_pos >= 0.6:
        pts += 25
        reasons.append(f"有效放量突破（量 {r20:.1f}× 均量，收盤偏強 {close_pos * 100:.0f}%）")
    elif breakout and r20 >= 1.2:
        pts += 15
        reasons.append(f"放量突破但收盤位置偏弱 {close_pos * 100:.0f}%，需觀察隔日")
    elif breakout and r20 < 1.0:
        pts += 5
        reasons.append(f"突破新高但量能不足（{r20:.1f}× 均量），假突破風險高")
    elif not breakout and r20 >= 2.0:
        pts += 8
        reasons.append(f"未突破高點但量能大增（{r20:.1f}×），可能蓄積能量")

    return _clamp(pts, 0, 25), reasons


def _score_distribution_penalty(
    closes: list[float], opens: list[float],
    highs: list[float], lows: list[float],
    volumes: list[float],
) -> tuple[float, list[str]]:
    """
    出貨訊號懲罰 (-0 ~ -25 pts，回傳正數作為扣分)
    識別以下危險形態：
    1. 爆量長上影（主力出貨經典形態）
    2. 價跌量增（down-day high volume）
    3. 連續出貨日計數
    """
    penalty = 0.0
    reasons: list[str] = []
    n = len(closes)
    if n < 5:
        return 0.0, []

    avg20v = _sma(volumes[:-1], 20) or 1

    # ① 爆量長上影
    today_body   = abs(closes[-1] - opens[-1])
    upper_shadow = highs[-1] - max(closes[-1], opens[-1])
    if (volumes[-1] > avg20v * 2.0
            and today_body > 0
            and upper_shadow > today_body * 1.5):
        penalty += 20
        reasons.append(f"爆量長上影（量 {volumes[-1]/avg20v:.1f}× 均量），高度懷疑主力出貨")

    # ② 今日: 收盤 < 開盤（跌日）且量 > 均量 1.3×
    if closes[-1] < opens[-1] and volumes[-1] > avg20v * 1.3:
        penalty += 10
        reasons.append(f"跌日高量（{volumes[-1]/avg20v:.1f}× 均量），下跌動能強")

    # ③ 近 5 日出貨日計數（跌日且量大於均量）
    dist_count = 0
    for i in range(-5, -1):
        if (closes[i] < opens[i]
                and len(volumes) >= abs(i)
                and volumes[i] > avg20v * 1.2):
            dist_count += 1
    if dist_count >= 3:
        penalty += 15
        reasons.append(f"近 5 日出現 {dist_count} 個出貨日，空頭籌碼持續賣壓")
    elif dist_count == 2:
        penalty += 7
        reasons.append(f"近 5 日出現 {dist_count} 個出貨日，注意賣壓")

    return _clamp(penalty, 0, 25), reasons


def _score_volume_trend(volumes: list[float]) -> tuple[float, list[str]]:
    """
    量能趨勢得分 (0-20 pts)
    比較近 5 日均量 vs 前 10 日均量的趨勢。
    """
    pts = 0.0
    reasons: list[str] = []
    if len(volumes) < 15:
        return 10.0, []   # 中性

    avg_recent = _sma(volumes[-5:],  5) or 0
    avg_older  = _sma(volumes[-15:-5], 10) or 0

    if avg_older <= 0:
        return 10.0, []

    trend_ratio = avg_recent / avg_older

    if trend_ratio >= 1.5:
        pts = 20; reasons.append(f"量能持續放大（近 5 日均量 {trend_ratio:.1f}× 前期），主力動作持續")
    elif trend_ratio >= 1.2:
        pts = 14; reasons.append(f"量能溫和擴張（{trend_ratio:.1f}×），籌碼逐漸活躍")
    elif trend_ratio >= 0.8:
        pts = 10; reasons.append(f"量能穩定（{trend_ratio:.1f}×）")
    elif trend_ratio >= 0.5:
        pts = 4;  reasons.append(f"量能萎縮（{trend_ratio:.1f}×），籌碼趨於觀望")
    else:
        pts = 0;  reasons.append(f"量能嚴重萎縮（{trend_ratio:.1f}×），市場失去動力")

    return _clamp(pts, 0, 20), reasons


# ── 主函數 ────────────────────────────────────────────────────────────────────

def compute(ohlcv: dict) -> dict:
    """
    計算量能分數。

    Parameters
    ----------
    ohlcv : dict  標準化 OHLCV

    Returns
    -------
    dict
        score          : float  0-100
        sub_scores     : dict
        reasons        : list
        signals        : dict
        confidence     : str
        distribution   : bool   是否偵測到出貨訊號
    """
    closes  = [v for v in (ohlcv.get("closes")  or []) if v and v > 0]
    opens   = [v for v in (ohlcv.get("opens")   or []) if v and v > 0]
    highs   = [v for v in (ohlcv.get("highs")   or []) if v and v > 0]
    lows    = [v for v in (ohlcv.get("lows")    or []) if v and v > 0]
    volumes = [v for v in (ohlcv.get("volumes") or []) if v is not None and v >= 0]

    n = min(len(closes), len(opens), len(highs), len(lows), len(volumes))
    if n < 10:
        return {
            "score": 40, "sub_scores": {}, "reasons": ["量能資料不足（需 ≥10 根K線）"],
            "signals": {}, "confidence": "LOW", "distribution": False,
        }

    closes  = closes[-n:];  opens  = opens[-n:]
    highs   = highs[-n:];   lows   = lows[-n:]
    volumes = volumes[-n:]

    # 異常值過濾：成交量為 0 的日子
    zero_vol_days = sum(1 for v in volumes[-20:] if v == 0)
    if zero_vol_days > 5:
        return {
            "score": 30, "sub_scores": {}, "reasons": [f"近 20 日有 {zero_vol_days} 日成交量為 0，資料異常"],
            "signals": {}, "confidence": "LOW", "distribution": False,
        }

    all_reasons: list[str] = []

    # 子分數 1: 量能強度 (0-30)
    strength_pts, strength_r = _score_volume_strength(volumes)
    all_reasons.extend(strength_r[:2])

    # 子分數 2: 有效突破 (0-25)
    breakout_pts, breakout_r = _score_effective_breakout(closes, opens, highs, lows, volumes)
    all_reasons.extend(breakout_r[:1])

    # 子分數 3: 出貨懲罰 (扣 0-25)
    dist_penalty, dist_r = _score_distribution_penalty(closes, opens, highs, lows, volumes)
    all_reasons.extend(dist_r[:2])

    # 子分數 4: 量能趨勢 (0-20)
    trend_pts, trend_r = _score_volume_trend(volumes)
    all_reasons.extend(trend_r[:1])

    # 基礎分 = strength + breakout + trend，再扣去出貨懲罰
    base = strength_pts + breakout_pts + trend_pts  # max = 75
    # 歸一化到 0-100（75 以上 → 100）
    normalized = _clamp(base / 75 * 100 - dist_penalty, 0, 100)

    # 信心度
    confidence = "HIGH" if n >= 60 and zero_vol_days == 0 else \
                 "MEDIUM" if n >= 20 else "LOW"
    if ohlcv.get("is_demo"):
        confidence = "LOW"

    distribution_detected = dist_penalty >= 15

    r5  = _vol_ratio(volumes, 5)
    r20 = _vol_ratio(volumes, 20)

    # ── label / warning（新增欄位，向後相容） ─────────────────────────────────
    _s = round(normalized, 1)
    _label = ("強勢" if _s >= 80 else "偏強" if _s >= 65 else
              "中性" if _s >= 50 else "偏弱" if _s >= 35 else "弱勢")
    _warning: list[str] = []
    if distribution_detected:
        _warning.append("出貨訊號：偵測到爆量長上影或持續賣壓")
    if trend_pts <= 4:
        _warning.append("量能嚴重萎縮，市場失去動力")

    return {
        "score":      _s,
        "label":      _label,
        "warning":    _warning,
        "sub_scores": {
            "strength":             round(strength_pts, 1),
            "effective_breakout":   round(breakout_pts, 1),
            "distribution_penalty": round(dist_penalty, 1),
            "volume_trend":         round(trend_pts, 1),
        },
        "reason":  all_reasons[:6],   # alias for new interface
        "reasons": all_reasons[:6],   # keep legacy key
        "signals": {
            "high_volume_5d":       r5  is not None and r5  >= 1.5,
            "high_volume_20d":      r20 is not None and r20 >= 1.5,
            "distribution_warning": distribution_detected,
            "vol_expanding":        trend_pts >= 14,
            "vol_contracting":      trend_pts <= 4,
        },
        "confidence":  confidence,
        "distribution": distribution_detected,
        "detail": {
            "current_volume": volumes[-1],
            "avg_volume_5d":  round(_sma(volumes[:-1], 5) or 0, 0),
            "avg_volume_20d": round(_sma(volumes[:-1], 20) or 0, 0),
            "vol_ratio_5d":   round(r5, 2)  if r5  is not None else None,
            "vol_ratio_20d":  round(r20, 2) if r20 is not None else None,
        },
    }
