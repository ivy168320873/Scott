"""
Capital Efficiency Filter Engine — Phase 12
Scans a watchlist of symbols and ranks them by capital deployment priority.

Outputs per symbol:
  - capital_efficiency_score  0–100
  - tier  A / B / C
  - entry_price, stop_price, target1, target2
  - suitability: short_term, overnight, swing
  - top3_picks with reasons
"""
from __future__ import annotations

import math
from typing import Callable


# ── Helpers ────────────────────────────────────────────────────────────────────

def _sma(lst: list, n: int) -> float:
    tail = [v for v in lst[-n:] if v and v > 0]
    return sum(tail) / len(tail) if tail else 0.0


def _atr(highs: list, lows: list, closes: list, n: int = 14) -> float:
    trs = []
    for i in range(1, min(n + 1, len(closes))):
        h, l, pc = highs[-i], lows[-i], closes[-(i + 1)]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(trs) / len(trs) if trs else closes[-1] * 0.02


def _vol_avg(volumes: list, n: int) -> float:
    tail = [v for v in volumes[-n:] if v and v > 0]
    return sum(tail) / len(tail) if tail else 0.0


def _pct(a: float, b: float) -> float:
    return (a - b) / b * 100 if b else 0.0


# ── Per-symbol scoring ─────────────────────────────────────────────────────────

