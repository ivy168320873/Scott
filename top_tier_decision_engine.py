"""
Top-Tier Decision Engine — Phase 13B
Final decision layer integrating all analysis modules.

Public API
----------
run_top_tier_decision(symbol, ohlcv_fn, **opts) -> dict

Decision hierarchy (strong → weak):
  STRONG_BUY → BUY → WATCH → HOLD → TRIM → SELL → AVOID

Decision levels:
  A+  top_tier_score > 85 + RISK_ON + low chase risk + data OK
  A   top_tier_score 70-85, most conditions met
  B   top_tier_score 50-70, mixed signals
  C   top_tier_score 35-50, hold / trim territory
  D   top_tier_score < 35 OR hard kill signal

Position sizes:
  NO_TRADE   — do not enter
  TINY       — 1-2% (trial / speculative)
  SMALL      — 3-5%
  NORMAL     — 5-10%
  AGGRESSIVE — 10%+ (A+ conditions only)
"""
from __future__ import annotations

from datetime import datetime, timezone, timedelta

import market_regime_engine   as _mre
import data_quality_engine    as _dqe
import position_sizing_engine as _pse
from risk_engine   import calc_chase_risk
from sell_engine   import calc_sell_decision
from sector_engine import calc_sector_leadership

_DISCLAIMER = "此為決策輔助系統，不代表自動下單，不構成投資建議。操作前請自行評估風險。"


# ── Public API ────────────────────────────────────────────────────────────────

