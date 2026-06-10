"""
Portfolio Manager Engine — Phase 13B
Computes institutional-grade portfolio allocation recommendations.

Public API
----------
run_portfolio_recommendation(
    account_value, current_cash, holdings, watchlist,
    risk_preference, ohlcv_fn
) -> dict
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

import market_regime_engine       as _mre
import data_quality_engine        as _dqe
import position_sizing_engine     as _pse
import institutional_flow_engine  as _ife

try:
    import signal_confidence_engine as _sce
    _HAS_SCE = True
except ImportError:
    _HAS_SCE = False

try:
    import top_tier_decision_engine as _ttde
    _HAS_TTDE = True
except ImportError:
    _HAS_TTDE = False

_DISCLAIMER = "此為決策輔助系統，不代表自動下單，不構成投資建議。操作前請自行評估風險。"

# Cash allocation targets by regime
_CASH_TARGETS = {
    "RISK_ON":    (0.05, 0.15),
    "NEUTRAL":    (0.15, 0.30),
    "RISK_OFF":   (0.40, 0.70),
    "CRASH_RISK": (0.80, 1.00),
}

# Max single-position size by risk preference
_MAX_SINGLE_PCT = {
    "conservative": 0.10,
    "balanced":     0.20,
    "aggressive":   0.25,
}

# Max sector concentration by risk preference
_MAX_SECTOR_PCT = {
    "conservative": 0.30,
    "balanced":     0.40,
    "aggressive":   0.50,
}

# Sector ETF proxies (for institutional flow reference)
_SECTOR_ETFS = {
    "半導體AI晶片": "SOXX",
    "AI雲端軟體/網路安全": "XLK",
    "電力能源": "XLE",
    "國防航太": "ITA",
    "台股半導體IC": "2330.TW",
}


# ── Public API ────────────────────────────────────────────────────────────────

def run_portfolio_recommendation(
    account_value: float,
    current_cash: float,
    holdings: list[dict],
    watchlist: list[str],
    risk_preference: str,
    ohlcv_fn,
) -> dict:
    """
    holdings  : list of {symbol, shares, cost, sector (optional), current_price (optional)}
    watchlist : list of symbol strings
    risk_preference: "conservative" | "balanced" | "aggressive"
    ohlcv_fn  : callable(symbol) -> ohlcv_dict
    """
    risk_pref = str(risk_preference or "balanced").lower()
    if risk_pref not in ("conservative", "balanced", "aggressive"):
        risk_pref = "balanced"

    account_value = max(0.0, float(account_value or 0))
    current_cash  = max(0.0, float(current_cash  or 0))
    if account_value == 0:
        account_value = current_cash

    risk_notes: list[str] = []
    is_demo_any = False

    # ── Market regime ─────────────────────────────────────────────────────────
    try:
        mr = _mre.run_market_regime(ohlcv_fn)
    except Exception:
        mr = {"market_regime": "NEUTRAL", "market_score": 50,
              "risk_budget_multiplier": 0.6, "is_demo": True}

    regime = mr.get("market_regime", "NEUTRAL")
    if mr.get("is_demo"):
        is_demo_any = True

    # ── Benchmark for flow analysis ───────────────────────────────────────────
    qqq_ohlcv = _safe_ohlcv(ohlcv_fn, "QQQ")
    if (qqq_ohlcv or {}).get("is_demo"):
        is_demo_any = True

    # ── Analyse each holding ──────────────────────────────────────────────────
    holding_analyses: list[dict] = []
    for h in (holdings or []):
        sym = str(h.get("symbol") or h.get("sym") or "").upper().strip()
        if not sym:
            continue
        analysis = _analyse_position(sym, h, ohlcv_fn, qqq_ohlcv, mr, risk_pref, account_value)
        if analysis.get("is_demo"):
            is_demo_any = True
        holding_analyses.append(analysis)

    # ── Analyse watchlist candidates ──────────────────────────────────────────
    watchlist_analyses: list[dict] = []
    for sym in (watchlist or []):
        sym = str(sym or "").upper().strip()
        if not sym or any(a["symbol"] == sym for a in holding_analyses):
            continue
        analysis = _analyse_candidate(sym, ohlcv_fn, qqq_ohlcv, mr, risk_pref, account_value)
        if analysis.get("is_demo"):
            is_demo_any = True
        watchlist_analyses.append(analysis)

    # ── Cash allocation ───────────────────────────────────────────────────────
    cash_pct_now = current_cash / account_value if account_value > 0 else 0
    cash_min, cash_max = _CASH_TARGETS.get(regime, (0.15, 0.30))
    recommended_cash_pct = round((cash_min + cash_max) / 2, 2)

    if regime == "CRASH_RISK":
        risk_notes.append("崩跌風險模式：現金應達 80%+ 以上，立即降低所有風險敞口")
    elif regime == "RISK_OFF":
        risk_notes.append("市場防守狀態：現金目標 40-70%，只保留高品質持倉")
    elif cash_pct_now > cash_max + 0.10:
        risk_notes.append(f"現金比例 {cash_pct_now:.0%} 偏高，可考慮分批建倉")
    elif cash_pct_now < cash_min - 0.05:
        risk_notes.append(f"現金比例 {cash_pct_now:.0%} 偏低，建議適度減倉保留彈性")

    # ── Categorise holdings → add/trim/exit ──────────────────────────────────
    positions_to_add:  list[dict] = []
    positions_to_trim: list[dict] = []
    positions_to_exit: list[dict] = []

    for a in holding_analyses:
        rec = a.get("recommendation")
        if rec in ("ADD", "STRONG_BUY") and regime not in ("RISK_OFF", "CRASH_RISK"):
            positions_to_add.append({"symbol": a["symbol"], "reason": a.get("main_reason",""), "allocation_score": a.get("allocation_score",50)})
        elif rec in ("TRIM",):
            positions_to_trim.append({"symbol": a["symbol"], "reason": a.get("main_reason",""), "allocation_score": a.get("allocation_score",50)})
        elif rec in ("EXIT", "SELL", "AVOID") or a.get("flow_direction") == "DISTRIBUTION":
            positions_to_exit.append({"symbol": a["symbol"], "reason": a.get("main_reason",""), "allocation_score": a.get("allocation_score",50)})

    # Rotation candidates: watchlist items with high allocation scores
    watchlist_sorted = sorted(watchlist_analyses, key=lambda x: x.get("allocation_score", 0), reverse=True)
    rotation_candidates = [
        {"symbol": a["symbol"], "allocation_score": a.get("allocation_score", 50),
         "flow_direction": a.get("flow_direction", "NEUTRAL"),
         "reason": a.get("main_reason", "")}
        for a in watchlist_sorted[:5]
        if a.get("allocation_score", 0) >= 55 and regime not in ("RISK_OFF", "CRASH_RISK")
    ]

    # ── Recommended allocation ────────────────────────────────────────────────
    recommended_allocation = _build_allocation(
        holding_analyses, watchlist_analyses,
        account_value, cash_pct_now, recommended_cash_pct,
        regime, risk_pref,
    )

    # ── Sector exposure ───────────────────────────────────────────────────────
    sector_exposure = _compute_sector_exposure(holding_analyses, recommended_allocation, account_value)

    # ── Concentration risk ────────────────────────────────────────────────────
    concentration_risk = _concentration_risk(recommended_allocation, sector_exposure, risk_pref)
    if concentration_risk.get("warnings"):
        risk_notes.extend(concentration_risk["warnings"])

    # ── Portfolio metrics ─────────────────────────────────────────────────────
    exp_vol, max_dd = _estimate_risk_metrics(holding_analyses, recommended_allocation, regime)

    # ── Portfolio score ───────────────────────────────────────────────────────
    portfolio_score = _portfolio_score(mr, holding_analyses, regime, risk_pref)

    return {
        "ok":                       True,
        "generated_at":             datetime.now(timezone.utc).isoformat(),
        "is_demo":                  is_demo_any,
        # ── Top-level ──────────────────────────────────────────────────────────
        "portfolio_score":          portfolio_score,
        "market_regime":            regime,
        "market_score":             mr.get("market_score", 50),
        "risk_preference":          risk_pref,
        # ── Cash ──────────────────────────────────────────────────────────────
        "current_cash_pct":         round(cash_pct_now * 100, 1),
        "recommended_cash_pct":     round(recommended_cash_pct * 100, 1),
        "cash_target_range":        [round(cash_min * 100), round(cash_max * 100)],
        # ── Allocation ────────────────────────────────────────────────────────
        "recommended_allocation":   recommended_allocation,
        "sector_exposure":          sector_exposure,
        "concentration_risk":       concentration_risk,
        # ── Action lists ──────────────────────────────────────────────────────
        "positions_to_add":         positions_to_add,
        "positions_to_trim":        positions_to_trim,
        "positions_to_exit":        positions_to_exit,
        "rotation_candidates":      rotation_candidates,
        # ── Risk metrics ──────────────────────────────────────────────────────
        "expected_volatility":      round(exp_vol, 2),
        "max_drawdown_estimate":    round(max_dd, 2),
        "risk_notes":               risk_notes,
        # ── Holdings detail ───────────────────────────────────────────────────
        "holdings_analysis":        holding_analyses,
        "watchlist_analysis":       watchlist_analyses,
        # ── Footer ────────────────────────────────────────────────────────────
        "disclaimer":               _DISCLAIMER,
    }


# ── Internal analysis helpers ─────────────────────────────────────────────────

def _analyse_position(sym, holding, ohlcv_fn, qqq_ohlcv, mr, risk_pref, account_value) -> dict:
    """Analyse one existing holding position."""
    ohlcv = _safe_ohlcv(ohlcv_fn, sym)
    shares = float(holding.get("shares") or holding.get("qty") or 0)
    cost   = float(holding.get("cost") or holding.get("avg_cost") or 0)
    sector = str(holding.get("sector") or "")
    cur_price = _last_close(ohlcv) or float(holding.get("current_price") or 0) or cost

    mkt_value = shares * cur_price
    pnl_pct   = round((cur_price - cost) / cost * 100, 2) if cost > 0 else 0
    pos_pct   = mkt_value / account_value if account_value > 0 else 0

    # Institutional flow
    flow = _ife.run_institutional_flow(sym, ohlcv, qqq_ohlcv)
    acc_score = flow.get("institutional_accumulation_score", 50)
    dist_risk = flow.get("distribution_risk", 50)
    flow_dir  = flow.get("flow_direction", "NEUTRAL")

    # Signal confidence
    conf_score = _get_confidence(sym, "BUY") if _HAS_SCE else 50

    # Top-tier score (lightweight: data quality + regime as proxy)
    dq   = _dqe.run_data_quality(ohlcv)
    ttd_score = _quick_ttd_score(ohlcv, mr, flow)

    # Allocation score
    alloc = _calc_allocation_score(ttd_score, acc_score, conf_score, flow, mr)

    # Hard rule: distribution detected → trim/exit
    recommendation = "HOLD"
    main_reason    = ""
    if dist_risk > 70 or flow_dir == "DISTRIBUTION":
        recommendation = "TRIM"
        main_reason    = f"派發風險 {dist_risk}，建議減倉"
    elif dist_risk > 50:
        main_reason    = f"派發風險偏高 {dist_risk}，持續觀察"
    elif alloc >= 70 and flow_dir == "ACCUMULATION" and mr.get("market_regime") == "RISK_ON":
        recommendation = "ADD"
        main_reason    = f"機構累積訊號強（{acc_score}），可考慮加碼"
    elif alloc >= 65:
        recommendation = "HOLD"
        main_reason    = f"持倉健康，配置分數 {alloc}"
    elif alloc < 40:
        recommendation = "TRIM"
        main_reason    = f"配置分數偏低 {alloc}，建議減少敞口"
    else:
        main_reason    = f"訊號中性，配置分數 {alloc}"

    # Position size check
    max_pos = _MAX_SINGLE_PCT.get(risk_pref, 0.20)
    if pos_pct > max_pos * 1.3:
        recommendation = "TRIM"
        main_reason    = f"倉位 {pos_pct:.0%} 過高（上限 {max_pos:.0%}），需要減倉"

    return {
        "symbol":                     sym,
        "sector":                     sector,
        "shares":                     shares,
        "cost":                       cost,
        "current_price":              cur_price,
        "market_value":               round(mkt_value, 2),
        "pnl_pct":                    pnl_pct,
        "position_pct":               round(pos_pct * 100, 2),
        "allocation_score":           alloc,
        "institutional_accumulation_score": acc_score,
        "distribution_risk":          dist_risk,
        "flow_direction":             flow_dir,
        "smart_money_score":          flow.get("smart_money_score", 50),
        "liquidity_quality":          flow.get("liquidity_quality", 50),
        "top_tier_score":             ttd_score,
        "signal_confidence":          conf_score,
        "recommendation":             recommendation,
        "main_reason":                main_reason,
        "is_demo":                    flow.get("is_demo", False),
    }


def _analyse_candidate(sym, ohlcv_fn, qqq_ohlcv, mr, risk_pref, account_value) -> dict:
    """Analyse a watchlist candidate (no existing position)."""
    ohlcv    = _safe_ohlcv(ohlcv_fn, sym)
    flow     = _ife.run_institutional_flow(sym, ohlcv, qqq_ohlcv)
    dq       = _dqe.run_data_quality(ohlcv)
    ttd_score = _quick_ttd_score(ohlcv, mr, flow)
    conf_score = _get_confidence(sym, "BUY") if _HAS_SCE else 50

    acc_score = flow.get("institutional_accumulation_score", 50)
    dist_risk = flow.get("distribution_risk", 50)
    flow_dir  = flow.get("flow_direction", "NEUTRAL")
    chase     = flow.get("relative_strength_vs_QQQ") or 1.0
    alloc     = _calc_allocation_score(ttd_score, acc_score, conf_score, flow, mr)

    # Hard rule: DISTRIBUTION candidates not added
    if flow_dir == "DISTRIBUTION" or dist_risk > 70:
        recommendation = "AVOID"
        main_reason    = f"派發風險 {dist_risk}，不建議建倉"
    elif mr.get("market_regime") in ("RISK_OFF", "CRASH_RISK"):
        recommendation = "WATCH"
        main_reason    = f"市場 {mr.get('market_regime')}，暫時觀察"
    elif alloc >= 65 and flow_dir in ("ACCUMULATION", "NEUTRAL") and dq.get("can_trade_decision"):
        recommendation = "BUY"
        main_reason    = f"配置分數 {alloc}，機構累積 {acc_score}"
    elif alloc >= 50:
        recommendation = "WATCH"
        main_reason    = f"配置分數 {alloc}，等待確認訊號"
    else:
        recommendation = "AVOID"
        main_reason    = f"配置分數 {alloc} 偏低，不符合標準"

    return {
        "symbol":                     sym,
        "allocation_score":           alloc,
        "institutional_accumulation_score": acc_score,
        "distribution_risk":          dist_risk,
        "flow_direction":             flow_dir,
        "smart_money_score":          flow.get("smart_money_score", 50),
        "liquidity_quality":          flow.get("liquidity_quality", 50),
        "top_tier_score":             ttd_score,
        "signal_confidence":          conf_score,
        "recommendation":             recommendation,
        "main_reason":                main_reason,
        "is_demo":                    flow.get("is_demo", False),
    }


def _calc_allocation_score(ttd_score, acc_score, conf_score, flow, mr) -> int:
    """Weighted composite allocation score (0-100)."""
    regime      = mr.get("market_regime", "NEUTRAL")
    liq_quality = flow.get("liquidity_quality", 50)

    regime_adj = {"RISK_ON": 1.0, "NEUTRAL": 0.85, "RISK_OFF": 0.55, "CRASH_RISK": 0.25}.get(regime, 0.85)

    raw = (
        ttd_score  * 0.25 +
        acc_score  * 0.20 +
        conf_score * 0.20 +
        _sector_leadership_score(flow) * 0.15 +
        liq_quality * 0.10 +
        50 * 0.10   # risk adjustment placeholder
    )
    return max(0, min(100, round(raw * regime_adj)))


def _sector_leadership_score(flow: dict) -> float:
    """Derive sector leadership score from flow metrics."""
    rs_qqq    = flow.get("relative_strength_vs_QQQ") or 1.0
    rs_sector = flow.get("relative_strength_vs_sector") or 1.0
    base = 50.0
    base += (rs_qqq - 1.0) * 100
    base += (rs_sector - 1.0) * 50
    return max(0, min(100, base))


def _quick_ttd_score(ohlcv, mr, flow) -> int:
    """Quick top-tier proxy score from available data (avoids full engine call)."""
    closes = (ohlcv or {}).get("closes", []) or []
    n = len(closes)
    if n < 5:
        return 30

    score = 50
    # Market regime contribution
    regime_pts = {"RISK_ON": 20, "NEUTRAL": 5, "RISK_OFF": -15, "CRASH_RISK": -30}
    score += regime_pts.get(mr.get("market_regime", "NEUTRAL"), 0)

    # Momentum (20d return)
    if n >= 21 and closes[-21] > 0:
        ret_20d = (closes[-1] / closes[-21] - 1) * 100
        score += max(-15, min(15, ret_20d * 1.5))

    # Flow quality
    acc = flow.get("institutional_accumulation_score", 50)
    score += (acc - 50) * 0.3

    return max(0, min(100, round(score)))


def _build_allocation(
    holding_analyses, watchlist_analyses,
    account_value, cash_pct_now, rec_cash_pct,
    regime, risk_pref,
) -> dict:
    """Return {symbol: pct_of_portfolio} allocation dict including Cash."""
    max_pos = _MAX_SINGLE_PCT.get(risk_pref, 0.20)
    alloc: dict[str, float] = {}

    # Keep existing positions scaled to target (avoid full churn)
    equity_budget = round(1.0 - rec_cash_pct, 2)
    total_score   = sum(max(1, a.get("allocation_score", 50)) for a in holding_analyses
                        if a.get("recommendation") not in ("EXIT", "SELL", "AVOID"))

    for a in holding_analyses:
        sym = a["symbol"]
        if a.get("recommendation") in ("EXIT", "SELL", "AVOID"):
            alloc[sym] = 0
            continue
        if total_score > 0:
            raw_pct = (a.get("allocation_score", 50) / total_score) * equity_budget
        else:
            raw_pct = equity_budget / max(len(holding_analyses), 1)
        alloc[sym] = round(min(raw_pct, max_pos) * 100, 1)

    # Add top watchlist candidate if regime allows
    if regime in ("RISK_ON", "NEUTRAL") and watchlist_analyses:
        used = sum(v for v in alloc.values())
        remaining = round((equity_budget * 100) - used, 1)
        if remaining > 2:
            top = max(watchlist_analyses, key=lambda x: x.get("allocation_score", 0))
            if top.get("recommendation") == "BUY" and top.get("allocation_score", 0) >= 60:
                alloc[top["symbol"]] = round(min(remaining, max_pos * 100), 1)

    alloc["Cash"] = round(rec_cash_pct * 100, 1)
    return alloc


def _compute_sector_exposure(holding_analyses, allocation, account_value) -> dict:
    """Map sector → total pct of portfolio."""
    sector_pct: dict[str, float] = {}
    for a in holding_analyses:
        sec = a.get("sector") or "其他"
        pct = allocation.get(a["symbol"], 0)
        sector_pct[sec] = round(sector_pct.get(sec, 0) + pct, 1)
    return sector_pct


def _concentration_risk(allocation, sector_exposure, risk_pref) -> dict:
    max_sec = _MAX_SECTOR_PCT.get(risk_pref, 0.40) * 100
    warns = []
    details: dict[str, str] = {}

    # Position level
    for sym, pct in allocation.items():
        if sym == "Cash":
            continue
        max_pos_pct = _MAX_SINGLE_PCT.get(risk_pref, 0.20) * 100
        if pct > max_pos_pct:
            warns.append(f"{sym} 倉位 {pct}% 超過 {max_pos_pct:.0f}% 上限")
            details[sym] = f"OVER_LIMIT ({pct:.1f}%)"

    # Sector level
    for sec, pct in sector_exposure.items():
        if sec == "Cash":
            continue
        if pct > max_sec:
            warns.append(f"板塊「{sec}」佔 {pct}%，超過 {max_sec:.0f}% 上限")
            details[sec] = f"SECTOR_OVER ({pct:.1f}%)"

    return {
        "ok": len(warns) == 0,
        "warnings": warns,
        "details": details,
        "max_single_pct": _MAX_SINGLE_PCT.get(risk_pref, 0.20) * 100,
        "max_sector_pct": max_sec,
    }


def _estimate_risk_metrics(holding_analyses, allocation, regime) -> tuple[float, float]:
    """Estimate portfolio expected volatility and max drawdown."""
    # Regime-based baseline
    base_vol = {"RISK_ON": 12, "NEUTRAL": 18, "RISK_OFF": 25, "CRASH_RISK": 40}.get(regime, 18)
    equity_pct = sum(v for k, v in allocation.items() if k != "Cash") / 100.0

    # Average distribution risk of holdings (higher dist risk = more vol)
    dist_avg = 50.0
    if holding_analyses:
        dist_avg = sum(a.get("distribution_risk", 50) for a in holding_analyses) / len(holding_analyses)

    vol_adj = base_vol * equity_pct * (1 + (dist_avg - 50) / 200)
    max_dd  = vol_adj * 2.0  # rough estimate: 2x annualized vol as peak drawdown
    return round(vol_adj, 1), round(min(max_dd, 80), 1)


def _portfolio_score(mr, holding_analyses, regime, risk_pref) -> int:
    regime_score = mr.get("market_score", 50)
    if not holding_analyses:
        return max(0, min(100, round(regime_score * 0.6 + 20)))
    avg_alloc = sum(a.get("allocation_score", 50) for a in holding_analyses) / len(holding_analyses)
    raw = regime_score * 0.40 + avg_alloc * 0.60
    return max(0, min(100, round(raw)))


def _get_confidence(sym: str, decision: str) -> int:
    if not _HAS_SCE:
        return 50
    try:
        stats = _sce.get_confidence_stats(decision)
        return int(stats.get("confidence_score", 50)) if isinstance(stats, dict) else 50
    except Exception:
        return 50


def _safe_ohlcv(fn, sym: str) -> dict:
    try:
        r = fn(str(sym))
        return r if r and isinstance(r, dict) else {}
    except Exception:
        return {}


def _last_close(ohlcv: dict) -> float | None:
    closes = (ohlcv or {}).get("closes") or []
    return float(closes[-1]) if closes else None
