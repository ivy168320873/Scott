"""
Portfolio Optimizer Engine — Phase 14
Upgrades from stock recommendation to optimal portfolio allocation.

Public API
----------
run_portfolio_optimize(
    account_value, current_cash, holdings, watchlist,
    risk_profile, ohlcv_fn
) -> dict
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

import market_regime_engine      as _mre
import data_quality_engine       as _dqe
import institutional_flow_engine as _ife

try:
    import macro_risk_engine as _mre14
    _HAS_MACRO = True
except ImportError:
    _HAS_MACRO = False

try:
    import top_tier_decision_engine as _ttde
    _HAS_TTDE = True
except ImportError:
    _HAS_TTDE = False

try:
    import signal_confidence_engine as _sce
    _HAS_SCE = True
except ImportError:
    _HAS_SCE = False

try:
    from sector_map import SYMBOL_TO_SECTOR as _SYM_SECTOR
    _HAS_SECTOR_MAP = True
except ImportError:
    _SYM_SECTOR: dict = {}
    _HAS_SECTOR_MAP = False

_DISCLAIMER = "此為決策輔助系統，不代表自動下單，不構成投資建議。操作前請自行評估風險。"

_MAX_SINGLE: dict[str, float] = {
    "conservative": 0.15,
    "balanced":     0.20,
    "aggressive":   0.25,
}
_MAX_SECTOR: dict[str, float] = {
    "conservative": 0.30,
    "balanced":     0.40,
    "aggressive":   0.50,
}

# Cash target (min, max) by macro/market regime
_CASH_TARGETS: dict[str, tuple[float, float]] = {
    "EXPANSION":  (0.05, 0.15),
    "RISK_ON":    (0.05, 0.15),
    "NEUTRAL":    (0.15, 0.30),
    "TIGHTENING": (0.40, 0.70),
    "RISK_OFF":   (0.80, 1.00),
    "CRASH_RISK": (0.80, 1.00),
}

# Base annualised return estimate by portfolio score range
_RETURN_BY_SCORE = [
    (80, 0.22),
    (65, 0.16),
    (50, 0.10),
    (35, 0.04),
    (0,  0.00),
]

# Base annual volatility estimate by regime
_VOL_BY_REGIME = {
    "EXPANSION":  0.14,
    "RISK_ON":    0.14,
    "NEUTRAL":    0.18,
    "TIGHTENING": 0.24,
    "RISK_OFF":   0.32,
    "CRASH_RISK": 0.40,
}

_MAX_SYMBOLS = 15   # safety cap: holdings + watchlist combined
_BENCH_SYM   = "QQQ"


# ── Internal helpers ──────────────────────────────────────────────────────────

def _analyze_symbol(
    sym: str,
    ohlcv_fn,
    is_existing: bool,
    bench_ohlcv: dict | None,
) -> dict:
    """Run all engines for one symbol; return a standardised analysis dict."""
    ohlcv = ohlcv_fn(sym)
    is_demo = (ohlcv or {}).get("is_demo", True)

    ttd: dict = {}
    if _HAS_TTDE:
        try:
            sector_name = _SYM_SECTOR.get(sym.upper(), "")
            ttd = _ttde.run_top_tier_decision(
                sym, ohlcv_fn, sector_name=sector_name
            )
        except Exception:
            pass

    flow: dict = {}
    try:
        flow = _ife.run_institutional_flow(sym, ohlcv, bench_ohlcv)
    except Exception:
        pass

    decision = ttd.get("decision", "HOLD") or "HOLD"
    regime   = ttd.get("market_regime", "NEUTRAL") or "NEUTRAL"

    cal: dict = {}
    if _HAS_SCE:
        try:
            cal = _sce.get_calibration_override(decision, regime)
        except Exception:
            pass

    return {
        "symbol":             sym,
        "is_existing":        is_existing,
        "decision":           decision,
        "ttd_score":          int(ttd.get("top_tier_score") or 50),
        "market_regime":      regime,
        "market_score":       ttd.get("market_score"),
        "risk_budget_mult":   ttd.get("risk_budget_mult", 0.6),
        "data_quality_status": ttd.get("data_quality_status", "FAIR") or "FAIR",
        "chase_risk_score":   int(ttd.get("chase_risk_score") or 0),
        "kill_signal":        bool(ttd.get("kill_signal_triggered", False)),
        "sector":             ttd.get("sector_leadership", {}).get("sector_name")
                              or _SYM_SECTOR.get(sym.upper(), "其他"),
        "flow_direction":     flow.get("flow_direction", "NEUTRAL"),
        "inst_acc_score":     int(flow.get("institutional_accumulation_score") or 50),
        "distribution_risk":  int(flow.get("distribution_risk") or 0),
        "is_demo":            is_demo,
        "cal_recommendation": cal.get("recommendation", ""),
        "cal_confidence":     cal.get("confidence_score"),
        "ttd_ok":             bool(ttd),
    }


def _passes_hard_gates(a: dict, is_new: bool) -> tuple[bool, str]:
    if a["cal_recommendation"] == "DISABLE":
        return False, "訊號校準已停用此決策類型"
    if a["kill_signal"]:
        return False, "Kill Signal 觸發，禁止交易"
    if is_new:
        if a["decision"] in ("SELL", "AVOID"):
            return False, f"決策為 {a['decision']}，不宜新建倉位"
        if a["flow_direction"] == "DISTRIBUTION":
            return False, "法人出貨中，不宜新建"
        if a["chase_risk_score"] > 90:
            return False, f"追高風險過高（{a['chase_risk_score']}），排除新建"
        if a["data_quality_status"] == "BAD":
            return False, "資料品質不足，排除"
    return True, ""


def _allocation_score(a: dict) -> float:
    ttd_s   = float(a["ttd_score"])
    inst_s  = float(a["inst_acc_score"])
    conf_s  = float(a.get("cal_confidence") or 50)
    anti_ch = float(100 - a["chase_risk_score"])

    raw = (
        ttd_s   * 0.35 +
        inst_s  * 0.30 +
        conf_s  * 0.15 +
        anti_ch * 0.20
    )

    if a["chase_risk_score"] > 80:
        raw *= 0.70
    if (a.get("cal_confidence") or 50) < 50:
        raw *= 0.80
    if a["distribution_risk"] > 70:
        raw *= 0.50

    return max(0.0, min(100.0, raw))


def _compute_optimal_weights(
    candidates: list[dict],
    equity_budget: float,
    max_single: float,
    max_sector: float,
) -> dict[str, float]:
    """
    Greedy proportional allocation with single-position and sector caps.
    Returns {symbol: fraction_of_portfolio} where values sum ≤ equity_budget.
    """
    if not candidates:
        return {}

    scored = [(c["symbol"], c.get("alloc_score", 50.0), c.get("sector", "其他"))
              for c in candidates]
    scored.sort(key=lambda x: x[1], reverse=True)

    # Initial proportional weights
    total_score = sum(max(1.0, s) for _, s, _ in scored)
    weights: dict[str, float] = {
        sym: (max(1.0, s) / total_score) * equity_budget
        for sym, s, _ in scored
    }

    # Enforce single-position cap (iterate until stable)
    for _ in range(10):
        excess = 0.0
        uncapped: list[str] = []
        for sym, w in list(weights.items()):
            if w > max_single:
                excess += w - max_single
                weights[sym] = max_single
            else:
                uncapped.append(sym)
        if excess < 1e-6 or not uncapped:
            break
        unc_score = sum(max(1.0, s) for sym, s, _ in scored if sym in uncapped)
        if unc_score > 0:
            for sym, s, _ in scored:
                if sym in uncapped:
                    weights[sym] = min(
                        max_single,
                        weights[sym] + excess * (max(1.0, s) / unc_score),
                    )

    # Enforce sector cap (iterate until stable)
    for _ in range(10):
        sector_totals: dict[str, float] = {}
        for sym, w in weights.items():
            sec = next((s for x, _, s in scored if x == sym), "其他")
            sector_totals[sec] = sector_totals.get(sec, 0.0) + w

        changed = False
        for sec, total in sector_totals.items():
            if total > max_sector + 1e-6:
                scale = max_sector / total
                for sym, w in list(weights.items()):
                    sym_sec = next((s for x, _, s in scored if x == sym), "其他")
                    if sym_sec == sec:
                        weights[sym] = w * scale
                changed = True
                break
        if not changed:
            break

    return weights


def _portfolio_metrics(
    weights: dict[str, float],
    analyzed: list[dict],
    macro_regime: str,
    cash_pct: float,
) -> tuple[int, float, float, float]:
    """
    Returns (portfolio_score, expected_return_pct, expected_vol_pct, est_max_drawdown_pct).
    All return values are percentages (e.g. 15.0 means 15%).
    """
    by_sym = {a["symbol"]: a for a in analyzed}

    # Portfolio score = weighted average TTD score
    total_weight = sum(weights.values())
    if total_weight > 0:
        wtd_score = sum(
            weights.get(sym, 0) * by_sym.get(sym, {}).get("ttd_score", 50)
            for sym in weights
        ) / total_weight
    else:
        wtd_score = 50.0
    portfolio_score = int(round(min(100, max(0, wtd_score))))

    # Regime adjustment factor
    regime_factor = {
        "EXPANSION": 1.0, "RISK_ON": 1.0,
        "NEUTRAL": 0.65,
        "TIGHTENING": 0.35,
        "RISK_OFF": 0.15, "CRASH_RISK": 0.10,
    }.get(macro_regime, 0.65)

    # Expected return from score lookup
    base_return = 0.0
    for threshold, ret in _RETURN_BY_SCORE:
        if portfolio_score >= threshold:
            base_return = ret
            break
    expected_return = round(base_return * regime_factor * (1 - cash_pct) * 100, 1)

    # Volatility estimate
    base_vol = _VOL_BY_REGIME.get(macro_regime, 0.18)
    n = max(1, len(weights))
    diversification = 1.0 - min(0.40, 0.08 * (n - 1))
    expected_vol = round(base_vol * diversification * (1 - cash_pct) * 100, 1)

    # Max drawdown estimate: roughly 2.5× annual vol for equities
    est_max_drawdown = round(expected_vol * 2.5, 1)

    return portfolio_score, expected_return, expected_vol, est_max_drawdown


# ── Public API ────────────────────────────────────────────────────────────────

def run_portfolio_optimize(
    account_value: float = 0.0,
    current_cash: float = 0.0,
    holdings: list[dict] | None = None,
    watchlist: list[str] | None = None,
    risk_profile: str = "balanced",
    ohlcv_fn=None,
) -> dict:
    """
    Compute optimal portfolio allocation.

    Parameters
    ----------
    account_value : total portfolio value (cash + equity)
    current_cash  : current cash on hand
    holdings      : [{symbol, shares, cost, sector?}, ...]
    watchlist     : [symbol, ...]
    risk_profile  : "conservative" | "balanced" | "aggressive"
    ohlcv_fn      : callable(symbol) -> ohlcv_dict

    Returns
    -------
    dict with portfolio_score, optimal_weights, rebalance_suggestions, etc.
    """
    holdings  = holdings  or []
    watchlist = watchlist or []
    profile   = risk_profile.lower().strip() if risk_profile else "balanced"
    if profile not in _MAX_SINGLE:
        profile = "balanced"

    max_single = _MAX_SINGLE[profile]
    max_sector = _MAX_SECTOR[profile]
    generated_at = datetime.now(timezone.utc).isoformat()

    # ── 1. Macro / market regime ──────────────────────────────────────────────
    macro_result: dict = {}
    if _HAS_MACRO and ohlcv_fn:
        try:
            macro_result = _mre14.run_macro_risk(ohlcv_fn)
        except Exception:
            pass

    macro_regime = macro_result.get("macro_regime") or "NEUTRAL"
    macro_score  = macro_result.get("macro_score")

    # If macro engine unavailable, fall back to market regime engine
    if not macro_result and ohlcv_fn:
        try:
            spy_ohlcv = ohlcv_fn("SPY")
            mr = _mre.run_market_regime(spy_ohlcv)
            macro_regime = mr.get("regime", "NEUTRAL")
        except Exception:
            pass

    cash_range    = _CASH_TARGETS.get(macro_regime, (0.15, 0.30))
    target_cash   = (cash_range[0] + cash_range[1]) / 2  # midpoint
    equity_budget = 1.0 - target_cash

    # ── 2. Collect symbols to analyse ────────────────────────────────────────
    existing_syms: list[str] = [
        h["symbol"].upper().strip() for h in holdings if h.get("symbol")
    ]
    wl_syms: list[str] = [
        str(s).upper().strip() for s in watchlist if s
    ]
    # Deduplicate, preserve order, cap total
    seen: set[str] = set()
    all_syms: list[str] = []
    for sym in existing_syms + wl_syms:
        if sym and sym not in seen:
            seen.add(sym)
            all_syms.append(sym)
    all_syms = all_syms[:_MAX_SYMBOLS]

    # ── 3. Fetch benchmark OHLCV once ─────────────────────────────────────────
    bench_ohlcv: dict | None = None
    if ohlcv_fn:
        try:
            bench_ohlcv = ohlcv_fn(_BENCH_SYM)
        except Exception:
            pass

    # ── 4. Analyse each symbol ────────────────────────────────────────────────
    analyzed: list[dict] = []
    any_demo = False
    for sym in all_syms:
        try:
            is_existing = sym in existing_syms
            a = _analyze_symbol(sym, ohlcv_fn, is_existing, bench_ohlcv)
            any_demo = any_demo or a["is_demo"]
            a["alloc_score"] = _allocation_score(a)
            analyzed.append(a)
        except Exception:
            analyzed.append({
                "symbol": sym, "is_existing": sym in existing_syms,
                "decision": "HOLD", "ttd_score": 50, "market_regime": "NEUTRAL",
                "data_quality_status": "FAIR", "chase_risk_score": 0,
                "kill_signal": False, "sector": "其他",
                "flow_direction": "NEUTRAL", "inst_acc_score": 50,
                "distribution_risk": 0, "is_demo": True,
                "cal_recommendation": "", "cal_confidence": None,
                "alloc_score": 50.0, "ttd_ok": False,
            })

    # ── 5. Apply hard gates ───────────────────────────────────────────────────
    eligible: list[dict] = []
    blocked:  list[dict] = []

    for a in analyzed:
        is_new = not a["is_existing"]
        ok, reason = _passes_hard_gates(a, is_new)
        if ok:
            eligible.append(a)
        else:
            blocked.append({**a, "blocked_reason": reason})

    # ── 6. Compute optimal weights ────────────────────────────────────────────
    raw_weights = _compute_optimal_weights(eligible, equity_budget, max_single, max_sector)

    # Round to 4dp and express as percentage
    optimal_weights_pct: dict[str, float] = {
        sym: round(w * 100, 2) for sym, w in raw_weights.items()
    }
    recommended_cash_pct = round(target_cash * 100, 1)

    # ── 7. Sector exposure ────────────────────────────────────────────────────
    sector_exposure: dict[str, float] = {}
    for sym, wpct in optimal_weights_pct.items():
        sec = next((a["sector"] for a in analyzed if a["symbol"] == sym), "其他")
        sector_exposure[sec] = round(sector_exposure.get(sec, 0.0) + wpct, 2)

    # ── 8. Compute current weights (from holdings) ────────────────────────────
    current_weights_pct: dict[str, float] = {}
    if account_value > 0:
        for h in holdings:
            sym   = (h.get("symbol") or "").upper().strip()
            shares = float(h.get("shares") or 0)
            # Use cost as proxy when live price unavailable
            cost  = float(h.get("cost") or h.get("price") or 0)
            value = shares * cost
            if sym and value > 0:
                current_weights_pct[sym] = round(value / account_value * 100, 2)
        if current_cash > 0 and account_value > 0:
            current_weights_pct["CASH"] = round(current_cash / account_value * 100, 2)

    # ── 9. Rebalance suggestions ──────────────────────────────────────────────
    positions_to_add:  list[str] = []
    positions_to_trim: list[str] = []
    positions_to_exit: list[str] = []
    rebalance_suggestions: list[dict] = []
    risk_notes: list[str] = []

    by_sym = {a["symbol"]: a for a in analyzed}

    # Exits: blocked existing holdings
    for b in blocked:
        if b["is_existing"]:
            positions_to_exit.append(b["symbol"])
            rebalance_suggestions.append({
                "action":  "EXIT",
                "symbol":  b["symbol"],
                "reason":  b["blocked_reason"],
                "urgency": "HIGH",
                "target_pct": 0.0,
                "current_pct": current_weights_pct.get(b["symbol"], 0.0),
            })

    # Existing holdings: compare optimal vs current
    for a in eligible:
        if not a["is_existing"]:
            continue
        sym = a["symbol"]
        opt = optimal_weights_pct.get(sym, 0.0)
        cur = current_weights_pct.get(sym, 0.0)
        diff = opt - cur

        # Force trim if over position limit or distribution risk
        force_trim = (
            a["distribution_risk"] > 70
            or a["decision"] in ("TRIM", "SELL")
            or a["flow_direction"] == "DISTRIBUTION"
        )
        if force_trim and sym not in positions_to_exit:
            positions_to_trim.append(sym)
            rebalance_suggestions.append({
                "action":  "TRIM",
                "symbol":  sym,
                "reason":  f"{a['decision']} + 出貨風險 {a['distribution_risk']}",
                "urgency": "HIGH" if a["distribution_risk"] > 80 else "MEDIUM",
                "target_pct": opt,
                "current_pct": cur,
            })
        elif diff < -3.0 and cur > 0:
            positions_to_trim.append(sym)
            rebalance_suggestions.append({
                "action":  "TRIM",
                "symbol":  sym,
                "reason":  f"目前 {cur}% 超出最佳比重 {opt}%",
                "urgency": "LOW",
                "target_pct": opt,
                "current_pct": cur,
            })
        elif diff > 3.0:
            rebalance_suggestions.append({
                "action":  "ADD",
                "symbol":  sym,
                "reason":  f"可加碼至 {opt}%（{a['decision']}，機構評分 {a['inst_acc_score']}）",
                "urgency": "LOW",
                "target_pct": opt,
                "current_pct": cur,
            })

    # New candidates from watchlist
    for a in eligible:
        if a["is_existing"]:
            continue
        sym = a["symbol"]
        opt = optimal_weights_pct.get(sym, 0.0)
        if opt >= 1.0:
            positions_to_add.append(sym)
            rebalance_suggestions.append({
                "action":  "ADD",
                "symbol":  sym,
                "reason":  f"新建倉位 {opt}%（{a['decision']}，機構評分 {a['inst_acc_score']}）",
                "urgency": "MEDIUM" if a["decision"] == "STRONG_BUY" else "LOW",
                "target_pct": opt,
                "current_pct": 0.0,
            })

    # ── 10. Risk notes ────────────────────────────────────────────────────────
    high_chase = [a for a in analyzed if a["chase_risk_score"] > 80]
    if high_chase:
        risk_notes.append(
            f"{len(high_chase)} 個倉位追高風險 >80，已自動降低配比：" +
            ", ".join(a["symbol"] for a in high_chase)
        )

    low_conf = [a for a in analyzed
                if (a.get("cal_confidence") or 50) < 50 and a["symbol"] in optimal_weights_pct]
    if low_conf:
        risk_notes.append(
            f"{len(low_conf)} 個倉位訊號信心 <50，配比已降低：" +
            ", ".join(a["symbol"] for a in low_conf)
        )

    if macro_regime in ("TIGHTENING", "RISK_OFF", "CRASH_RISK"):
        risk_notes.append(f"總體宏觀環境 {macro_regime}，建議持有現金比例 {recommended_cash_pct}%")

    if any_demo:
        risk_notes.append("部分資料使用模擬數值，請勿用於實際交易決策")

    dist_holdings = [a for a in analyzed if a["is_existing"] and a["flow_direction"] == "DISTRIBUTION"]
    if dist_holdings:
        risk_notes.append(
            "現有持倉偵測到法人出貨：" + ", ".join(a["symbol"] for a in dist_holdings)
        )

    # Sector concentration warnings
    for sec, pct in sector_exposure.items():
        if pct > max_sector * 100 * 0.9:
            risk_notes.append(f"板塊 {sec} 集中度 {pct}%，接近上限")

    # ── 11. Portfolio-level metrics ───────────────────────────────────────────
    portfolio_score, expected_return, expected_vol, est_max_drawdown = _portfolio_metrics(
        raw_weights, analyzed, macro_regime, target_cash
    )

    # ── 12. Positions detail ──────────────────────────────────────────────────
    positions_detail: list[dict] = []
    for a in analyzed:
        sym = a["symbol"]
        positions_detail.append({
            "symbol":          sym,
            "decision":        a["decision"],
            "ttd_score":       a["ttd_score"],
            "flow_direction":  a["flow_direction"],
            "inst_acc_score":  a["inst_acc_score"],
            "chase_risk_score": a["chase_risk_score"],
            "sector":          a["sector"],
            "is_existing":     a["is_existing"],
            "is_demo":         a["is_demo"],
            "alloc_score":     round(a.get("alloc_score", 50.0), 1),
            "optimal_pct":     optimal_weights_pct.get(sym, 0.0),
            "current_pct":     current_weights_pct.get(sym, 0.0),
            "cal_confidence":  a.get("cal_confidence"),
        })

    # Sort rebalance suggestions: EXIT first, then HIGH urgency, then by action
    _urgency_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    _action_order  = {"EXIT": 0, "TRIM": 1, "ADD": 2}
    rebalance_suggestions.sort(
        key=lambda x: (_urgency_order.get(x.get("urgency", "LOW"), 2),
                       _action_order.get(x.get("action", "ADD"), 2))
    )

    return {
        "ok":                     True,
        "portfolio_score":        portfolio_score,
        "expected_return_pct":    expected_return,
        "expected_volatility_pct": expected_vol,
        "estimated_max_drawdown_pct": est_max_drawdown,
        "recommended_cash_pct":   recommended_cash_pct,
        "optimal_weights":        optimal_weights_pct,
        "sector_exposure":        sector_exposure,
        "risk_notes":             risk_notes,
        "rebalance_suggestions":  rebalance_suggestions,
        "positions_to_add":       positions_to_add,
        "positions_to_trim":      positions_to_trim,
        "positions_to_exit":      positions_to_exit,
        "positions_detail":       positions_detail,
        "macro_regime":           macro_regime,
        "macro_score":            macro_score,
        "risk_profile":           profile,
        "symbols_analyzed":       len(analyzed),
        "symbols_eligible":       len(eligible),
        "symbols_blocked":        len(blocked),
        "blocked_detail":         [{"symbol": b["symbol"], "reason": b["blocked_reason"]} for b in blocked],
        "is_demo":                any_demo,
        "disclaimer":             _DISCLAIMER,
        "generated_at":           generated_at,
    }