def run_top_tier_decision(
    symbol: str,
    ohlcv_fn,
    *,
    cost: float = 0.0,
    holding_days: int = 0,
    sector_name: str = "",
    sector_stocks: dict | None = None,      # {sym: ohlcv_norm} for sector scoring
    watchlist_ohlcv: dict | None = None,    # {sym: ohlcv_norm} for alternatives
    stop_pct: float = 8.0,
    trail_pct: float = 15.0,
    profit_target_pct: float = 20.0,
) -> dict:
    """
    Full top-tier decision for one symbol.

    ohlcv_fn : callable(symbol: str) -> normalized OHLCV dict
    cost     : average cost basis (0 = new position analysis)
    """
    symbol = str(symbol or "").upper().strip()
    must_not_buy: list[str] = []
    risk_controls: list[str] = []

    # ── Step 1: Fetch and validate data ───────────────────────────────────────
    ohlcv = _safe_fetch(ohlcv_fn, symbol)
    dq    = _dqe.run_data_quality(ohlcv)

    # ── Step 2: Market regime ─────────────────────────────────────────────────
    mr = _mre.run_market_regime(ohlcv_fn)
    regime = mr["market_regime"]

    # ── Step 3: Chase risk ────────────────────────────────────────────────────
    cr = calc_chase_risk(ohlcv) if dq["bar_count"] >= 20 else _empty_cr()
    chase_score = cr.get("score") or 50

    # ── Step 4: Sell decision (only meaningful if we have a cost basis) ───────
    sd = None
    if cost > 0 and dq["bar_count"] >= 5:
        sd = calc_sell_decision(
            ohlcv,
            cost=cost,
            holding_days=holding_days,
            stop_pct=stop_pct,
            trail_pct=trail_pct,
            profit_target_pct=profit_target_pct,
        )

    # ── Step 5: Sector leadership (optional) ─────────────────────────────────
    sl = None
    if sector_stocks and len(sector_stocks) >= 2:
        try:
            sl = calc_sector_leadership(sector_name or symbol, sector_stocks)
        except Exception:
            pass

    # ── Step 6: Kill signal (rule-based, no AI) ───────────────────────────────
    kill = _evaluate_kill_signal(ohlcv, sd)

    # ── Step 7: Alternative candidates ───────────────────────────────────────
    alternatives = _score_alternatives(symbol, ohlcv, watchlist_ohlcv or {})

    # ── Step 8: Compute composite score ──────────────────────────────────────
    score_breakdown: dict[str, float] = {}

    # Market regime (25%)
    mr_s = mr.get("market_score", 50)
    score_breakdown["market_regime"] = mr_s * 0.25

    # Data quality (15%)
    dq_s = dq.get("data_quality_score", 50)
    score_breakdown["data_quality"] = dq_s * 0.15

    # Chase risk inverted (20%) — low chase = good
    cr_s = max(0, 100 - chase_score)
    score_breakdown["chase_risk_inv"] = cr_s * 0.20

    # Momentum proxy (20%) — computed from OHLCV
    mom_s = _momentum_score(ohlcv)
    score_breakdown["momentum"] = mom_s * 0.20

    # Sell signal health (10%)
    sell_s = _sell_health(sd)
    score_breakdown["sell_health"] = sell_s * 0.10

    # Sector leadership (10%)
    sl_s = _sector_score(sl)
    score_breakdown["sector"] = sl_s * 0.10

    top_tier_score = round(sum(score_breakdown.values()))

    # ── Step 9: Apply hard rules ──────────────────────────────────────────────

    # Hard Rule 2: BAD data → AVOID
    if dq["data_status"] == "BAD":
        must_not_buy.append("資料品質 BAD：資料不可靠，拒絕所有買進決策")

    # Hard Rule 1: Demo data → max WATCH
    if dq["is_demo"]:
        must_not_buy.append("Demo 資料環境：最高等級為 WATCH，不允許 BUY / STRONG_BUY")

    # Hard Rule 3: RISK_OFF → no BUY/STRONG_BUY
    if regime == "RISK_OFF":
        must_not_buy.append("市場狀態 RISK_OFF：SPY 跌破 MA200，禁止主動買進")

    # Hard Rule 4: CRASH_RISK → only WATCH/TRIM/SELL/AVOID
    if regime == "CRASH_RISK":
        must_not_buy.append("市場狀態 CRASH_RISK：崩跌風險模式，禁止一切新倉")
        risk_controls.append("立即檢查現有持倉停損設定")
        risk_controls.append("持倉比例降至 20% 以下")

    # Hard Rule 5: Chase Risk > 80 → no STRONG_BUY
    if chase_score > 80:
        must_not_buy.append(f"追高風險 {chase_score}（>80）：禁止 STRONG_BUY")

    # Hard Rule 6: Chase Risk > 90 → max WATCH
    if chase_score > 90:
        must_not_buy.append(f"追高風險 {chase_score}（>90）：最高等級降為 WATCH")

    # Hard Rule 7: STOP_LOSS sell signal → SELL
    force_sell = False
    if sd and sd.get("level") == "STOP_LOSS":
        force_sell = True
        risk_controls.append(f"停損觸發：{sd.get('reasons', ['停損條件成立'])[0]}")

    # Hard Rule 8: Kill signal → SELL or TRIM
    force_trim = False
    if kill.get("triggered"):
        force_trim = True
        risk_controls.append(f"Kill Signal: {kill['primary']}")

    # Hard Rule 9: Sector LAGGING → downgrade BUY
    sector_lagging = sl and sl.get("level") in ("LAGGING", "WEAKENING")
    if sector_lagging:
        must_not_buy.append(f"板塊狀態 {sl.get('level', 'LAGGING')}：個股 BUY 訊號降級")

    # Hard Rule 10: Capital efficiency poor → ROTATE/TRIM
    if sd and sd.get("level") == "ROTATE":
        risk_controls.append("資金效率差：建議換股，將資金轉向強勢標的")

    # ── Step 10: Determine decision ───────────────────────────────────────────
    decision, level, dcolor = _decide(
        top_tier_score  = top_tier_score,
        regime          = regime,
        chase_score     = chase_score,
        dq_status       = dq["data_status"],
        is_demo         = dq["is_demo"],
        force_sell      = force_sell,
        force_trim      = force_trim,
        must_not_buy    = must_not_buy,
        sd_level        = sd.get("level") if sd else "NONE",
        sector_lagging  = sector_lagging,
        alternatives_count = len(alternatives),
    )

    # Hard Rule 12: better alternatives → suggest ROTATE
    if alternatives and decision in ("WATCH", "HOLD", "TRIM"):
        risk_controls.append(
            f"有 {len(alternatives)} 個替代標的動能更強，可考慮 ROTATE：" +
            ", ".join(a["symbol"] for a in alternatives[:3])
        )

    # Hard Rule 11: STRONG_BUY conditions
    market_permission = (
        regime == "RISK_ON"
        and dq["data_status"] in ("OK",)
        and not dq["is_demo"]
        and chase_score < 50
        and top_tier_score > 85
        and not must_not_buy
    )

    # Position sizing (Phase 12B)
    pos_result = _pse.run_position_sizing({
        "decision":          decision,
        "top_tier_score":    top_tier_score,
        "market_regime":     regime,
        "risk_budget_mult":  mr.get("risk_budget_multiplier", 1.0),
        "chase_risk_score":  chase_score,
        "ohlcv":             ohlcv,
        "win_rate_estimate": 0.60 if decision == "STRONG_BUY" else 0.55,
        "reward_risk_ratio": 2.5  if decision == "STRONG_BUY" else 2.0,
    })
    pos_level = pos_result.get("position_size_level", "NO_TRADE")

    # Next check time
    next_check = _next_check_time(decision)

    # Bull / bear case
    bull_case, bear_case = _build_cases(ohlcv, cr, sd, sl, mr)

    # Score breakdown for UI compat
    dimensions_ui = _build_dimensions_ui(
        mr, dq, cr, sd, sl, chase_score, top_tier_score, decision, level
    )

    return {
        "ok":              True,
        # ── New comprehensive fields ──────────────────────────────────────
        "symbol":              symbol,
        "top_tier_score":      top_tier_score,
        "decision":            decision,
        "decision_level":      level,
        "market_regime":       regime,
        "market_score":        mr.get("market_score"),
        "market_permission":   market_permission,
        "position_size_level": pos_level,
        "position_sizing":     pos_result,
        "main_reason":         _main_reason(decision, level, top_tier_score, symbol),
        "bull_case":           bull_case,
        "bear_case":           bear_case,
        "kill_signal":         kill,
        "must_not_buy_reasons": must_not_buy,
        "risk_controls":       risk_controls,
        "alternative_candidates": alternatives,
        "score_breakdown":     score_breakdown,
        "next_check_time":     next_check,
        "disclaimer":          _DISCLAIMER,
        # ── Backward-compat UI fields (used by _renderTTD in index.html) ──
        "action_level":    _level_to_action(level, decision),
        "action_label":    _decision_label(decision),
        "action_color":    dcolor,
        "composite_score": top_tier_score,
        "dimensions":      dimensions_ui,
        "summary":         _main_reason(decision, level, top_tier_score, symbol),
        # Sub-engine raw results
        "data_quality":    dq,
        "market_regime_detail": mr,
        "chase_risk":      cr,
        "sell_decision":   sd,
        "sector_leadership": sl,
    }


