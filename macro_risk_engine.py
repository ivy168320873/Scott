"""
Macro Risk Engine — Phase 13C
Compute macro-level risk score from multiple asset classes.

Public API
----------
run_macro_risk(ohlcv_fn) -> dict

ohlcv_fn: callable(symbol: str) -> dict with keys
          {closes, opens, highs, lows, volumes, timestamps, is_demo}
"""
from __future__ import annotations

import statistics

from economic_calendar import get_upcoming_events
from macro_data import get_macro_snapshot

# ── Public API ─────────────────────────────────────────────────────────────────

def run_macro_risk(ohlcv_fn) -> dict:
    """
    Compute macro-level risk score from multiple asset classes.

    Parameters
    ----------
    ohlcv_fn : callable(symbol: str) -> dict
        Returns a dict with keys: closes, opens, highs, lows,
        volumes, timestamps, is_demo.

    Returns
    -------
    dict  (see module docstring for full schema)
    """
    # ── Fetch all symbols ──────────────────────────────────────────────────────
    spy_raw  = _safe_fetch(ohlcv_fn, "SPY")
    qqq_raw  = _safe_fetch(ohlcv_fn, "QQQ")
    iwm_raw  = _safe_fetch(ohlcv_fn, "IWM")
    xlu_raw  = _safe_fetch(ohlcv_fn, "XLU")
    xly_raw  = _safe_fetch(ohlcv_fn, "XLY")
    tlt_raw  = _safe_fetch(ohlcv_fn, "TLT")
    soxx_raw = _safe_fetch(ohlcv_fn, "SOXX")
    uup_raw  = _safe_fetch(ohlcv_fn, "UUP")
    vixy_raw = _safe_fetch(ohlcv_fn, "^VIX")
    volatility_is_proxy = False
    if not vixy_raw:
        volatility_is_proxy = True
        vixy_raw = _safe_fetch(ohlcv_fn, "VIXY")
    if not vixy_raw:
        volatility_is_proxy = True
        vixy_raw = _safe_fetch(ohlcv_fn, "VXX")

    # ── is_demo flag ──────────────────────────────────────────────────────────
    all_fetched = [
        spy_raw, qqq_raw, iwm_raw, xlu_raw, xly_raw,
        tlt_raw, soxx_raw, uup_raw, vixy_raw,
    ]
    is_demo = any(d.get("is_demo", False) for d in all_fetched if d)

    # ── Convenience: extract close lists ──────────────────────────────────────
    spy_c  = spy_raw.get("closes",  [])
    qqq_c  = qqq_raw.get("closes",  [])
    iwm_c  = iwm_raw.get("closes",  [])
    xlu_c  = xlu_raw.get("closes",  [])
    xly_c  = xly_raw.get("closes",  [])
    tlt_c  = tlt_raw.get("closes",  [])
    soxx_c = soxx_raw.get("closes", [])
    uup_c  = uup_raw.get("closes",  [])
    vixy_c = vixy_raw.get("closes", [])

    # ── Compute components ────────────────────────────────────────────────────
    eb_score  = _calc_equity_breadth(spy_c, qqq_c, iwm_c)
    bq_score  = _calc_breadth_quality(qqq_c, soxx_c)
    dr_score  = _calc_defensive_rotation(xlu_c, xly_c)
    bm_score  = _calc_bond_market(tlt_c)
    vp_score  = (
        _calc_volatility_proxy(vixy_c, spy_c)
        if volatility_is_proxy
        else _calc_vix(vixy_c, spy_c)
    )
    direct_macro = get_macro_snapshot()
    rates_credit_score = int(direct_macro.get("rates_credit_score", 50))
    event_calendar = get_upcoming_events()

    component_scores = {
        "equity_breadth":     eb_score,
        "breadth_quality":    bq_score,
        "defensive_rotation": dr_score,
        "bond_market":        bm_score,
        "volatility":         vp_score,
        "volatility_proxy":   vp_score,  # backward-compatible UI key
        "rates_credit":       rates_credit_score,
        "dollar_strength":    _calc_dollar_strength(uup_c),
    }

    # ── Composite macro score ─────────────────────────────────────────────────
    macro_score = round(
        eb_score  * 0.25
        + bq_score  * 0.15
        + dr_score  * 0.15
        + bm_score  * 0.10
        + vp_score  * 0.15
        + rates_credit_score * 0.20
    )
    macro_score = max(0, min(100, macro_score))

    # ── Regime / risk level / budget ──────────────────────────────────────────
    macro_regime        = _classify_regime(macro_score)
    risk_level          = _classify_risk_level(macro_score)
    risk_budget_adj     = _regime_budget(macro_regime)

    # ── Key drivers ───────────────────────────────────────────────────────────
    key_drivers = _build_key_drivers(
        spy_c, qqq_c, soxx_c, xlu_c, xly_c, tlt_c, vixy_c,
        component_scores, macro_score,
    )

    # ── Warnings ──────────────────────────────────────────────────────────────
    warnings = _build_warnings(
        spy_c, qqq_c, uup_c, vixy_c, component_scores, macro_score,
    )
    warnings.extend(direct_macro.get("warnings") or [])
    warnings.extend(event_calendar.get("warnings") or [])

    return {
        "ok":                    True,
        "macro_score":           macro_score,
        "macro_regime":          macro_regime,
        "risk_level":            risk_level,
        "risk_budget_adjustment": risk_budget_adj,
        "component_scores":      component_scores,
        "key_drivers":           key_drivers,
        "warnings":              warnings,
        "upcoming_events":       event_calendar.get("events") or [],
        "event_calendar":        event_calendar,
        "direct_macro_data":     direct_macro,
        "volatility_source":     "ETF_PROXY" if volatility_is_proxy else "VIX_INDEX",
        "data_completeness":     {
            "direct_rates_credit": direct_macro.get("status"),
            "event_calendar": event_calendar.get("status"),
            "vix_direct": not volatility_is_proxy,
        },
        "is_demo":               is_demo,
        "disclaimer":            "此為決策輔助，不代表自動下單。",
    }