def _score_symbol(symbol: str, ohlcv: dict) -> dict | None:
    closes  = ohlcv.get("closes") or []
    highs   = ohlcv.get("highs")  or closes
    lows    = ohlcv.get("lows")   or closes
    volumes = ohlcv.get("volumes") or []

    if len(closes) < 20:
        return None

    price = closes[-1]
    if price <= 0:
        return None

    score = 0
    breakdown: dict[str, dict] = {}

    # ── 1. 近 5 日漲幅 (0–18 pts) ─────────────────────────────────────────────
    gain5 = _pct(price, closes[-6]) if len(closes) >= 6 else 0.0
    if   gain5 >= 8:  g5_pts = 18
    elif gain5 >= 5:  g5_pts = 14
    elif gain5 >= 2:  g5_pts = 10
    elif gain5 >= 0:  g5_pts = 6
    elif gain5 >= -3: g5_pts = 2
    else:             g5_pts = 0
    score += g5_pts
    breakdown["gain5d"] = {"score": g5_pts, "max": 18, "value": round(gain5, 2), "label": "近5日漲幅"}

    # ── 2. 近 5 日成交量變化 vs 20 日均量 (0–20 pts) ──────────────────────────
    vol5  = _vol_avg(volumes, 5)
    vol20 = _vol_avg(volumes, 20)
    vol_ratio = vol5 / vol20 if vol20 else 1.0
    if   vol_ratio >= 2.5: vr_pts = 20
    elif vol_ratio >= 1.8: vr_pts = 16
    elif vol_ratio >= 1.3: vr_pts = 11
    elif vol_ratio >= 0.9: vr_pts = 6
    else:                  vr_pts = 2
    score += vr_pts
    breakdown["vol_change"] = {"score": vr_pts, "max": 20, "value": round(vol_ratio, 2), "label": "量能比(5日/20日)"}

    # ── 3. 距離突破價百分比 (0–20 pts) ────────────────────────────────────────
    h20 = max(highs[-20:]) if len(highs) >= 20 else price
    h60 = max(highs[-60:]) if len(highs) >= 60 else h20
    breakout_ref = h20  # primary: 20d high
    dist_pct = _pct(breakout_ref, price)   # negative = already above
    if   dist_pct <= 0:    bp_pts = 20   # already broken out
    elif dist_pct <= 1.5:  bp_pts = 17   # within 1.5% of breakout
    elif dist_pct <= 3:    bp_pts = 13
    elif dist_pct <= 5:    bp_pts = 8
    elif dist_pct <= 8:    bp_pts = 4
    else:                  bp_pts = 0
    score += bp_pts
    breakdown["breakout_dist"] = {
        "score": bp_pts, "max": 20,
        "value": round(dist_pct, 2),
        "breakout_price": round(breakout_ref, 4),
        "label": "距突破價距離%",
    }

    # ── 4. 均線多頭排列 / 板塊熱度代理 (0–22 pts) ─────────────────────────────
    ma5  = _sma(closes, 5)
    ma10 = _sma(closes, 10)
    ma20 = _sma(closes, 20)
    ma60 = _sma(closes, 60) if len(closes) >= 60 else 0
    stack_pts = 0
    stack_note = []
    if ma5  > ma10:  stack_pts += 6;  stack_note.append("MA5>MA10")
    if ma10 > ma20:  stack_pts += 7;  stack_note.append("MA10>MA20")
    if ma20 > ma60 and ma60:  stack_pts += 9;  stack_note.append("MA20>MA60")
    score += stack_pts
    breakdown["ma_stack"] = {
        "score": stack_pts, "max": 22,
        "detail": " ".join(stack_note) or "均線無多頭排列",
        "label": "均線多頭排列",
    }

    # ── 5. 停損距離 (ATR-based) — 越近停損越扣分，越遠越好 (0–12 pts) ─────────
    atr_val = _atr(highs, lows, closes)
    atr_pct = atr_val / price * 100
    stop_dist_pct = atr_pct * 1.5   # 1.5 ATR stop
    if   stop_dist_pct <= 3:   sl_pts = 12   # tight stop = low risk
    elif stop_dist_pct <= 5:   sl_pts = 8
    elif stop_dist_pct <= 8:   sl_pts = 4
    else:                      sl_pts = 0
    score += sl_pts
    breakdown["stop_dist"] = {
        "score": sl_pts, "max": 12,
        "value": round(stop_dist_pct, 2),
        "atr_pct": round(atr_pct, 2),
        "label": "停損距離%",
    }

    # ── 6. 預估上漲空間 (risk/reward ratio) (0–8 pts) ─────────────────────────
    resistance = h60 if h60 > price else price * 1.12
    upside_pct = _pct(resistance, price)
    rr_ratio = upside_pct / stop_dist_pct if stop_dist_pct > 0 else 0
    if   rr_ratio >= 3:  rr_pts = 8
    elif rr_ratio >= 2:  rr_pts = 6
    elif rr_ratio >= 1.5: rr_pts = 4
    elif rr_ratio >= 1:   rr_pts = 2
    else:                 rr_pts = 0
    score += rr_pts
    breakdown["rr_ratio"] = {
        "score": rr_pts, "max": 8,
        "value": round(rr_ratio, 2),
        "upside_pct": round(upside_pct, 2),
        "resistance": round(resistance, 4),
        "label": "風報比",
    }

    score = max(0, min(100, score))

    # ── Tier A / B / C ─────────────────────────────────────────────────────────
    if score >= 70:
        tier = "A"
        tier_label = "A級：最值得優先投入資金"
        tier_color = "#3fb950"
        tier_bg    = "#0d2b14"
    elif score >= 50:
        tier = "B"
        tier_label = "B級：可觀察，等突破確認"
        tier_color = "#e3b341"
        tier_bg    = "#2b2200"
    else:
        tier = "C"
        tier_label = "C級：暫不投入，避免卡資金"
        tier_color = "#8b949e"
        tier_bg    = "#1a1a1a"

    # ── Entry / Stop / Targets ─────────────────────────────────────────────────
    atr = atr_val

    # Entry: if already above 20d high → current price; else just below h20
    if price >= breakout_ref:
        entry_price = round(price, 4)
    else:
        entry_price = round(price * 1.002, 4)   # slight buy-stop above current

    # Stop: 1.5 ATR below entry, minimum -3%, maximum -10%
    raw_stop = entry_price - 1.5 * atr
    stop_pct_actual = (entry_price - raw_stop) / entry_price * 100
    stop_pct_actual = min(max(stop_pct_actual, 3.0), 10.0)
    stop_price = round(entry_price * (1 - stop_pct_actual / 100), 4)

    # Target 1: breakout level or +5-7%
    t1_from_rr = entry_price * (1 + stop_pct_actual * 1.5 / 100)
    t1_from_res = breakout_ref * 1.03 if breakout_ref > price else price * 1.06
    target1 = round(max(t1_from_rr, min(t1_from_res, price * 1.08)), 4)

    # Target 2: 2x risk/reward or h60
    t2_from_rr = entry_price * (1 + stop_pct_actual * 2.5 / 100)
    t2_from_h60 = h60 * 1.02 if h60 > price else price * 1.15
    target2 = round(max(t2_from_rr, min(t2_from_h60, price * 1.20)), 4)

    target1_pct = round(_pct(target1, entry_price), 2)
    target2_pct = round(_pct(target2, entry_price), 2)

    # ── Trading style suitability ─────────────────────────────────────────────
    # Short-term (2-5 days): needs strong volume + price momentum near breakout
    short_term = vol_ratio >= 1.5 and gain5 >= 1.5 and dist_pct <= 3

    # Overnight (隔日沖): high single-day volume spike + strong close
    today_vol = volumes[-1] if volumes else 0
    yday_vol_avg = _vol_avg(volumes[:-1], 5) if len(volumes) > 1 else 0
    day_vol_spike = today_vol / yday_vol_avg if yday_vol_avg else 1.0
    today_gain = _pct(price, closes[-2]) if len(closes) >= 2 else 0.0
    overnight = day_vol_spike >= 2.0 and today_gain >= 1.0 and dist_pct <= 2

    # Swing (2-6 weeks): clean MA stack + position in lower 60% of 52w range
    n252 = min(252, len(highs))
    hi52 = max(highs[-n252:])
    lo52 = min(lows[-n252:])
    rng52 = hi52 - lo52
    pos52 = (price - lo52) / rng52 * 100 if rng52 else 50
    swing = stack_pts >= 16 and pos52 <= 65

    return {
        "ok":      True,
        "symbol":  symbol.upper(),
        "price":   round(price, 4),
        "score":   score,
        "tier":    tier,
        "tier_label": tier_label,
        "tier_color": tier_color,
        "tier_bg":    tier_bg,
        # Sorting fields
        "gain5d_pct":         round(gain5, 2),
        "vol_ratio":          round(vol_ratio, 2),
        "breakout_dist_pct":  round(dist_pct, 2),
        "stop_dist_pct":      round(stop_pct_actual, 2),
        "upside_pct":         round(upside_pct, 2),
        "rr_ratio":           round(rr_ratio, 2),
        # Trade plan
        "entry_price":  entry_price,
        "stop_price":   stop_price,
        "target1":      target1,
        "target2":      target2,
        "target1_pct":  target1_pct,
        "target2_pct":  target2_pct,
        # Style suitability
        "suitability": {
            "short_term": short_term,
            "overnight":  overnight,
            "swing":      swing,
        },
        # Detail
        "breakdown":    breakdown,
        "is_demo":      bool(ohlcv.get("is_demo", False)),
        "source":       ohlcv.get("source", "unknown"),
        "meta": {
            "ma5":     round(ma5,  4),
            "ma20":    round(ma20, 4),
            "ma60":    round(ma60, 4) if ma60 else None,
            "h20":     round(h20,  4),
            "h60":     round(h60,  4),
            "atr_pct": round(atr_val / price * 100, 2),
            "pos52w":  round(pos52, 1),
        },
    }