# ── Decision logic ────────────────────────────────────────────────────────────

def _decide(
    top_tier_score, regime, chase_score, dq_status, is_demo,
    force_sell, force_trim, must_not_buy, sd_level, sector_lagging,
    alternatives_count,
) -> tuple[str, str, str]:
    """Returns (decision, level, color)."""

    # Hard overrides (highest priority)
    if dq_status == "BAD":
        return "AVOID", "D", "#8b949e"

    if force_sell or sd_level == "STOP_LOSS":
        return "SELL", "D", "#f85149"

    if regime == "CRASH_RISK":
        if force_trim:
            return "SELL", "D", "#f85149"
        return "WATCH", "C", "#e3b341"

    if force_trim and sd_level in ("SELL", "STOP_LOSS"):
        return "SELL", "D", "#f85149"
    elif force_trim:
        return "TRIM", "C", "#f0883e"

    # Demo cap
    if is_demo:
        return "WATCH", "C", "#e3b341"

    # RISK_OFF: max WATCH
    if regime == "RISK_OFF":
        if top_tier_score < 30:
            return "AVOID", "D", "#8b949e"
        return "WATCH", "C", "#e3b341"

    # Hard rule: chase > 90 → max WATCH
    if chase_score > 90:
        return "WATCH", "B", "#e3b341"

    # Must-not-buy blocks BUY/STRONG_BUY
    blocked_buy = bool(must_not_buy)

    # Score-based decision
    if top_tier_score >= 85 and not blocked_buy and regime == "RISK_ON" and chase_score < 50:
        return "STRONG_BUY", "A+", "#58a6ff"

    if top_tier_score >= 70 and not blocked_buy and regime in ("RISK_ON", "NEUTRAL"):
        if chase_score > 80:
            return "WATCH", "B", "#e3b341"
        if sector_lagging:
            return "WATCH", "B", "#e3b341"
        return "BUY", "A", "#3fb950"

    if top_tier_score >= 55 and not blocked_buy:
        if chase_score > 75 or sector_lagging:
            return "WATCH", "B", "#e3b341"
        return "BUY" if regime == "RISK_ON" else "WATCH", "B", "#3fb950" if regime == "RISK_ON" else "#e3b341"

    if top_tier_score >= 45:
        if sd_level in ("TRIM", "ROTATE"):
            return "TRIM", "C", "#f0883e"
        return "WATCH", "C", "#e3b341"

    if top_tier_score >= 30:
        if sd_level in ("SELL", "STOP_LOSS", "TRIM"):
            return "TRIM", "C", "#f0883e"
        return "HOLD", "C", "#e3b341"

    return "AVOID", "D", "#8b949e"