# ── Component calculators ──────────────────────────────────────────────────────

def _calc_equity_breadth(spy_c: list, qqq_c: list, iwm_c: list) -> int:
    """
    equity_breadth (30% weight)
    SPY MA200 / MA50 / QQQ MA20 / IWM vs SPY 20d relative return
    Base 50, sum adjustments, clamp 0-100.
    """
    score = 50

    # SPY above MA200
    spy_ma200 = _above_ma(spy_c, 200)
    if spy_ma200 is True:
        score += 35
    elif spy_ma200 is False:
        score -= 30

    # SPY above MA50
    spy_ma50 = _above_ma(spy_c, 50)
    if spy_ma50 is True:
        score += 20
    elif spy_ma50 is False:
        score -= 10

    # QQQ above MA20
    qqq_ma20 = _above_ma(qqq_c, 20)
    if qqq_ma20 is True:
        score += 15
    elif qqq_ma20 is False:
        score -= 10

    # IWM vs SPY 20d return (breadth health check)
    iwm_ret = _20d_return(iwm_c)
    spy_ret = _20d_return(spy_c)
    if iwm_ret is not None and spy_ret is not None:
        if iwm_ret > spy_ret:
            score += 10  # small-caps keeping up = healthy breadth
        else:
            score -= 5

    return max(0, min(100, round(score)))


def _calc_breadth_quality(qqq_c: list, soxx_c: list) -> int:
    """
    breadth_quality (20% weight)
    QQQ/SPY relative strength, SOXX vs QQQ leadership.
    Base 50, clamp 0-100.
    """
    score = 50

    # SOXX relative to QQQ over 20d (semis leading = risk-on)
    soxx_ret = _20d_return(soxx_c)
    qqq_ret  = _20d_return(qqq_c)

    if soxx_ret is not None and qqq_ret is not None:
        # SOXX > QQQ return: semis leading
        if soxx_ret > qqq_ret:
            score += 15

    # QQQ relative strength ratio over 20d > 1.05
    if qqq_ret is not None:
        # Treat qqq_ret as percentage: convert to ratio → 1 + ret/100
        qqq_rs = 1 + (qqq_ret / 100.0)
        if qqq_rs > 1.05:
            score += 10

    return max(0, min(100, round(score)))


def _calc_defensive_rotation(xlu_c: list, xly_c: list) -> int:
    """
    defensive_rotation (25% weight)
    XLU vs XLY 20d return.
    Formula: base 50 + (xly_ret - xlu_ret) * 200, clamp 0-100.
    """
    xlu_ret = _20d_return(xlu_c)
    xly_ret = _20d_return(xly_c)

    if xlu_ret is None or xly_ret is None:
        return 50  # insufficient data

    diff = (xly_ret - xlu_ret) / 100.0  # convert pct → decimal for formula
    score = 50 + diff * 200
    return max(0, min(100, round(score)))


def _calc_bond_market(tlt_c: list) -> int:
    """
    bond_market (15% weight)
    TLT 20d return.
    Formula: base 50 + tlt_20d_return_decimal * 400, clamp 0-100.
    (TLT -5% → 50 + (-0.05)*400 = 30)
    """
    tlt_ret = _20d_return(tlt_c)
    if tlt_ret is None:
        return 50

    score = 50 + (tlt_ret / 100.0) * 400
    return max(0, min(100, round(score)))


