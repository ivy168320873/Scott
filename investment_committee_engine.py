"""
Investment Committee Engine — Phase 15
Simulates an institutional investment committee with 5 independent analysts.
Each committee votes independently; votes are aggregated with hard rules.

Public API
----------
run_investment_committee(
    symbol, ohlcv_fn,
    holdings=None, risk_profile="balanced"
) -> dict
"""
from __future__ import annotations

import math
import statistics
from datetime import datetime, timezone

import market_regime_engine      as _mre
import data_quality_engine       as _dqe
import institutional_flow_engine as _ife

try:
    import top_tier_decision_engine as _ttde
    _HAS_TTDE = True
except ImportError:
    _HAS_TTDE = False

try:
    import macro_risk_engine as _macro_eng
    _HAS_MACRO = True
except ImportError:
    _HAS_MACRO = False

try:
    import signal_confidence_engine as _sce
    _HAS_SCE = True
except ImportError:
    _HAS_SCE = False

try:
    import portfolio_optimizer as _poe
    _HAS_POE = True
except ImportError:
    _HAS_POE = False

try:
    from sector_map import SYMBOL_TO_SECTOR as _SYM_SECTOR
except ImportError:
    _SYM_SECTOR: dict = {}

_DISCLAIMER = "此為決策輔助，不代表自動下單，不構成投資建議。操作前請自行評估風險。"

_VOTE_SCORE = {"BUY": 1, "HOLD": 0, "WATCH": 0, "SELL": -1}

# Decision ladder (strong → weak)
_DECISION_ORDER = [
    "STRONG_BUY", "BUY", "WATCH", "HOLD", "TRIM", "SELL", "AVOID"
]


# ── Shared helpers ────────────────────────────────────────────────────────────

def _sma(closes: list[float], period: int) -> float | None:
    if len(closes) < period:
        return None
    return sum(closes[-period:]) / period


def _clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))


def _safe(ohlcv: dict, field: str, default=None):
    return (ohlcv or {}).get(field, default)


# ── Committee 1: Technical ────────────────────────────────────────────────────