# ── Scoring helpers ───────────────────────────────────────────────────────────

def _safe_fetch(fn, symbol: str) -> dict:
    try:
        r = fn(symbol)
        return r if r and isinstance(r, dict) else {}
    except Exception:
        return {}


def _empty_cr() -> dict:
    return {
        "ok": False, "score": 50, "level": "UNKNOWN",
        "level_label": "資料不足", "level_color": "#8b949e",
        "reasons": ["K線資料不足"], "suggested_action": "",
        "warning_flags": [], "risk_level": "UNKNOWN",
        "detail": {},
    }


def _sma(lst: list, n: int) -> float | None:
    valid = [v for v in lst[-n:] if v and v > 0]
    return sum(valid) / len(valid) if len(valid) >= n else None


def _momentum_score(ohlcv: dict) -> float:
    closes  = ohlcv.get("closes", [])
    volumes = ohlcv.get("volumes", [])
    n = len(closes)
    if n < 10:
        return 50.0

    score = 50.0
    ma20 = _sma(closes, 20)
    ma50 = _sma(closes, min(50, n))
    price = closes[-1]

    if ma20 and price > ma20:
        score += 15
    elif ma20 and price < ma20:
        score -= 10

    if ma50 and price > ma50:
        score += 10
    elif ma50 and price < ma50:
        score -= 8

    # 10-day return
    if n >= 10:
        gain10 = (closes[-1] - closes[-10]) / closes[-10] * 100
        score  += max(-15, min(15, gain10 * 1.5))

    # Volume trend
    if len(volumes) >= 10:
        avg_vol = sum(v for v in volumes[-20:-5] if v) / max(1, len([v for v in volumes[-20:-5] if v]))
        last_vol = volumes[-1] or 0
        if avg_vol > 0 and last_vol > avg_vol * 1.5:
            score += 8

    return max(0, min(100, score))


def _sell_health(sd) -> float:
    if not sd:
        return 65.0  # neutral if no sell data
    lvl = sd.get("level", "NONE")
    m = {
        "NONE": 80, "WATCH": 65, "TRIM": 45,
        "ROTATE": 40, "SELL": 20, "STOP_LOSS": 5,
    }
    return float(m.get(lvl, 60))


def _sector_score(sl) -> float:
    if not sl:
        return 55.0
    lvl = sl.get("level", "NEUTRAL")
    m = {"LEADING": 90, "IMPROVING": 75, "NEUTRAL": 55, "WEAKENING": 35, "LAGGING": 15}
    return float(m.get(lvl, 55))