def _calc_vix(vix_c: list, spy_c: list) -> int:
    """Score the direct CBOE VIX level; higher score means lower macro risk."""
    valid = [float(value) for value in vix_c if value and float(value) > 0]
    if not valid:
        return _calc_volatility_proxy([], spy_c)
    level = valid[-1]
    if level < 15:
        score = 88
    elif level < 20:
        score = 72
    elif level < 25:
        score = 52
    elif level < 35:
        score = 30
    else:
        score = 10
    if len(valid) >= 6 and valid[-6] > 0:
        change_5d = (level - valid[-6]) / valid[-6] * 100
        if change_5d > 25:
            score -= 12
        elif change_5d < -20:
            score += 8
    return max(0, min(100, round(score)))


def _calc_volatility_proxy(vixy_c: list, spy_c: list) -> int:
    """
    volatility_proxy (10% weight)
    VIXY/VXX 20d return (inverted) or SPY realized vol fallback.
    Formula from VIXY: base 50 - vixy_20d_return_decimal * 400, clamp 0-100.
    """
    vixy_ret = _20d_return(vixy_c)

    if vixy_ret is not None:
        score = 50 - (vixy_ret / 100.0) * 400
        return max(0, min(100, round(score)))

    # Fallback: SPY 5-day realized daily vol
    if len(spy_c) >= 6:
        recent = spy_c[-6:]
        daily_rets = [
            abs((recent[i] - recent[i - 1]) / recent[i - 1])
            for i in range(1, len(recent))
            if recent[i - 1] > 0
        ]
        if daily_rets:
            avg_daily_vol = statistics.mean(daily_rets) * 100  # as percentage
            if avg_daily_vol < 0.8:
                return 80
            elif avg_daily_vol < 1.2:
                return 65
            elif avg_daily_vol < 1.8:
                return 50
            elif avg_daily_vol < 2.5:
                return 35
            else:
                return 20

    return 50  # no data


def _calc_dollar_strength(uup_c: list) -> int:
    """
    dollar_strength (informational only — 0% direct weight)
    UUP 20d return as DXY proxy. Used in warnings.
    Score > 50 means strengthening dollar.
    """
    uup_ret = _20d_return(uup_c)
    if uup_ret is None:
        return 50
    score = 50 + (uup_ret / 100.0) * 400
    return max(0, min(100, round(score)))


# ── Regime / risk classification ──────────────────────────────────────────────

def _classify_regime(macro_score: int) -> str:
    if macro_score >= 68:
        return "EXPANSION"
    elif macro_score >= 45:
        return "NEUTRAL"
    elif macro_score >= 28:
        return "TIGHTENING"
    else:
        return "RISK_OFF"


def _classify_risk_level(macro_score: int) -> str:
    if macro_score >= 70:
        return "LOW"
    elif macro_score >= 45:
        return "MEDIUM"
    elif macro_score >= 25:
        return "HIGH"
    else:
        return "EXTREME"


def _regime_budget(macro_regime: str) -> float:
    return {
        "EXPANSION":  1.2,
        "NEUTRAL":    0.85,
        "TIGHTENING": 0.50,
        "RISK_OFF":   0.20,
    }.get(macro_regime, 0.85)


# ── Key drivers builder ───────────────────────────────────────────────────────