# ── Top-3 reasoning ───────────────────────────────────────────────────────────

def _top3_reasons(item: dict) -> list[str]:
    reasons = []
    if item["gain5d_pct"] >= 3:
        reasons.append(f"近5日漲幅 +{item['gain5d_pct']}%，強勢動能持續中")
    if item["vol_ratio"] >= 1.8:
        reasons.append(f"成交量放大 {item['vol_ratio']}x，機構資金介入跡象")
    if item["breakout_dist_pct"] <= 0:
        reasons.append(f"已突破20日高點 {item['meta']['h20']}，突破訊號確認")
    elif item["breakout_dist_pct"] <= 2:
        reasons.append(f"距突破價僅 {item['breakout_dist_pct']}%，即將挑戰前高")
    bd = item["breakdown"].get("ma_stack", {})
    if "MA20>MA60" in bd.get("detail", ""):
        reasons.append("均線完整多頭排列，趨勢健康")
    if item["rr_ratio"] >= 2.5:
        reasons.append(f"風報比 {item['rr_ratio']}x，停損小 / 目標空間大")
    suitability = item["suitability"]
    styles = []
    if suitability["short_term"]: styles.append("短線")
    if suitability["overnight"]:  styles.append("隔日沖")
    if suitability["swing"]:      styles.append("波段")
    if styles:
        reasons.append(f"適合：{'、'.join(styles)} 操作")
    return reasons[:4]