def _evaluate_kill_signal(ohlcv: dict, sd) -> dict:
    closes  = ohlcv.get("closes",  [])
    volumes = ohlcv.get("volumes", [])
    opens   = ohlcv.get("opens",   [])
    n = len(closes)

    triggers: list[str] = []
    if n < 20:
        return {"triggered": False, "triggers": [], "primary": ""}

    ma20 = _sma(closes, 20)
    price = closes[-1]

    if ma20 and price < ma20:
        triggers.append(f"收盤跌破 MA20（{ma20:.2f}）")

    if sd and sd.get("level") == "STOP_LOSS":
        sp = (sd.get("detail") or {}).get("stop_price")
        if sp:
            triggers.append(f"觸發固定停損 {sp:.2f}")

    if n >= 2 and opens and len(opens) >= 2:
        if opens[-1] > 0 and closes[-1] < opens[-1] and (opens[-1] - closes[-1]) / opens[-1] > 0.02:
            if volumes and len(volumes) >= 20:
                avg_v = sum(v for v in volumes[-20:-1] if v) / max(1, sum(1 for v in volumes[-20:-1] if v))
                if avg_v > 0 and (volumes[-1] or 0) > avg_v * 1.8:
                    triggers.append("量增大跌（放量長黑K），賣壓沉重")

    return {
        "triggered": len(triggers) > 0,
        "triggers":  triggers,
        "primary":   triggers[0] if triggers else "",
    }


def _score_alternatives(symbol: str, ohlcv: dict, watchlist_ohlcv: dict) -> list[dict]:
    if not watchlist_ohlcv:
        return []

    my_mom = _momentum_score(ohlcv)
    alts = []
    for sym, wl_ohlcv in watchlist_ohlcv.items():
        if sym.upper() == symbol:
            continue
        try:
            mom = _momentum_score(wl_ohlcv)
            if mom > my_mom + 10:
                alts.append({"symbol": sym, "momentum_score": round(mom)})
        except Exception:
            pass

    alts.sort(key=lambda x: x["momentum_score"], reverse=True)
    return alts[:5]


def _build_cases(ohlcv, cr, sd, sl, mr) -> tuple[list, list]:
    closes = ohlcv.get("closes", [])
    bull, bear = [], []

    n = len(closes)
    ma20 = _sma(closes, 20) if n >= 20 else None
    ma50 = _sma(closes, 50) if n >= 50 else None
    price = closes[-1] if closes else None

    if price and ma20 and price > ma20:
        bull.append(f"股價站上 MA20（{ma20:.2f}），短期趨勢向上")
    if price and ma50 and price > ma50:
        bull.append(f"股價站上 MA50（{ma50:.2f}），中期趨勢健康")

    if mr.get("market_regime") == "RISK_ON":
        bull.append("市場處於 RISK_ON 環境，整體偏多有利多頭操作")

    cr_level = cr.get("level", "")
    if cr_level in ("LOW", "MEDIUM"):
        bull.append(f"追高風險 {cr_level}（{cr.get('score', '?')}分），目前位置不算過高")

    if sl and sl.get("level") in ("LEADING", "IMPROVING"):
        bull.append(f"板塊狀態 {sl.get('level_label', sl.get('level'))}，板塊動能支撐")

    # Bear case
    for r in (cr.get("reasons") or [])[:2]:
        bear.append(r)

    if sd and sd.get("level") in ("TRIM", "SELL", "STOP_LOSS"):
        bear.append(f"持倉信號：{sd.get('level_label', sd.get('level'))}（{(sd.get('reasons') or [''])[0]}）")

    if mr.get("market_regime") in ("RISK_OFF", "CRASH_RISK"):
        bear.append(f"市場 {mr['market_regime']}：環境不利多頭操作")

    return bull[:5], bear[:5]



def _next_check_time(decision: str) -> str:
    now = datetime.now(timezone.utc)
    if decision in ("STRONG_BUY", "BUY"):
        delta = timedelta(hours=4)
    elif decision in ("WATCH", "HOLD"):
        delta = timedelta(hours=24)
    else:
        delta = timedelta(hours=8)
    return (now + delta).strftime("%Y-%m-%d %H:%M UTC")