def _build_key_drivers(
    spy_c:    list,
    qqq_c:    list,
    soxx_c:   list,
    xlu_c:    list,
    xly_c:    list,
    tlt_c:    list,
    vixy_c:   list,
    component_scores: dict,
    macro_score: int,
) -> list[str]:
    drivers: list[tuple[int, str]] = []  # (priority, message)

    # SPY trend vs MA200
    spy_ma200 = _above_ma(spy_c, 200)
    if spy_ma200 is True:
        drivers.append((10, "SPY 站穩 MA200，中長期趨勢健康"))
    elif spy_ma200 is False:
        drivers.append((10, "SPY 跌破 MA200，中長期趨勢偏空"))

    # SPY trend vs MA50
    spy_ma50 = _above_ma(spy_c, 50)
    if spy_ma50 is False and spy_ma200 is True:
        drivers.append((8, "SPY 跌破 MA50，短中期趨勢轉弱"))

    # Semis leadership
    soxx_ret = _20d_return(soxx_c)
    qqq_ret  = _20d_return(qqq_c)
    if soxx_ret is not None and qqq_ret is not None:
        if soxx_ret > qqq_ret:
            drivers.append((9, "SOXX 領先 QQQ，科技週期偏多"))
        else:
            drivers.append((7, "SOXX 落後 QQQ，半導體走弱"))

    # Defensive rotation
    xlu_ret = _20d_return(xlu_c)
    xly_ret = _20d_return(xly_c)
    if xlu_ret is not None and xly_ret is not None:
        if xly_ret > xlu_ret:
            drivers.append((8, "消費/成長板塊領漲，市場偏進攻型"))
        else:
            drivers.append((8, "防禦板塊輪動中，市場偏保守"))

    # Bond market / rates
    tlt_ret = _20d_return(tlt_c)
    if tlt_ret is not None:
        if tlt_ret < -3:
            drivers.append((9, "TLT 下跌，利率上升壓力明顯"))
        elif tlt_ret > 3:
            drivers.append((9, "TLT 上漲，利率走低，流動性改善"))

    # Volatility
    vixy_ret = _20d_return(vixy_c)
    if vixy_ret is not None:
        if vixy_ret > 15:
            drivers.append((9, "波動率偏高，建議降低風險敞口"))
        elif vixy_ret < -15:
            drivers.append((7, "波動率明顯下降，市場趨於穩定"))

    # Macro score summary
    if macro_score >= 68:
        drivers.append((6, "整體宏觀環境偏多，擴張期格局"))
    elif macro_score < 28:
        drivers.append((10, "宏觀風險極高，建議大幅降低曝險"))

    # Sort by priority descending, take top 4
    drivers.sort(key=lambda x: x[0], reverse=True)
    return [msg for _, msg in drivers[:4]]


# ── Warnings builder ──────────────────────────────────────────────────────────

def _build_warnings(
    spy_c:    list,
    qqq_c:    list,
    uup_c:    list,
    vixy_c:   list,
    component_scores: dict,
    macro_score: int,
) -> list[str]:
    warnings: list[str] = []

    # SPY below MA200
    if _above_ma(spy_c, 200) is False:
        warnings.append("SPY 跌破 MA200：熊市環境，禁止主動買進")

    # Both SPY and QQQ below MA20
    if _above_ma(spy_c, 20) is False and _above_ma(qqq_c, 20) is False:
        warnings.append("SPY 與 QQQ 同時跌破 MA20：市場短期走弱")

    # High volatility
    vp = component_scores.get("volatility_proxy", 50)
    if vp < 30:
        warnings.append("波動率指數偏高，市場恐慌情緒上升")

    # Dollar strength warning
    ds = component_scores.get("dollar_strength", 50)
    uup_ret = _20d_return(uup_c)
    if uup_ret is not None and uup_ret > 3:
        warnings.append(f"美元走強（UUP +{uup_ret:.1f}%），風險資產承壓")

    # Bond market tightening
    bm = component_scores.get("bond_market", 50)
    if bm < 35:
        warnings.append("債券市場走弱，利率上升環境不利股市估值")

    # Extreme macro risk
    if macro_score < 28:
        warnings.append("宏觀評分極低，建議大幅降低部位，保留現金")
    elif macro_score < 45:
        warnings.append("宏觀環境收緊，建議控制新倉規模")

    # Defensive rotation warning
    dr = component_scores.get("defensive_rotation", 50)
    if dr < 35:
        warnings.append("防禦性板塊輪動明顯，機構資金偏向避險")

    return warnings


# ── Helper functions ──────────────────────────────────────────────────────────

def _safe_fetch(fn, symbol: str) -> dict:
    """Fetch OHLCV data for a symbol; return {} on any error."""
    try:
        result = fn(symbol)
        if isinstance(result, dict):
            return result
        return {}
    except Exception:
        return {}


def _sma(closes: list, n: int) -> float | None:
    """Simple moving average of the last n valid closes."""
    if not closes:
        return None
    tail = [v for v in closes[-n:] if v and v > 0]
    if not tail:
        return None
    return sum(tail) / len(tail)


def _20d_return(closes: list) -> float | None:
    """
    20-day return as a percentage (e.g. 5.2 for +5.2%).
    Returns None if insufficient data.
    """
    valid = [v for v in closes if v and v > 0]
    if len(valid) < 21:
        return None
    base  = valid[-21]
    latest = valid[-1]
    if base == 0:
        return None
    return round((latest - base) / base * 100, 4)


def _above_ma(closes: list, period: int) -> bool | None:
    """
    True  if the latest close is above the period-day SMA.
    False if below.
    None  if insufficient data.
    """
    valid = [v for v in closes if v and v > 0]
    if len(valid) < period + 1:
        return None
    ma = _sma(valid, period)
    if ma is None:
        return None
    return valid[-1] > ma
