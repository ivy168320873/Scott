"""
Sector Leadership Score (0-100).
Aggregates stock-level momentum metrics to assess how a sector is
performing relative to itself (breadth, new highs, volume expansion).
Levels: LEADING / IMPROVING / NEUTRAL / WEAKENING / LAGGING
"""
from __future__ import annotations

from ohlcv_utils import sanitize_ohlcv


def _sma(lst: list, n: int) -> float:
    tail = [v for v in lst[-n:] if v and v > 0]
    return sum(tail) / len(tail) if tail else 0.0


def calc_sector_leadership(
    sector_name: str,
    stocks_ohlcv: dict,
) -> dict:
    """
    sector_name:  display name, e.g. "Technology" or "台股半導體IC"
    stocks_ohlcv: {symbol: normalized_ohlcv_dict, ...}
                  normalized_ohlcv_dict must have closes/highs/lows/volumes lists.
    """
    if not stocks_ohlcv:
        return _empty_result(sector_name, "無股票資料")

    gains_20d:          list[float] = []
    new_high_count      = 0
    vol_expand_count    = 0
    above_ma20_count    = 0
    total               = 0
    vol_sample          = 0      # 有成交量資料的成分股數（量能比例的分母）

    for sym, raw_ohlcv in stocks_ohlcv.items():
        ohlcv = sanitize_ohlcv(raw_ohlcv) or {}   # 缺值成分股不得混入板塊統計
        closes  = ohlcv.get("closes",  [])
        highs   = ohlcv.get("highs",   [])
        volumes = ohlcv.get("volumes", [])
        n = min(len(closes), len(highs), len(volumes)) if closes else 0
        if n < 20:
            continue
        total += 1

        # 20-day gain
        ref = closes[-21] if n >= 21 and closes[-21] else closes[-n]
        gain20 = (closes[-1] - ref) / ref * 100 if ref else 0.0
        gains_20d.append(gain20)

        # Created 20-day high?
        if closes[-1] >= max(highs[-20:]):
            new_high_count += 1

        # Volume expansion today vs 20d avg（無量資料者不列入分母）
        if ohlcv.get("has_volume", True):
            vol_sample += 1
            avg_vol = _sma(volumes, 20)
            if avg_vol > 0 and volumes[-1] > avg_vol * 1.2:
                vol_expand_count += 1

        # Above MA20?
        ma20 = _sma(closes, 20)
        if ma20 > 0 and closes[-1] > ma20:
            above_ma20_count += 1

    if total == 0:
        return _empty_result(sector_name, "無有效 K 線資料（各股資料不足 20 天）")

    avg_gain          = sum(gains_20d) / len(gains_20d) if gains_20d else 0.0
    new_high_ratio    = new_high_count   / total * 100
    # 分母改用「有量資料的檔數」；樣本不足時為 None，代表未知而非 0%。
    # 門檻取「過半且至少 1 檔」——避免 4 檔中只有 1 檔有量卻宣稱
    # 「100% 個股量能放大（資金湧入）」，同時不讓單檔板塊永遠算不出比例。
    _MIN_VOL_SAMPLE = max(1, (total + 1) // 2)
    vol_expand_ratio = (
        vol_expand_count / vol_sample * 100 if vol_sample >= _MIN_VOL_SAMPLE else None
    )
    above_ma20_ratio  = above_ma20_count / total * 100

    score = 50   # neutral baseline
    reasons:      list[str] = []
    warning_flags: list[str] = []

    # ── 1. Average 20-day gain (±25 pts) ─────────────────────────────────────
    if   avg_gain >  15: score += 25; reasons.append(f"板塊 20 日均漲幅 +{avg_gain:.1f}%（強勢領漲）")
    elif avg_gain >   8: score += 15; reasons.append(f"板塊 20 日均漲幅 +{avg_gain:.1f}%")
    elif avg_gain >   3: score +=  8; reasons.append(f"板塊 20 日均漲幅 +{avg_gain:.1f}%（小幅上漲）")
    elif avg_gain >  -3: score +=  0; reasons.append(f"板塊 20 日均漲幅 {avg_gain:+.1f}%（盤整）")
    elif avg_gain >  -8: score -= 10; reasons.append(f"板塊 20 日均跌幅 {avg_gain:.1f}%"); warning_flags.append("板塊下跌")
    else:                score -= 25; reasons.append(f"板塊 20 日均跌幅 {avg_gain:.1f}%（明顯走弱）"); warning_flags.append("板塊明顯走弱")

    # ── 2. New 20-day high ratio (±15 pts) ───────────────────────────────────
    if   new_high_ratio > 50: score += 15; reasons.append(f"{new_high_ratio:.0f}% 個股創 20 日高（強勢擴散）")
    elif new_high_ratio > 30: score +=  8; reasons.append(f"{new_high_ratio:.0f}% 個股創 20 日高")
    elif new_high_ratio > 15: score +=  3
    elif new_high_ratio <  5: score -= 10; reasons.append(f"僅 {new_high_ratio:.0f}% 個股創新高"); warning_flags.append("創新高比例低")

    # ── 3. Volume expansion ratio (±10 pts) ──────────────────────────────────
    # 無成交量資料時整段跳過——不得把「沒有量資料」講成「板塊量能萎縮」。
    if vol_expand_ratio is None:
        reasons.append(f"僅 {vol_sample}/{total} 檔有成交量資料，量能項未納入評分")
    elif vol_expand_ratio > 60: score += 10; reasons.append(f"{vol_expand_ratio:.0f}% 個股量能放大（{vol_expand_count}/{vol_sample} 檔，資金湧入）")
    elif vol_expand_ratio > 40: score +=  5; reasons.append(f"{vol_expand_ratio:.0f}% 個股量能放大（{vol_expand_count}/{vol_sample} 檔）")
    elif vol_expand_ratio < 15: score -=  8; reasons.append(f"板塊整體量能萎縮（{vol_expand_ratio:.0f}%，{vol_expand_count}/{vol_sample} 檔）"); warning_flags.append("量能萎縮")

    # ── 4. Above MA20 ratio (±10 pts) ────────────────────────────────────────
    if   above_ma20_ratio > 80: score += 10; reasons.append(f"{above_ma20_ratio:.0f}% 個股站上 MA20（廣泛強勢）")
    elif above_ma20_ratio > 60: score +=  5
    elif above_ma20_ratio < 30: score -= 10; reasons.append(f"僅 {above_ma20_ratio:.0f}% 個股站上 MA20"); warning_flags.append("多數跌破MA20")
    elif above_ma20_ratio < 50: score -=  5

    score = min(100, max(0, score))

    if   score >= 80: level = "LEADING";   color = "#3fb950"; level_label = "板塊領先"
    elif score >= 65: level = "IMPROVING"; color = "#58a6ff"; level_label = "板塊改善"
    elif score >= 45: level = "NEUTRAL";   color = "#e3b341"; level_label = "中性盤整"
    elif score >= 30: level = "WEAKENING"; color = "#f0883e"; level_label = "板塊轉弱"
    else:             level = "LAGGING";   color = "#f85149"; level_label = "板塊落後"

    _ACTIONS = {
        "LEADING":   f"板塊強勢，優先在 {sector_name} 中找強勢個股",
        "IMPROVING": f"板塊改善中，可逢低佈局 {sector_name} 強勢股",
        "NEUTRAL":   f"板塊中性，選股需更嚴格，優先看個股強度",
        "WEAKENING": f"板塊轉弱，降低 {sector_name} 部位，觀察是否止穩",
        "LAGGING":   f"板塊明顯落後，建議減少 {sector_name} 持倉，轉向強勢板塊",
    }

    return {
        "ok":             True,
        "engine":         "sector_leadership",
        "score":          score,
        "level":          level,
        "level_label":    level_label,
        "level_color":    color,
        "reasons":        reasons,
        "suggested_action": _ACTIONS.get(level, ""),
        "warning_flags":  warning_flags,
        # sector_leadership-specific ──────────────────────────────────────────
        "sector":         sector_name,
        "stock_count":    total,
        "leadership_level": level,   # alias for frontend clarity
        "detail": {
            "avg_20d_gain_pct":      round(avg_gain, 1),
            "new_high_ratio_pct":    round(new_high_ratio, 1),
            "vol_expand_ratio_pct":  round(vol_expand_ratio, 1) if vol_expand_ratio is not None else None,
            "above_ma20_ratio_pct":  round(above_ma20_ratio, 1),
        },
    }


def _empty_result(sector: str, msg: str) -> dict:
    return {
        "ok":             False,
        "engine":         "sector_leadership",
        "sector":         sector,
        "score":          None,
        "level":          "NEUTRAL",
        "level_label":    "資料不足",
        "level_color":    "#8b949e",
        "leadership_level": "NEUTRAL",
        "stock_count":    0,
        "reasons":        [msg],
        "suggested_action": "請確認板塊名稱或稍後再試",
        "warning_flags":  [],
        "detail":         {},
    }