def _main_reason(decision: str, level: str, score: int, symbol: str) -> str:
    texts = {
        "STRONG_BUY": f"{symbol} 達到頂級進場條件（綜合分 {score}）：市場、板塊、個股三重共振，優先積極配置。",
        "BUY":        f"{symbol} 進場條件良好（{score}分），可建倉，控制追高風險並設定停損。",
        "WATCH":      f"{symbol} 信號尚未成熟（{score}分），建議觀察等待更強確認信號。",
        "HOLD":       f"{symbol} 可續持（{score}分），無明顯賣出訊號，繼續追蹤。",
        "TRIM":       f"{symbol} 建議分批減倉（{score}分），部分獲利了結或降低風險。",
        "SELL":       f"{symbol} 觸發賣出條件（{score}分），建議出場或大幅減倉。",
        "AVOID":      f"{symbol} 資料品質或市場條件不達標（{score}分），避免操作。",
    }
    return texts.get(decision, f"{symbol} 決策等級 {level}，綜合分 {score}。")


# ── UI compat helpers ─────────────────────────────────────────────────────────

def _decision_label(decision: str) -> str:
    m = {
        "STRONG_BUY": "強力買進", "BUY": "積極進場",
        "WATCH": "觀察等待", "HOLD": "可續抱",
        "TRIM": "分批減倉", "SELL": "賣出出場",
        "AVOID": "避免操作",
    }
    return m.get(decision, decision)


def _level_to_action(level: str, decision: str) -> str:
    """Map new level system to old A1/A2/B1/B2/C1/C2/NO for UI compat."""
    m = {"A+": "A1", "A": "A2", "B": "B1", "C": "B2", "D": "C1"}
    if decision == "AVOID":
        return "C2"
    return m.get(level, "NO")


def _build_dimensions_ui(mr, dq, cr, sd, sl, chase_score, composite, decision, level) -> list:
    """Build the 10-dimension array expected by _renderTTD() in the frontend."""
    def dim(id_, name, icon, score, note, weight=0.1):
        s = round(max(0, min(100, score)))
        g = "A" if s >= 80 else "B" if s >= 65 else "C" if s >= 45 else "D" if s >= 30 else "F"
        return {"id": id_, "name": name, "icon": icon, "score": s, "grade": g, "note": note, "weight": weight}

    mr_s  = mr.get("market_score", 50)
    dq_s  = dq.get("data_quality_score", 50)
    cr_s  = max(0, 100 - chase_score)
    sd_s  = _sell_health(sd)
    sl_s  = _sector_score(sl)

    mom_s = 0
    closes = {}
    try:
        from operator import itemgetter
    except Exception:
        pass

    dims = [
        dim("d1", "市場狀態",  "🌍", mr_s,  f"{mr.get('market_regime')} · {mr_s}分", 0.20),
        dim("d2", "資料品質",  "📡", dq_s,  f"{dq.get('data_status')} · {dq.get('source', '?')}", 0.12),
        dim("d3", "追高風險",  "🎯", cr_s,  f"風險分={chase_score} → 安全分={cr_s}", 0.15),
        dim("d4", "個股動能",  "⚡", cr.get("detail", {}).get("ma20_dev_pct", 0) * 3 + 50,
            f"MA20偏差 {(cr.get('detail') or {}).get('ma20_dev_pct', '?')}%", 0.15),
        dim("d5", "賣出條件",  "🚦", sd_s,  f"{(sd or {}).get('level', 'N/A')} · {sd_s:.0f}分", 0.10),
        dim("d6", "板塊領導",  "📊", sl_s,  f"{(sl or {}).get('level', 'N/A')} · {sl_s:.0f}分", 0.10),
        dim("d7", "市場許可",  "🔓", 90 if mr.get("market_regime") == "RISK_ON" else 30,
            "RISK_ON" if mr.get("market_regime") == "RISK_ON" else mr.get("market_regime", "?"), 0.08),
        dim("d8", "資金效率",  "💰", dq_s * 0.7 + 30, f"品質{dq_s}分 × 系數0.7", 0.05),
        dim("d9", "風險預算",  "🛡",  mr_s * 0.5 + cr_s * 0.5, f"市{mr_s} × 個{cr_s}", 0.05),
    ]

    # D10 final
    gc = {"A+": "A", "A": "A", "B": "B", "C": "C", "D": "D"}.get(level, "C")
    dims.append({
        "id": "d10", "name": "最終操作等級", "icon": "🏆",
        "score": composite, "grade": level,
        "note": _decision_label(decision), "weight": 0,
    })
    return dims
