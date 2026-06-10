from __future__ import annotations

import math
import statistics
from typing import Any


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_div(a: float, b: float, default: float = 0.0) -> float:
    return a / b if b != 0 else default


def _clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> int:
    return int(max(lo, min(hi, round(value))))


def _obv_series(closes: list[float], volumes: list[float]) -> list[float]:
    obv: list[float] = [0.0]
    for i in range(1, len(closes)):
        if closes[i] > closes[i - 1]:
            obv.append(obv[-1] + volumes[i])
        elif closes[i] < closes[i - 1]:
            obv.append(obv[-1] - volumes[i])
        else:
            obv.append(obv[-1])
    return obv


def _obv_momentum(closes: list[float], volumes: list[float], period: int = 60) -> float:
    n = len(closes)
    if n < period:
        period = n
    c = closes[-period:]
    v = volumes[-period:]
    obv = _obv_series(c, v)
    half = len(obv) // 2
    if half == 0:
        return 0.0
    first_avg = statistics.mean(obv[:half])
    second_avg = statistics.mean(obv[half:])
    spread = max(abs(first_avg), abs(second_avg), 1.0)
    raw = _safe_div(second_avg - first_avg, spread)
    return max(-1.0, min(1.0, raw))


def _rolling_realized_vol(closes: list[float], window: int = 5) -> list[float]:
    """Annualised realised vol for each position that has a full window."""
    results: list[float] = []
    for i in range(window, len(closes) + 1):
        chunk = closes[i - window: i]
        rets = [math.log(chunk[j] / chunk[j - 1]) for j in range(1, len(chunk)) if chunk[j - 1] > 0]
        if len(rets) < 2:
            results.append(0.0)
        else:
            results.append(statistics.stdev(rets) * math.sqrt(252))
    return results


# ---------------------------------------------------------------------------
# Sub-calculations
# ---------------------------------------------------------------------------

def _relative_strength(
    stock_closes: list[float],
    bench_closes: list[float],
    period: int = 20,
) -> float | None:
    if len(stock_closes) < period + 1 or len(bench_closes) < period + 1:
        return None
    stock_ret = stock_closes[-1] / stock_closes[-(period + 1)] - 1
    bench_ret = bench_closes[-1] / bench_closes[-(period + 1)] - 1
    return _safe_div(1 + stock_ret, 1 + bench_ret, default=None)


def _calc_volume_accumulation(
    closes: list[float],
    opens: list[float],
    volumes: list[float],
    period: int = 20,
    obv_period: int = 60,
) -> tuple[int, float, float]:
    """Returns (score, up_vol_ratio, obv_mom)."""
    n = len(closes)
    p = min(period, n)
    c = closes[-p:]
    o = opens[-p:]
    v = volumes[-p:]

    up_vol = sum(v[i] for i in range(p) if c[i] > o[i])
    down_vol = sum(v[i] for i in range(p) if c[i] < o[i])
    total_ud = up_vol + down_vol
    ratio = _safe_div(up_vol, total_ud, default=0.5)

    base = 50 + (ratio - 0.5) * 150
    obv_mom = _obv_momentum(closes, volumes, period=obv_period)
    score = _clamp(base + obv_mom * 15)
    return score, ratio, obv_mom


def _calc_price_volume_confirmation(
    closes: list[float],
    volumes: list[float],
    period: int = 20,
) -> int:
    n = len(closes)
    p = min(period, n)
    if p < 2:
        return 50

    c = closes[-p:]
    v = volumes[-p:]
    avg_vol = statistics.mean(v) if v else 1.0

    confirmed = 0.0
    diverged = 0.0
    for i in range(1, p):
        price_up = c[i] > c[i - 1]
        price_down = c[i] < c[i - 1]
        vol_up = v[i] >= avg_vol
        vol_low = v[i] < avg_vol

        if price_up and vol_up:
            confirmed += 1
        elif price_down and vol_up:
            diverged += 1
        elif price_up and vol_low:
            diverged += 0.5

    denom = confirmed + diverged
    if denom == 0:
        return 50
    return _clamp(_safe_div(confirmed, denom, 0.5) * 100)


