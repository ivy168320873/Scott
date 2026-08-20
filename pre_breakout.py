"""Pre-breakout readiness engine for RocketStock.

Scores whether a stock is *preparing* to break out before the breakout happens.
The engine is intentionally deterministic and OHLCV-only so it can be tested,
backtested and calibrated before adding news/sector features.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from math import sqrt
from statistics import mean, pstdev
from typing import Any, Mapping


@dataclass(frozen=True)
class BreakoutReadiness:
    score: int
    state: str
    pivot: float
    trigger_price: float
    invalidation_price: float
    distance_to_pivot_pct: float
    components: dict[str, int]
    reasons: list[str]
    warnings: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _clip(value: float, lo: float = 0.0, hi: float = 100.0) -> int:
    return int(round(max(lo, min(hi, value))))


def _safe_mean(values: list[float]) -> float:
    return mean(values) if values else 0.0


def _atr(highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> float:
    if len(closes) < 2:
        return 0.0
    trs: list[float] = []
    start = max(1, len(closes) - period)
    for i in range(start, len(closes)):
        trs.append(max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        ))
    return _safe_mean(trs)


def _ema(values: list[float], period: int) -> float:
    if not values:
        return 0.0
    alpha = 2.0 / (period + 1.0)
    result = values[0]
    for value in values[1:]:
        result = alpha * value + (1.0 - alpha) * result
    return result


def _slope(values: list[float]) -> float:
    """Least-squares slope normalized by the series mean."""
    n = len(values)
    if n < 2:
        return 0.0
    x_mean = (n - 1) / 2.0
    y_mean = _safe_mean(values)
    if y_mean == 0:
        return 0.0
    numerator = sum((i - x_mean) * (v - y_mean) for i, v in enumerate(values))
    denominator = sum((i - x_mean) ** 2 for i in range(n)) or 1.0
    return (numerator / denominator) / y_mean


def _validate_ohlcv(ohlcv: Mapping[str, Any]) -> tuple[list[float], list[float], list[float], list[float], list[float]]:
    closes = [float(x) for x in ohlcv.get("closes", [])]
    highs = [float(x) for x in ohlcv.get("highs", [])]
    lows = [float(x) for x in ohlcv.get("lows", [])]
    opens = [float(x) for x in ohlcv.get("opens", closes)]
    volumes = [float(x) for x in ohlcv.get("volumes", [])]
    n = len(closes)
    if n < 60 or not all(len(series) >= n for series in (highs, lows, opens, volumes)):
        raise ValueError("pre-breakout engine requires at least 60 aligned OHLCV bars")
    if any(v <= 0 for v in closes[-60:]):
        raise ValueError("invalid non-positive close in recent OHLCV bars")
    return closes, highs, lows, opens, volumes


def score_pre_breakout(
    ohlcv: Mapping[str, Any],
    *,
    market_score: float = 70.0,
    sector_score: float = 70.0,
    rs_score: float | None = None,
) -> dict[str, Any]:
    """Return a 0-100 readiness score and actionable breakout levels.

    The score intentionally rewards *pre-breakout* structure, not an already
    extended move. A stock >3% above the pivot is penalized as late/chase risk.
    """
    closes, highs, lows, _opens, volumes = _validate_ohlcv(ohlcv)
    c = closes[-1]

    # Use prior bars only for the pivot so today's close cannot manufacture its
    # own resistance level. 50 bars captures common VCP/base resistance.
    pivot = max(highs[-51:-1])
    distance_pct = (pivot - c) / pivot * 100.0
    atr14 = _atr(highs, lows, closes, 14)
    trigger = pivot + max(0.001 * pivot, 0.10 * atr14)

    ma20 = _safe_mean(closes[-20:])
    ma50 = _safe_mean(closes[-50:])
    recent_low = min(lows[-10:])
    invalidation = max(recent_low, ma20 - 1.2 * atr14)

    # 1) Proximity to pivot: ideal ~0-2%; too far means not ready, above means chase.
    if 0 <= distance_pct <= 1.0:
        proximity = 100
    elif 1.0 < distance_pct <= 3.0:
        proximity = _clip(100 - (distance_pct - 1.0) * 15)
    elif 3.0 < distance_pct <= 8.0:
        proximity = _clip(70 - (distance_pct - 3.0) * 10)
    elif distance_pct < 0:
        proximity = _clip(50 + distance_pct * 20)  # already above pivot => penalize
    else:
        proximity = 10

    # 2) Volatility contraction: compare recent 10-day range to prior 20-day range.
    recent_ranges = [(highs[i] - lows[i]) / closes[i] for i in range(len(closes) - 10, len(closes))]
    prior_ranges = [(highs[i] - lows[i]) / closes[i] for i in range(len(closes) - 30, len(closes) - 10)]
    recent_range = _safe_mean(recent_ranges)
    prior_range = _safe_mean(prior_ranges) or recent_range or 1e-9
    contraction_ratio = recent_range / prior_range
    contraction = _clip((1.35 - contraction_ratio) / 0.65 * 100)

    # 3) Volume dry-up: desirable before breakout; not zero/dead liquidity.
    recent_vol = _safe_mean(volumes[-5:])
    base_vol = _safe_mean(volumes[-25:-5]) or recent_vol or 1.0
    vol_ratio = recent_vol / base_vol
    volume_dryup = _clip((1.15 - vol_ratio) / 0.55 * 100)
    if recent_vol <= 0:
        volume_dryup = 0

    # 4) Accumulation: up-day volume vs down-day volume over last 20 bars.
    up_vol: list[float] = []
    down_vol: list[float] = []
    for i in range(len(closes) - 20, len(closes)):
        if closes[i] >= closes[i - 1]:
            up_vol.append(volumes[i])
        else:
            down_vol.append(volumes[i])
    up_avg = _safe_mean(up_vol)
    down_avg = _safe_mean(down_vol)
    accumulation_ratio = up_avg / down_avg if down_avg > 0 else 1.5
    accumulation = _clip((accumulation_ratio - 0.75) / 0.75 * 100)

    # 5) Trend structure: rising 20/50-day trend, price above both.
    structure_points = 0.0
    structure_points += 35 if c >= ma20 else 0
    structure_points += 25 if ma20 >= ma50 else 0
    structure_points += 20 if _slope(closes[-20:]) > 0 else 0
    structure_points += 20 if _slope(closes[-50:]) > 0 else 0
    trend_structure = _clip(structure_points)

    # 6) Pressure absorption: repeated tests near pivot, with higher lows.
    tolerance = max(0.008 * pivot, 0.30 * atr14)
    tests = sum(1 for h in highs[-20:-1] if pivot - tolerance <= h <= pivot + tolerance)
    higher_low_slope = _slope(lows[-10:])
    pressure = _clip(min(tests, 4) * 20 + (20 if higher_low_slope > 0 else 0))

    # 7) Relative-strength proxy when no external RS percentile is supplied.
    if rs_score is None:
        ret20 = c / closes[-21] - 1.0
        ret60 = c / closes[-60] - 1.0
        rs_score = _clip(50 + ret20 * 180 + ret60 * 80)
    relative_strength = _clip(float(rs_score))

    market = _clip(float(market_score))
    sector = _clip(float(sector_score))

    weights = {
        "proximity": 0.18,
        "contraction": 0.16,
        "volume_dryup": 0.12,
        "accumulation": 0.12,
        "trend_structure": 0.14,
        "pressure_absorption": 0.10,
        "relative_strength": 0.10,
        "market": 0.04,
        "sector": 0.04,
    }
    components = {
        "proximity": proximity,
        "contraction": contraction,
        "volume_dryup": volume_dryup,
        "accumulation": accumulation,
        "trend_structure": trend_structure,
        "pressure_absorption": pressure,
        "relative_strength": relative_strength,
        "market": market,
        "sector": sector,
    }
    raw = sum(components[k] * weights[k] for k in weights)

    warnings: list[str] = []
    if ohlcv.get("is_demo"):
        raw = min(raw, 45)
        warnings.append("demo data: readiness capped")
    if distance_pct < -3.0:
        raw = min(raw, 55)
        warnings.append("price already extended above pivot")
    if c < ma50:
        raw = min(raw, 64)
        warnings.append("price below 50-day average")
    if market < 45:
        raw = min(raw, 69)
        warnings.append("weak market regime")

    score = _clip(raw)
    if score >= 85:
        state = "HIGH_PROBABILITY_CANDIDATE"
    elif score >= 75:
        state = "READY"
    elif score >= 60:
        state = "BUILDING"
    else:
        state = "NORMAL"

    reasons: list[str] = []
    if proximity >= 80:
        reasons.append("price is within striking distance of resistance")
    if contraction >= 75:
        reasons.append("volatility is contracting")
    if volume_dryup >= 70:
        reasons.append("volume is drying up constructively")
    if accumulation >= 70:
        reasons.append("up-day volume dominates down-day volume")
    if pressure >= 60:
        reasons.append("resistance is being tested repeatedly with improving lows")
    if relative_strength >= 75:
        reasons.append("relative strength is elevated")

    result = BreakoutReadiness(
        score=score,
        state=state,
        pivot=round(pivot, 4),
        trigger_price=round(trigger, 4),
        invalidation_price=round(invalidation, 4),
        distance_to_pivot_pct=round(distance_pct, 2),
        components=components,
        reasons=reasons,
        warnings=warnings,
    ).to_dict()
    result["metrics"] = {
        "atr14": round(atr14, 4),
        "ma20": round(ma20, 4),
        "ma50": round(ma50, 4),
        "recent_range_ratio": round(contraction_ratio, 3),
        "recent_volume_ratio": round(vol_ratio, 3),
        "resistance_tests_20d": tests,
    }
    result["source"] = ohlcv.get("source")
    result["fetched_at"] = ohlcv.get("fetched_at")
    return result


def rank_pre_breakout(
    symbols: list[str],
    ohlcv_fn,
    *,
    top_n: int = 10,
    market_score: float = 70.0,
    sector_scores: Mapping[str, float] | None = None,
    rs_scores: Mapping[str, float] | None = None,
) -> list[dict[str, Any]]:
    """Score and rank symbols; failures are isolated per symbol."""
    ranked: list[dict[str, Any]] = []
    sector_scores = sector_scores or {}
    rs_scores = rs_scores or {}
    for raw_symbol in symbols:
        symbol = str(raw_symbol or "").upper().strip()
        if not symbol:
            continue
        try:
            ohlcv = ohlcv_fn(symbol)
            if not ohlcv:
                continue
            row = score_pre_breakout(
                ohlcv,
                market_score=market_score,
                sector_score=float(sector_scores.get(symbol, 70.0)),
                rs_score=rs_scores.get(symbol),
            )
            row["symbol"] = symbol
            ranked.append(row)
        except Exception as exc:  # scanner must not fail the whole batch
            ranked.append({"symbol": symbol, "score": 0, "state": "ERROR", "error": str(exc)})
    ranked.sort(key=lambda item: (item.get("score", 0), -abs(item.get("distance_to_pivot_pct", 999))), reverse=True)
    return ranked[: max(1, int(top_n))]