# ── Public API ─────────────────────────────────────────────────────────────────

def run_capital_filter(
    symbols: list[str],
    ohlcv_fn: Callable,
    sort_by: str = "score",
) -> dict:
    """
    symbols  : list of ticker strings
    ohlcv_fn : callable(symbol) -> ohlcv_dict | None
    sort_by  : 'score' | 'gain5d' | 'vol_change' | 'breakout_dist' | 'rr_ratio'
    """
    results: list[dict] = []
    errors:  list[dict] = []

    for sym in symbols[:30]:
        sym = sym.upper().strip()
        if not sym:
            continue
        try:
            ohlcv = ohlcv_fn(sym)
            if not ohlcv:
                errors.append({"symbol": sym, "error": "無法取得K線資料"})
                continue
            item = _score_symbol(sym, ohlcv)
            if item is None:
                errors.append({"symbol": sym, "error": "K線資料不足20日"})
                continue
            results.append(item)
        except Exception as e:
            errors.append({"symbol": sym, "error": str(e)})

    # Sort
    _SORT_KEYS = {
        "score":        lambda x: -x["score"],
        "gain5d":       lambda x: -x["gain5d_pct"],
        "vol_change":   lambda x: -x["vol_ratio"],
        "breakout_dist": lambda x: x["breakout_dist_pct"],   # ascending: closest first
        "rr_ratio":     lambda x: -x["rr_ratio"],
        "stop_dist":    lambda x: x["stop_dist_pct"],        # ascending: tightest first
        "upside":       lambda x: -x["upside_pct"],
    }
    key_fn = _SORT_KEYS.get(sort_by, _SORT_KEYS["score"])
    results.sort(key=key_fn)

    # Top-3 picks (A-tier or best B-tier, min score 45)
    top_pool = [r for r in results if r["score"] >= 45][:6]
    top3: list[dict] = []
    for rank, item in enumerate(top_pool[:3], start=1):
        top3.append({
            "rank":    rank,
            "symbol":  item["symbol"],
            "score":   item["score"],
            "tier":    item["tier"],
            "price":   item["price"],
            "entry":   item["entry_price"],
            "stop":    item["stop_price"],
            "target1": item["target1"],
            "target2": item["target2"],
            "reasons": _top3_reasons(item),
            "suitability": item["suitability"],
        })

    # Tier counts
    tier_counts = {"A": 0, "B": 0, "C": 0}
    for r in results:
        tier_counts[r["tier"]] = tier_counts.get(r["tier"], 0) + 1

    return {
        "ok":          True,
        "total":       len(results),
        "errors":      errors,
        "tier_counts": tier_counts,
        "results":     results,
        "top3":        top3,
        "sort_by":     sort_by,
        "disclaimer":  "此為資金效率決策輔助，不代表自動下單。",
    }