def _calc_breakout_quality(
    closes: list[float],
    highs: list[float],
    lows: list[float],
    volumes: list[float],
) -> tuple[int, dict]:
    n = len(closes)
    detail: dict[str, Any] = {"has_breakout": False}

    lookback_start = max(0, n - 65)
    lookback_end = max(0, n - 5)
    if lookback_end <= lookback_start:
        return 50, detail

    resistance = max(highs[lookback_start:lookback_end])
    detail["resistance"] = resistance

    recent_closes = closes[max(0, n - 5):]
    recent_highs = highs[max(0, n - 5):]
    recent_lows = lows[max(0, n - 5):]
    recent_vols = volumes[max(0, n - 5):]

    bo_idx_local = None
    for i, rc in enumerate(recent_closes):
        if rc > resistance:
            bo_idx_local = i
            break

    if bo_idx_local is None:
        return 50, detail

    detail["has_breakout"] = True
    global_bo_idx = (n - 5) + bo_idx_local

    avg_vol_20 = statistics.mean(volumes[max(0, global_bo_idx - 20): global_bo_idx]) if global_bo_idx > 0 else 1.0
    if avg_vol_20 == 0:
        avg_vol_20 = 1.0

    bo_vol = volumes[global_bo_idx]
    bo_close = closes[global_bo_idx]
    bo_high = highs[global_bo_idx]
    bo_low = lows[global_bo_idx]

    vol_mult = _safe_div(bo_vol, avg_vol_20, default=1.0)
    hl_range = bo_high - bo_low
    close_pos = _safe_div(bo_close - bo_low, hl_range, default=0.5) if hl_range > 0 else 0.5

    detail["vol_mult"] = vol_mult
    detail["close_pos"] = close_pos

    score = 50.0
    if vol_mult > 2.0:
        score += 25
    elif vol_mult > 1.5:
        score += 15
    elif vol_mult > 1.2:
        score += 8
    elif vol_mult < 0.8:
        score -= 20

    if close_pos > 0.75:
        score += 15
    elif close_pos > 0.5:
        score += 7
    elif close_pos < 0.25:
        score -= 15

    holding = closes[-1] > resistance
    detail["holding"] = holding
    if holding:
        score += 10
    else:
        score -= 20

    return _clamp(score), detail


def _calc_distribution_risk(
    closes: list[float],
    highs: list[float],
    lows: list[float],
    volumes: list[float],
    period: int = 20,
) -> int:
    n = len(closes)
    p = min(period, n)
    if p < 2:
        return 0

    c = closes[-p:]
    h = highs[-p:]
    lo = lows[-p:]
    v = volumes[-p:]
    avg_vol = statistics.mean(v) if v else 1.0

    dist_days = 0
    for i in range(1, p):
        if c[i] < c[i - 1] and v[i] > avg_vol * 1.1:
            dist_days += 1

    score = _safe_div(dist_days, p) * 60

    shadow_sum = 0.0
    shadow_count = 0
    for i in range(p):
        if v[i] > avg_vol * 1.3:
            hl = h[i] - lo[i]
            if hl > 0:
                upper_shadow = _safe_div(h[i] - c[i], hl)
                shadow_sum += upper_shadow
                shadow_count += 1

    if shadow_count > 0:
        avg_upper = shadow_sum / shadow_count
        score += avg_upper * 40

    return _clamp(score)


def _calc_smart_money(
    closes: list[float],
    highs: list[float],
    lows: list[float],
    opens: list[float],
    volumes: list[float],
    period: int = 40,
) -> int:
    n = len(closes)
    p = min(period, n)
    if p == 0:
        return 0

    c = closes[-p:]
    h = highs[-p:]
    lo = lows[-p:]
    o = opens[-p:]
    v = volumes[-p:]
    avg_vol = statistics.mean(v) if v else 1.0

    smart_days = 0
    for i in range(p):
        hl = h[i] - lo[i]
        close_pos = _safe_div(c[i] - lo[i], hl, default=0.0) if hl > 0 else 0.0
        if v[i] > avg_vol * 1.5 and close_pos > 0.7 and c[i] >= o[i]:
            smart_days += 1

    base = _safe_div(smart_days, p) * 100

    if avg_vol > 0:
        try:
            cv = _safe_div(statistics.stdev(v), avg_vol, default=1.0)
        except statistics.StatisticsError:
            cv = 1.0
    else:
        cv = 1.0

    if cv < 0.3:
        base += 15
    elif cv < 0.5:
        base += 8

    return _clamp(base)