def _technical_committee(ohlcv: dict, flow: dict, ttd: dict) -> dict:
    """
    Momentum · Trend · Breakout · Relative Strength
    Votes purely on price/volume technicals.
    """
    closes  = _safe(ohlcv, "closes", [])
    highs   = _safe(ohlcv, "highs",  [])
    lows    = _safe(ohlcv, "lows",   [])
    volumes = _safe(ohlcv, "volumes", [])
    n       = len(closes)
    reasons: list[str] = []

    if n < 20:
        return {"vote": "HOLD", "confidence": 15, "reasons": ["技術資料不足（<20 根K線）"]}

    score = 50.0
    price = closes[-1]

    # ── MA trend ──────────────────────────────────────────────────────────────
    ma20 = _sma(closes, 20)
    ma50 = _sma(closes, min(50, n))
    ma200 = _sma(closes, min(200, n))

    ma_above = 0
    if ma20:
        if price > ma20:
            score += 12; ma_above += 1
        else:
            score -= 10
    if ma50:
        if price > ma50:
            score += 8; ma_above += 1
        else:
            score -= 6
    if ma200:
        if price > ma200:
            score += 6; ma_above += 1
        else:
            score -= 4

    if ma_above == 3:
        reasons.append("均線多頭排列（MA20/50/200 全部站上）")
    elif ma_above == 0 and n >= 50:
        reasons.append("均線空頭排列（MA20/50/200 全部跌破）")
    elif ma20 and price > ma20:
        reasons.append(f"收盤 {price:.2f} 站上 MA20 {ma20:.2f}")
    elif ma20 and price < ma20:
        reasons.append(f"收盤 {price:.2f} 跌破 MA20 {ma20:.2f}")

    # ── Momentum ──────────────────────────────────────────────────────────────
    if n >= 11:
        ret10 = (closes[-1] / closes[-11] - 1) * 100
        score += _clamp(ret10 * 1.5, -18, 18)
        if ret10 > 8:
            reasons.append(f"10 日強勁漲幅 +{ret10:.1f}%，動能爆發")
        elif ret10 > 3:
            reasons.append(f"10 日漲幅 +{ret10:.1f}%，動能持續")
        elif ret10 < -8:
            reasons.append(f"10 日跌幅 {ret10:.1f}%，動能崩潰")
        elif ret10 < -3:
            reasons.append(f"10 日跌幅 {ret10:.1f}%，動能轉弱")

    if n >= 21:
        ret20 = (closes[-1] / closes[-21] - 1) * 100
        score += _clamp(ret20 * 0.8, -12, 12)

    # ── Relative strength vs QQQ ──────────────────────────────────────────────
    rs_qqq = flow.get("relative_strength_vs_QQQ")
    if rs_qqq is not None:
        if rs_qqq > 1.08:
            score += 14
            reasons.append(f"相對強度 {rs_qqq:.2f}×，明顯超越 QQQ")
        elif rs_qqq > 1.02:
            score += 7
            reasons.append(f"相對強度 {rs_qqq:.2f}×，小幅跑贏 QQQ")
        elif rs_qqq < 0.90:
            score -= 14
            reasons.append(f"相對強度 {rs_qqq:.2f}×，嚴重落後 QQQ")
        elif rs_qqq < 0.97:
            score -= 7
            reasons.append(f"相對強度 {rs_qqq:.2f}×，略為落後 QQQ")

    # ── Breakout quality ──────────────────────────────────────────────────────
    bq = flow.get("breakout_quality") or 50
    score += _clamp((bq - 50) * 0.20, -10, 10)
    if bq > 72:
        reasons.append(f"突破品質 {bq}／100，放量有效突破")
    elif bq < 30:
        reasons.append(f"突破品質 {bq}／100，量縮無效突破")

    # ── Price-volume confirmation ─────────────────────────────────────────────
    pvc = flow.get("price_volume_confirmation") or 50
    score += _clamp((pvc - 50) * 0.10, -5, 5)
    if pvc > 68:
        reasons.append(f"量價配合 {pvc}，主力資金配合")

    score = _clamp(score)

    # Confidence: improves with more bars and signal clarity
    conf = int(_clamp(35 + n // 8, 35, 85))
    if ohlcv.get("is_demo"):
        conf = min(30, conf)

    # Vote thresholds
    if score >= 73:
        vote = "BUY"
    elif score >= 56:
        vote = "WATCH"
    elif score >= 40:
        vote = "HOLD"
    else:
        vote = "SELL"

    return {
        "vote": vote, "confidence": conf,
        "reasons": reasons[:4], "score": round(score),
    }


# ── Committee 2: Risk ─────────────────────────────────────────────────────────

def _risk_committee(ttd: dict, ohlcv: dict) -> dict:
    """
    Chase Risk · Kill Signal · Max Drawdown · Position Sizing
    The committee that protects against losses.
    """
    closes  = _safe(ohlcv, "closes", [])
    n       = len(closes)
    reasons: list[str] = []

    chase    = int(ttd.get("chase_risk_score") or 0)
    kill     = (ttd.get("kill_signal") or {}).get("triggered", False)
    dq       = ttd.get("data_quality") or {}
    dq_status = dq.get("data_status", "FAIR") or "FAIR"
    is_demo  = dq.get("is_demo", True)
    pos_level = ttd.get("position_size_level", "NO_TRADE") or "NO_TRADE"
    must_not = ttd.get("must_not_buy_reasons", []) or []
    sell_dec = (ttd.get("sell_decision") or {})
    sell_level = sell_dec.get("level", "NONE") if sell_dec else "NONE"

    score = 60.0  # risk committee starts cautious

    # ── Kill signal ───────────────────────────────────────────────────────────
    if kill:
        reasons.append(f"Kill Signal 觸發：{(ttd.get('kill_signal') or {}).get('primary', '異常賣壓')}")
        return {"vote": "SELL", "confidence": 92, "reasons": reasons, "score": 5}

    # ── Stop loss triggered ───────────────────────────────────────────────────
    if sell_level == "STOP_LOSS":
        reasons.append(f"停損訊號觸發：{sell_dec.get('reasons', ['---'])[0] if sell_dec else '停損條件成立'}")
        return {"vote": "SELL", "confidence": 90, "reasons": reasons, "score": 8}

    # ── Data quality ──────────────────────────────────────────────────────────
    if dq_status == "BAD":
        reasons.append("資料品質 BAD：不可靠資料，風險委員會拒絕放行")
        return {"vote": "SELL", "confidence": 88, "reasons": reasons, "score": 10}

    if is_demo:
        reasons.append("模擬資料環境，實際風險無法評估，風險委員會保守觀望")
        return {"vote": "WATCH", "confidence": 25, "reasons": reasons, "score": 45}

    # ── Chase risk ────────────────────────────────────────────────────────────
    if chase > 85:
        score -= 40
        reasons.append(f"追高風險 {chase}／100，嚴重超買，禁止追入")
    elif chase > 75:
        score -= 25
        reasons.append(f"追高風險 {chase}／100，偏高，建議等待回測")
    elif chase > 60:
        score -= 15
        reasons.append(f"追高風險 {chase}／100，中等風險，謹慎介入")
    elif chase < 35:
        score += 15
        reasons.append(f"追高風險 {chase}／100，低風險區間，可安全佈局")
    else:
        reasons.append(f"追高風險 {chase}／100，尚在合理範圍")

    # ── Max drawdown estimate (ATR-based) ─────────────────────────────────────
    if n >= 15 and closes:
        highs = _safe(ohlcv, "highs", [])
        lows  = _safe(ohlcv, "lows",  [])
        atrs  = []
        for i in range(max(0, n - 15), n):
            hi = highs[i] if i < len(highs) else closes[i]
            lo = lows[i]  if i < len(lows)  else closes[i]
            prev_c = closes[i - 1] if i > 0 else closes[i]
            atr = max(hi - lo, abs(hi - prev_c), abs(lo - prev_c))
            atrs.append(atr)
        avg_atr = sum(atrs) / len(atrs) if atrs else 0
        atr_pct = avg_atr / closes[-1] * 100 if closes[-1] > 0 else 0
        est_dd  = atr_pct * 15  # rough 15-bar drawdown estimate
        if est_dd > 25:
            score -= 10
            reasons.append(f"波動率高，預估最大回撤 ~{est_dd:.0f}%，風險較大")
        elif est_dd < 10:
            score += 5
            reasons.append(f"波動率低，預估最大回撤 ~{est_dd:.0f}%，風險可控")

    # ── Position sizing ───────────────────────────────────────────────────────
    if pos_level in ("AGGRESSIVE", "NORMAL"):
        score += 10
        reasons.append(f"倉位建議 {pos_level}，風險委員會核准")
    elif pos_level in ("TINY", "NO_TRADE"):
        score -= 20
        reasons.append(f"倉位建議 {pos_level}，風險條件不符")
    elif pos_level == "SMALL":
        reasons.append(f"倉位建議 {pos_level}，小倉試探")

    # ── Sell decision ────────────────────────────────────────────────────────
    if sell_level in ("SELL", "ROTATE"):
        score -= 15
        reasons.append(f"賣出引擎評級 {sell_level}")
    elif sell_level == "TRIM":
        score -= 8
        reasons.append("持有成本偏高，建議減碼")

    score = _clamp(score)
    conf = int(_clamp(50 + (100 - chase) * 0.3, 40, 88))

    if score >= 70:
        vote = "BUY"
    elif score >= 52:
        vote = "WATCH"
    elif score >= 38:
        vote = "HOLD"
    else:
        vote = "SELL"

    return {"vote": vote, "confidence": conf, "reasons": reasons[:4], "score": round(score)}


# ── Committee 3: Market ───────────────────────────────────────────────────────

def _market_committee(ttd: dict, macro: dict) -> dict:
    """
    Market Regime · Macro Risk · Sector Rotation · SPY/QQQ/SOXX
    Top-down macro perspective.
    """
    reasons: list[str] = []

    regime      = ttd.get("market_regime", "NEUTRAL") or "NEUTRAL"
    mkt_score   = int(ttd.get("market_score") or 50)
    macro_score = int(macro.get("macro_score") or 50)
    macro_regime = macro.get("macro_regime", "") or ""
    risk_budget = float(ttd.get("risk_budget_mult") or 0.6)
    sector_ldr  = ttd.get("sector_leadership") or {}
    sector_lvl  = sector_ldr.get("level", "NEUTRAL") if sector_ldr else "NEUTRAL"
    key_drivers = macro.get("key_drivers", []) or []
    warnings    = macro.get("warnings", []) or []

    score = 50.0

    # ── Market regime ─────────────────────────────────────────────────────────
    regime_adj = {
        "RISK_ON": 25, "NEUTRAL": 0, "RISK_OFF": -25, "CRASH_RISK": -40
    }
    score += regime_adj.get(regime, 0)
    reasons.append(f"市場環境 {regime}（評分 {mkt_score}）")

    # ── Market score fine-tuning ──────────────────────────────────────────────
    score += (mkt_score - 50) * 0.20

    # ── Macro risk ────────────────────────────────────────────────────────────
    if macro_score > 0:
        score += (macro_score - 50) * 0.25
        if macro_regime:
            reasons.append(f"總體宏觀 {macro_regime}（評分 {macro_score}）")
        if macro_score >= 68:
            reasons.append("宏觀指標全面展開，市場委員會看多")
        elif macro_score < 35:
            reasons.append("宏觀指標惡化，防禦配置為主")

    # ── Risk budget ───────────────────────────────────────────────────────────
    score += (risk_budget - 0.6) * 15

    # ── Sector rotation ───────────────────────────────────────────────────────
    sector_adj = {
        "LEADING": 12, "IMPROVING": 6, "NEUTRAL": 0,
        "WEAKENING": -8, "LAGGING": -14,
    }
    score += sector_adj.get(sector_lvl, 0)
    if sector_lvl in ("LEADING", "IMPROVING"):
        sec_name = sector_ldr.get("sector_name", "") or ""
        reasons.append(f"板塊 {sec_name} 處於 {sector_lvl}，順勢加分")
    elif sector_lvl in ("LAGGING", "WEAKENING"):
        sec_name = sector_ldr.get("sector_name", "") or ""
        reasons.append(f"板塊 {sec_name} 處於 {sector_lvl}，不宜逆勢追進")

    # ── Macro warnings ────────────────────────────────────────────────────────
    if warnings:
        score -= len(warnings) * 3
        reasons.append(f"宏觀預警 {len(warnings)} 項：{warnings[0]}")

    score = _clamp(score)
    conf  = int(_clamp(55 + mkt_score * 0.20, 45, 88))
    if (ttd.get("data_quality") or {}).get("is_demo"):
        conf = min(30, conf)

    if regime in ("RISK_OFF", "CRASH_RISK"):
        return {"vote": "SELL", "confidence": conf, "reasons": reasons[:4], "score": round(score)}

    if score >= 70:
        vote = "BUY"
    elif score >= 55:
        vote = "WATCH"
    elif score >= 40:
        vote = "HOLD"
    else:
        vote = "SELL"

    return {"vote": vote, "confidence": conf, "reasons": reasons[:4], "score": round(score)}


# ── Committee 4: Institutional Flow ──────────────────────────────────────────

def _institutional_committee(flow: dict) -> dict:
    """
    Institutional Flow · Smart Money · Distribution Risk · Liquidity
    Follows the smart money footprint.
    """
    reasons: list[str] = []

    direction = flow.get("flow_direction", "NEUTRAL") or "NEUTRAL"
    inst_acc  = int(flow.get("institutional_accumulation_score") or 50)
    smart_s   = int(flow.get("smart_money_score") or 50)
    dist_risk = int(flow.get("distribution_risk") or 0)
    liq       = int(flow.get("liquidity_quality") or 50)
    vol_acc   = int(flow.get("volume_accumulation_score") or 50)
    is_demo   = flow.get("is_demo", True)
    bar_count = flow.get("bar_count", 0) or 0

    # Immediate hard calls
    if direction == "DISTRIBUTION":
        reasons.append(f"法人出貨明確（出貨風險 {dist_risk}），機構委員會強烈建議賣出")
        return {"vote": "SELL", "confidence": 82, "reasons": reasons, "score": 12}

    if direction == "FAKE_BREAKOUT":
        reasons.append("假突破形態確認，主力誘多後倒貨，拒絕跟進")
        return {"vote": "SELL", "confidence": 78, "reasons": reasons, "score": 18}

    score = 50.0

    # ── Institutional accumulation score ─────────────────────────────────────
    score += (inst_acc - 50) * 0.35
    reasons.append(f"機構累積評分 {inst_acc}／100")

    # ── Smart money ───────────────────────────────────────────────────────────
    score += (smart_s - 50) * 0.25
    if smart_s > 70:
        reasons.append(f"聰明資金流入 {smart_s}，主力積極吸籌")
    elif smart_s < 35:
        reasons.append(f"聰明資金流出 {smart_s}，主力悄悄出脫")

    # ── Distribution risk ─────────────────────────────────────────────────────
    if dist_risk > 60:
        score -= 20
        reasons.append(f"出貨風險 {dist_risk}，高位震盪可能派發")
    elif dist_risk > 40:
        score -= 8
        reasons.append(f"出貨風險 {dist_risk}，需監控量能變化")

    # ── Volume accumulation ───────────────────────────────────────────────────
    score += (vol_acc - 50) * 0.15

    # ── Liquidity quality ─────────────────────────────────────────────────────
    if liq > 70:
        score += 6
        reasons.append(f"流動性良好 {liq}，大量買進不易影響股價")
    elif liq < 30:
        score -= 8
        reasons.append(f"流動性不足 {liq}，小股票需注意滑點")

    score = _clamp(score)

    # Confidence: scales with bar count and non-demo status
    conf = int(_clamp(30 + bar_count // 5, 30, 82))
    if is_demo:
        conf = min(28, conf)

    if direction == "ACCUMULATION":
        if score >= 68:
            vote = "BUY"
        else:
            vote = "WATCH"
        reasons.append("法人悄悄建倉，機構委員會看多")
    elif score >= 68:
        vote = "BUY"
    elif score >= 52:
        vote = "WATCH"
    elif score >= 38:
        vote = "HOLD"
    else:
        vote = "SELL"

    return {"vote": vote, "confidence": conf, "reasons": reasons[:4], "score": round(score)}


# ── Committee 5: Portfolio ────────────────────────────────────────────────────

def _portfolio_committee(
    symbol: str,
    ttd: dict,
    holdings: list[dict] | None,
    risk_profile: str,
    ohlcv_fn,
) -> dict:
    """
    Portfolio Optimization · Sector Exposure · Capital Efficiency
    Evaluates if this position improves the overall portfolio.
    """
    reasons: list[str] = []
    holdings = holdings or []

    # ── Quick portfolio optimizer check ───────────────────────────────────────
    poe_result: dict = {}
    if _HAS_POE and ohlcv_fn:
        try:
            watchlist = [symbol] if not any(
                (h.get("symbol") or "").upper() == symbol for h in holdings
            ) else []
            poe_result = _poe.run_portfolio_optimize(
                account_value = 0,
                current_cash  = 0,
                holdings      = holdings,
                watchlist     = watchlist,
                risk_profile  = risk_profile,
                ohlcv_fn      = ohlcv_fn,
            )
        except Exception:
            pass

    # ── Extract portfolio signals for this symbol ─────────────────────────────
    opt_weight = (poe_result.get("optimal_weights") or {}).get(symbol, 0.0)
    to_exit    = symbol in (poe_result.get("positions_to_exit") or [])
    to_trim    = symbol in (poe_result.get("positions_to_trim") or [])
    to_add     = symbol in (poe_result.get("positions_to_add")  or [])
    port_score = int(poe_result.get("portfolio_score") or 0)
    blocked    = any(b.get("symbol") == symbol
                     for b in (poe_result.get("blocked_detail") or []))
    blocked_reason = next(
        (b.get("reason") for b in (poe_result.get("blocked_detail") or [])
         if b.get("symbol") == symbol), ""
    )

    # Current holding details
    current_holding = next(
        (h for h in holdings if (h.get("symbol") or "").upper() == symbol),
        None,
    )

    score = 50.0

    # ── Portfolio optimizer verdict ───────────────────────────────────────────
    if blocked:
        score -= 30
        reasons.append(f"投資組合優化器排除此標的：{blocked_reason}")
    elif to_exit:
        score -= 25
        reasons.append("投組優化建議出清此標的")
    elif to_trim:
        score -= 12
        reasons.append("投組優化建議減碼")
    elif to_add:
        score += 20
        reasons.append(f"投組優化建議新建倉位（最佳比重 {opt_weight:.1f}%）")
    elif opt_weight > 10:
        score += 12
        reasons.append(f"最佳配比 {opt_weight:.1f}%，與投組相容性佳")
    elif opt_weight > 3:
        score += 5
        reasons.append(f"最佳配比 {opt_weight:.1f}%，可適度佈局")

    # ── Sector exposure ───────────────────────────────────────────────────────
    sector = _SYM_SECTOR.get(symbol.upper(), "其他")
    sector_exp = (poe_result.get("sector_exposure") or {}).get(sector, 0.0)
    macro_regime = poe_result.get("macro_regime", "NEUTRAL") or "NEUTRAL"
    max_sector_pct = {"conservative": 30, "balanced": 40, "aggressive": 50}.get(
        risk_profile.lower(), 40
    )
    if sector_exp > max_sector_pct * 0.85:
        score -= 10
        reasons.append(f"板塊 {sector} 集中度 {sector_exp:.0f}%，接近上限 {max_sector_pct}%")
    elif sector_exp > 0:
        reasons.append(f"板塊 {sector}：目前曝險 {sector_exp:.0f}%")

    # ── Capital efficiency (current holding) ──────────────────────────────────
    if current_holding:
        cost  = float(current_holding.get("cost") or 0)
        ttd_score = int(ttd.get("top_tier_score") or 50)
        if cost > 0:
            closes = _safe(ttd.get("data_quality") or {}, "closes", [])
            # Use TTD score as proxy for capital efficiency
            if ttd_score > 70:
                score += 10
                reasons.append(f"現有持倉效率佳（TTD {ttd_score}），資金有效運用")
            elif ttd_score < 40:
                score -= 10
                reasons.append(f"現有持倉效率低（TTD {ttd_score}），資金利用率差")

    # ── Portfolio score context ────────────────────────────────────────────────
    if port_score > 0:
        score += (port_score - 50) * 0.10
        if port_score >= 70:
            reasons.append(f"整體投組評分 {port_score}，環境有利")
        elif port_score < 40:
            reasons.append(f"整體投組評分 {port_score}，環境不利")

    score = _clamp(score)

    # If no holdings and no optimizer data, fall back to TTD score
    if not poe_result and not holdings:
        ttd_score = int(ttd.get("top_tier_score") or 50)
        score = 40 + ttd_score * 0.20
        reasons.append(f"無持倉資料，以 TTD 評分 {ttd_score} 作為組合適合度參考")

    conf = 55 if poe_result else 35
    if (ttd.get("data_quality") or {}).get("is_demo"):
        conf = min(28, conf)

    if score >= 70:
        vote = "BUY"
    elif score >= 53:
        vote = "WATCH"
    elif score >= 38:
        vote = "HOLD"
    else:
        vote = "SELL"

    return {"vote": vote, "confidence": conf, "reasons": reasons[:4], "score": round(score)}


# ── Aggregation ───────────────────────────────────────────────────────────────

def _aggregate_decision(
    committee_score: int,
    buy_votes: int,
    hold_votes: int,
    watch_votes: int,
    sell_votes: int,
    avg_confidence: float,
) -> str:
    """Map vote counts to a final decision string."""
    # Strong consensus for buy
    if committee_score >= 4:
        return "STRONG_BUY"
    if committee_score >= 3:
        return "BUY"
    if committee_score >= 1:
        return "WATCH"
    # Score == 0: distinguish WATCH vs HOLD by vote counts
    if committee_score == 0:
        if watch_votes > hold_votes:
            return "WATCH"
        if buy_votes > 0 and sell_votes > 0:
            return "WATCH"   # mixed signal
        return "HOLD"
    if committee_score == -1:
        return "TRIM"
    if committee_score == -2:
        return "SELL"
    # -3 and below
    return "AVOID"


def _apply_hard_rules(
    decision: str,
    votes: dict[str, dict],
    ttd: dict,
    flow: dict,
) -> tuple[str, list[str]]:
    """
    Apply hard override rules and return (final_decision, override_notes).
    Decision may only be weakened, never strengthened.
    """
    override_notes: list[str] = []

    risk_vote   = votes.get("risk", {})
    regime      = ttd.get("market_regime", "NEUTRAL") or "NEUTRAL"
    is_demo     = (ttd.get("data_quality") or {}).get("is_demo", False)
    flow_dir    = flow.get("flow_direction", "NEUTRAL") or "NEUTRAL"

    # Hard Rule 1: Risk Committee SELL + confidence > 80 → max HOLD
    if (risk_vote.get("vote") == "SELL" and
            int(risk_vote.get("confidence") or 0) > 80):
        if _DECISION_ORDER.index(decision) < _DECISION_ORDER.index("HOLD"):
            decision = "HOLD"
            override_notes.append("風險委員會高置信度賣出（>80）：最終決策上限降為 HOLD")

    # Hard Rule 2: Market Regime RISK_OFF → no STRONG_BUY
    if regime == "RISK_OFF":
        if decision == "STRONG_BUY":
            decision = "BUY"
            override_notes.append("市場環境 RISK_OFF：STRONG_BUY 降級為 BUY")

    # Hard Rule 3: Demo data → max WATCH
    if is_demo:
        if _DECISION_ORDER.index(decision) < _DECISION_ORDER.index("WATCH"):
            decision = "WATCH"
            override_notes.append("Demo 資料環境：最終決策上限為 WATCH")

    # Hard Rule 4: Institutional Flow DISTRIBUTION → downgrade BUY
    if flow_dir == "DISTRIBUTION":
        if decision in ("STRONG_BUY", "BUY"):
            decision = "WATCH"
            override_notes.append("法人出貨確認：買進訊號降級為 WATCH")

    # Hard Rule 5: CRASH_RISK → max WATCH
    if regime == "CRASH_RISK":
        if _DECISION_ORDER.index(decision) < _DECISION_ORDER.index("WATCH"):
            decision = "WATCH"
            override_notes.append("市場環境 CRASH_RISK：決策上限為 WATCH")

    return decision, override_notes


def _compute_confidence(votes: dict[str, dict]) -> int:
    """Overall committee confidence, adjusted for consensus."""
    confs  = [int(v.get("confidence") or 50) for v in votes.values()]
    avg_c  = statistics.mean(confs) if confs else 50

    vote_vals = [_VOTE_SCORE.get(v.get("vote", "HOLD"), 0) for v in votes.values()]
    abs_sum   = sum(abs(x) for x in vote_vals)

    # Agreement bonus/penalty
    if abs_sum == 5:         # unanimous
        adj = 8
    elif abs_sum >= 4:
        adj = 4
    elif abs_sum >= 3:
        adj = 0
    elif abs_sum >= 2:
        adj = -5
    else:                    # mixed / neutral
        adj = -10

    return int(_clamp(avg_c + adj, 10, 92))


def _split_reasons(
    votes: dict[str, dict],
    final_decision: str,
) -> tuple[list[str], list[str]]:
    """
    Separate majority_reasons (aligned with final) from minority_reasons (opposing).
    """
    # Determine if each committee "agrees" with the final decision
    bullish = final_decision in ("STRONG_BUY", "BUY", "WATCH")
    bearish = final_decision in ("SELL", "AVOID", "TRIM")

    majority: list[str] = []
    minority: list[str] = []

    for name, v in votes.items():
        committee_vote = v.get("vote", "HOLD")
        reasons        = v.get("reasons", [])[:2]
        is_bullish_vote = committee_vote == "BUY"
        is_bearish_vote = committee_vote == "SELL"

        if (bullish and is_bullish_vote) or (bearish and is_bearish_vote):
            majority.extend(reasons)
        elif (bullish and is_bearish_vote) or (bearish and is_bullish_vote):
            minority.extend(reasons)

    # Remove duplicates, cap length
    seen: set[str] = set()
    clean_majority: list[str] = []
    for r in majority:
        if r not in seen:
            seen.add(r)
            clean_majority.append(r)

    seen2: set[str] = set()
    clean_minority: list[str] = []
    for r in minority:
        if r not in seen2:
            seen2.add(r)
            clean_minority.append(r)

    return clean_majority[:6], clean_minority[:4]


# ── Public API ────────────────────────────────────────────────────────────────

def run_investment_committee(
    symbol: str,
    ohlcv_fn,
    holdings: list[dict] | None = None,
    risk_profile: str = "balanced",
) -> dict:
    """
    Run the investment committee for one symbol.

    Parameters
    ----------
    symbol       : stock ticker (e.g. "NVDA", "2330.TW")
    ohlcv_fn     : callable(symbol) -> ohlcv dict
    holdings     : current portfolio holdings (optional, for Portfolio Committee)
    risk_profile : "conservative" | "balanced" | "aggressive"
    """
    symbol  = str(symbol or "").upper().strip()
    profile = (risk_profile or "balanced").lower().strip()
    generated_at = datetime.now(timezone.utc).isoformat()

    # ── Gather shared engine data ─────────────────────────────────────────────
    ohlcv: dict = {}
    try:
        ohlcv = ohlcv_fn(symbol)
    except Exception:
        pass

    ttd: dict = {}
    if _HAS_TTDE and ohlcv_fn:
        try:
            sector_name = _SYM_SECTOR.get(symbol, "")
            ttd = _ttde.run_top_tier_decision(symbol, ohlcv_fn, sector_name=sector_name)
        except Exception:
            pass
    if not ttd:
        # Minimal fallback so committees don't crash
        dq = _dqe.run_data_quality(ohlcv)
        mr = {}
        try:
            mr = _mre.run_market_regime(ohlcv_fn)
        except Exception:
            pass
        ttd = {
            "top_tier_score":    50,
            "decision":          "HOLD",
            "market_regime":     mr.get("market_regime", "NEUTRAL"),
            "market_score":      mr.get("market_score", 50),
            "risk_budget_mult":  mr.get("risk_budget_multiplier", 0.6),
            "chase_risk_score":  50,
            "data_quality":      dq,
            "kill_signal":       {"triggered": False},
            "position_size_level": "NO_TRADE",
            "must_not_buy_reasons": [],
        }

    bench_ohlcv: dict = {}
    try:
        bench_ohlcv = ohlcv_fn("QQQ")
    except Exception:
        pass

    flow: dict = {}
    try:
        flow = _ife.run_institutional_flow(symbol, ohlcv, bench_ohlcv)
    except Exception:
        flow = {
            "flow_direction": "NEUTRAL", "institutional_accumulation_score": 50,
            "smart_money_score": 50, "distribution_risk": 0, "liquidity_quality": 50,
            "volume_accumulation_score": 50, "breakout_quality": 50,
            "price_volume_confirmation": 50, "relative_strength_vs_QQQ": None,
            "is_demo": True, "bar_count": 0,
        }

    macro: dict = {}
    if _HAS_MACRO and ohlcv_fn:
        try:
            macro = _macro_eng.run_macro_risk(ohlcv_fn)
        except Exception:
            pass

    # ── Each committee votes independently ───────────────────────────────────
    tech_vote  = _technical_committee(ohlcv, flow, ttd)
    risk_vote  = _risk_committee(ttd, ohlcv)
    mkt_vote   = _market_committee(ttd, macro)
    inst_vote  = _institutional_committee(flow)
    port_vote  = _portfolio_committee(symbol, ttd, holdings, profile, ohlcv_fn)

    committee_votes = {
        "technical":    tech_vote,
        "risk":         risk_vote,
        "market":       mkt_vote,
        "institutional": inst_vote,
        "portfolio":    port_vote,
    }

    # ── Tally votes ───────────────────────────────────────────────────────────
    committee_score = sum(
        _VOTE_SCORE.get(v["vote"], 0) for v in committee_votes.values()
    )
    buy_votes   = sum(1 for v in committee_votes.values() if v["vote"] == "BUY")
    hold_votes  = sum(1 for v in committee_votes.values() if v["vote"] == "HOLD")
    watch_votes = sum(1 for v in committee_votes.values() if v["vote"] == "WATCH")
    sell_votes  = sum(1 for v in committee_votes.values() if v["vote"] == "SELL")

    avg_confidence = statistics.mean(
        int(v.get("confidence") or 50) for v in committee_votes.values()
    )

    # ── Final decision ────────────────────────────────────────────────────────
    raw_decision = _aggregate_decision(
        committee_score, buy_votes, hold_votes, watch_votes,
        sell_votes, avg_confidence,
    )

    final_decision, override_notes = _apply_hard_rules(
        raw_decision, committee_votes, ttd, flow
    )

    confidence = _compute_confidence(committee_votes)

    majority_reasons, minority_reasons = _split_reasons(committee_votes, final_decision)
    if override_notes:
        minority_reasons = override_notes[:2] + minority_reasons

    return {
        "ok":               True,
        "symbol":           symbol,
        "committee_votes":  committee_votes,
        "buy_votes":        buy_votes,
        "hold_votes":       hold_votes,
        "watch_votes":      watch_votes,
        "sell_votes":       sell_votes,
        "committee_score":  committee_score,
        "final_decision":   final_decision,
        "raw_decision":     raw_decision,
        "override_notes":   override_notes,
        "confidence":       confidence,
        "majority_reasons": majority_reasons,
        "minority_reasons": minority_reasons,
        "market_regime":    ttd.get("market_regime", "NEUTRAL"),
        "ttd_score":        ttd.get("top_tier_score", 50),
        "is_demo":          (ttd.get("data_quality") or {}).get("is_demo", True),
        "disclaimer":       _DISCLAIMER,
        "generated_at":     generated_at,
    }