def _calc_liquidity_quality(
    closes: list[float],
    volumes: list[float],
    period: int = 20,
) -> int:
    n = len(closes)
    p = min(period, n)
    if p == 0:
        return 15

    c = closes[-p:]
    v = volumes[-p:]
    adv = statistics.mean([c[i] * v[i] for i in range(p)])

    if adv >= 1_000_000_000:
        base = 100
    elif adv >= 500_000_000:
        base = 90
    elif adv >= 100_000_000:
        base = 80
    elif adv >= 50_000_000:
        base = 70
    elif adv >= 10_000_000:
        base = 55
    elif adv >= 1_000_000:
        base = 40
    elif adv >= 500_000:
        base = 30
    else:
        base = 15

    avg_vol = statistics.mean(v) if v else 1.0
    try:
        cv = _safe_div(statistics.stdev(v), avg_vol, default=1.0)
    except statistics.StatisticsError:
        cv = 1.0

    # consistent volume flow is a mild quality signal
    adj = 5 if cv < 0.5 else -5
    return _clamp(base + adj)


def _calc_volatility_stability(closes: list[float], window: int = 5, period: int = 25) -> int:
    n = len(closes)
    p = min(period, n)
    if p < window + 1:
        return 55

    chunk = closes[-p:]
    vols = _rolling_realized_vol(chunk, window=window)
    if len(vols) < 2:
        return 55

    half = len(vols) // 2
    early_avg = statistics.mean(vols[:half]) if vols[:half] else 0.0
    late_avg = statistics.mean(vols[half:]) if vols[half:] else 0.0

    if early_avg == 0:
        return 55

    ratio = _safe_div(late_avg, early_avg, default=1.0)

    if ratio < 0.7:
        return 85
    elif ratio < 0.9:
        return 70
    elif ratio <= 1.1:
        return 55
    elif ratio <= 1.3:
        return 40
    else:
        return 25


def _build_reasons_warnings(
    up_vol_ratio: float,
    obv_mom: float,
    rs_qqq: float | None,
    rs_sector: float | None,
    breakout_score: int,
    dist_risk: int,
    smart_money: int,
    period: int,
) -> tuple[list[str], list[str]]:
    reasons: list[str] = []
    warnings: list[str] = []

    up_pct = round(up_vol_ratio * 100)
    down_pct = 100 - up_pct

    if up_vol_ratio > 0.65:
        reasons.append(f"近{period}日買盤量{up_pct}%，多方主導")
    elif up_vol_ratio < 0.35:
        warnings.append(f"近{period}日賣盤量{down_pct}%，空方主導")

    if obv_mom > 0.3:
        reasons.append("OBV趨勢向上，累積型態")
    elif obv_mom < -0.3:
        warnings.append("OBV趨勢向下，派發型態")

    if rs_qqq is not None:
        if rs_qqq > 1.1:
            reasons.append(f"相對QQQ強度{rs_qqq:.2f}，明顯跑贏大盤")
        elif rs_qqq < 0.9:
            warnings.append(f"相對QQQ強度{rs_qqq:.2f}，跑輸大盤")

    if rs_sector is not None:
        if rs_sector > 1.05:
            reasons.append(f"相對板塊強度{rs_sector:.2f}，板塊領跑")
        elif rs_sector < 0.9:
            warnings.append(f"相對板塊強度{rs_sector:.2f}，板塊落後")

    if breakout_score >= 75:
        reasons.append(f"突破品質分數{breakout_score}，放量有效突破")
    elif breakout_score < 40:
        warnings.append(f"突破品質分數{breakout_score}，突破力度不足")

    if dist_risk > 70:
        warnings.append("放量長上影或放量下跌，機構可能在出貨")

    if smart_money > 70:
        reasons.append("Smart Money分數高：大量日收盤接近高點")

    return reasons, warnings


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_institutional_flow(
    symbol: str,
    ohlcv: dict,
    benchmark_ohlcv: dict | None = None,
    sector_ohlcv: dict | None = None,
) -> dict:
    closes: list[float] = [float(x) for x in (ohlcv.get("closes") or [])]
    opens: list[float] = [float(x) for x in (ohlcv.get("opens") or [])]
    highs: list[float] = [float(x) for x in (ohlcv.get("highs") or [])]
    lows: list[float] = [float(x) for x in (ohlcv.get("lows") or [])]
    volumes: list[float] = [float(x) for x in (ohlcv.get("volumes") or [])]
    is_demo: bool = bool(ohlcv.get("is_demo", False))

    n = len(closes)

    # Align parallel arrays to the shortest series
    length = min(len(closes), len(opens), len(highs), len(lows), len(volumes))
    closes = closes[:length]
    opens = opens[:length]
    highs = highs[:length]
    lows = lows[:length]
    volumes = volumes[:length]
    n = length

    base_result: dict[str, Any] = {
        "ok": True,
        "symbol": symbol,
        "is_demo": is_demo,
        "bar_count": n,
    }

    if n < 10:
        return {
            **base_result,
            "relative_strength_vs_QQQ": None,
            "relative_strength_vs_sector": None,
            "volume_accumulation_score": 50,
            "price_volume_confirmation": 50,
            "breakout_quality": 50,
            "distribution_risk": 0,
            "smart_money_score": 50,
            "institutional_accumulation_score": 50,
            "liquidity_quality": 15,
            "volatility_stability": 55,
            "flow_direction": "NEUTRAL",
            "confidence": 20 if not is_demo else min(20, 30),
            "reasons": [],
            "warnings": ["數據不足（少於10根K棒），無法進行機構流向分析"],
            "breakout_detail": {"has_breakout": False},
        }

    # All-zero volume guard
    all_zero_vol = all(v == 0 for v in volumes)
    if all_zero_vol:
        volumes = [1.0] * n

    # Relative strength
    bench_closes: list[float] = []
    if benchmark_ohlcv:
        bench_closes = [float(x) for x in (benchmark_ohlcv.get("closes") or [])]
    rs_qqq = _relative_strength(closes, bench_closes) if bench_closes else None

    sector_closes: list[float] = []
    if sector_ohlcv:
        sector_closes = [float(x) for x in (sector_ohlcv.get("closes") or [])]
    rs_sector = _relative_strength(closes, sector_closes) if sector_closes else None

    # Component scores
    vol_acc_score, up_vol_ratio, obv_mom = _calc_volume_accumulation(closes, opens, volumes)
    pv_confirmation = _calc_price_volume_confirmation(closes, volumes)
    bq_score, bq_detail = _calc_breakout_quality(closes, highs, lows, volumes)
    dist_risk = _calc_distribution_risk(closes, highs, lows, volumes)
    smart_score = _calc_smart_money(closes, highs, lows, opens, volumes)
    liq_quality = _calc_liquidity_quality(closes, volumes)
    vol_stability = _calc_volatility_stability(closes)

    # Composite
    inst_acc = round(
        vol_acc_score * 0.25
        + smart_score * 0.25
        + pv_confirmation * 0.15
        + bq_score * 0.15
        + (100 - dist_risk) * 0.10
        + liq_quality * 0.05
        + vol_stability * 0.05
    )
    inst_acc = _clamp(inst_acc)

    # Flow direction – strict priority order
    vol_mult = bq_detail.get("vol_mult", 1.0)
    has_breakout = bq_detail.get("has_breakout", False)

    if (
        has_breakout
        and (rs_qqq is not None and rs_qqq < 0.9 or rs_sector is not None and rs_sector < 0.85)
        and vol_mult < 1.3
    ):
        flow_direction = "FAKE_BREAKOUT"
    elif dist_risk >= 65 or (dist_risk >= 45 and inst_acc < 40 and obv_mom < -0.2):
        flow_direction = "DISTRIBUTION"
    elif inst_acc >= 62 and smart_score >= 55 and dist_risk < 40:
        flow_direction = "ACCUMULATION"
    else:
        flow_direction = "NEUTRAL"

    # Confidence
    if n < 20:
        confidence = 20
    elif n < 60:
        confidence = 50
    elif n < 80:
        confidence = 70
    elif n < 120:
        confidence = 80
    else:
        confidence = 85

    if is_demo:
        confidence = min(confidence, 30)

    has_benchmark = rs_qqq is not None or rs_sector is not None
    if has_benchmark:
        confidence = min(confidence + 10, 95)

    # Reasons / warnings
    reasons, warnings = _build_reasons_warnings(
        up_vol_ratio=up_vol_ratio,
        obv_mom=obv_mom,
        rs_qqq=rs_qqq,
        rs_sector=rs_sector,
        breakout_score=bq_score,
        dist_risk=dist_risk,
        smart_money=smart_score,
        period=min(20, n),
    )

    if all_zero_vol:
        warnings.append("成交量全為零，計算結果僅供參考")

    return {
        "ok": True,
        "symbol": symbol,
        "is_demo": is_demo,
        "bar_count": n,
        "relative_strength_vs_QQQ": rs_qqq,
        "relative_strength_vs_sector": rs_sector,
        "volume_accumulation_score": vol_acc_score,
        "price_volume_confirmation": pv_confirmation,
        "breakout_quality": bq_score,
        "distribution_risk": dist_risk,
        "smart_money_score": smart_score,
        "institutional_accumulation_score": inst_acc,
        "liquidity_quality": liq_quality,
        "volatility_stability": vol_stability,
        "flow_direction": flow_direction,
        "confidence": confidence,
        "reasons": reasons,
        "warnings": warnings,
        "breakout_detail": bq_detail,
    }
