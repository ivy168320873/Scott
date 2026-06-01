"""
Expert rule-based technical analysis engine + Claude AI integration.
Phase 2: Every analysis output includes Bear Case / Kill Signal / Sell Plan.

Payload may include an optional `decision_results` key containing Phase 1
engine outputs (chase_risk, sell_decision, sector_leadership).  When present,
they enrich both the Claude prompt and the rule-based fallback.
"""
from __future__ import annotations
import os
from typing import Any


# ── Indicator text helpers (unchanged) ───────────────────────────────────────

def _rsi_text(v: float) -> str:
    if v >= 80: return f"RSI {v:.1f} 處於嚴重超買區，短期回調壓力極大"
    if v >= 70: return f"RSI {v:.1f} 進入超買區，需警惕拉回風險"
    if v >= 60: return f"RSI {v:.1f} 偏多頭，動能尚可"
    if v >= 50: return f"RSI {v:.1f} 維持多頭偏強格局"
    if v >= 40: return f"RSI {v:.1f} 略偏弱，中性偏空"
    if v >= 30: return f"RSI {v:.1f} 接近超賣區，需留意止跌訊號"
    return f"RSI {v:.1f} 已在超賣區，逢低買進機會浮現"


def _macd_text(hist: float, macd: float, signal: float) -> str:
    cross = "MACD 上穿 Signal（黃金交叉）" if macd > signal else "MACD 下穿 Signal（死亡交叉）"
    momentum = "柱狀圖為正且放大" if hist > 0 else "柱狀圖為負且擴展"
    if hist > 0:
        return f"{cross}，{momentum}，短線多頭動能加速"
    return f"{cross}，{momentum}，短線空頭壓力增加"


def _bb_text(pct_b: float, price: float, upper: float, lower: float) -> str:
    if pct_b > 1.0:
        return f"價格突破布林上軌（{upper:.2f}），處於超買臨界，需觀察能否持續放量突破"
    if pct_b > 0.8:
        return f"價格緊貼布林上軌（{upper:.2f}），多頭氣勢強勁"
    if pct_b < 0.0:
        return f"價格跌破布林下軌（{lower:.2f}），短線超賣，可能出現技術反彈"
    if pct_b < 0.2:
        return f"價格靠近布林下軌（{lower:.2f}），支撐位參考"
    return f"布林%B = {pct_b:.2f}，價格在布林帶中部遊走，方向待確認"


def _ma_text(ma_analysis: list[dict]) -> str:
    bull = [m for m in ma_analysis if m.get("bullish")]
    bear = [m for m in ma_analysis if not m.get("bullish")]
    if len(bull) >= 3:
        names = "、".join(m["label"] for m in bull[:3])
        return f"價格站上 {names} 等均線，多頭排列佔優"
    if len(bear) >= 3:
        names = "、".join(m["label"] for m in bear[:3])
        return f"價格跌破 {names}，均線空頭排列"
    return "均線多空混雜，市場方向尚未明朗"


def _stoch_text(k: float, d: float) -> str:
    if k > 80 and d > 80: return f"隨機指標 K={k:.1f}/D={d:.1f} 雙雙位於超買區"
    if k < 20 and d < 20: return f"隨機指標 K={k:.1f}/D={d:.1f} 雙雙位於超賣區，可能觸底反彈"
    if k > d and k < 80: return f"K 線上穿 D 線（{k:.1f} > {d:.1f}），短線買進訊號"
    if k < d and k > 20: return f"K 線下穿 D 線（{k:.1f} < {d:.1f}），短線賣出訊號"
    return f"隨機指標 K={k:.1f}/D={d:.1f}，中性"


def _adx_text(adx: float, di_plus: float, di_minus: float) -> str:
    trend = di_plus > di_minus
    if adx >= 40: strength = f"強勢趨勢（ADX={adx:.0f}）"
    elif adx >= 25: strength = f"趨勢成形中（ADX={adx:.0f}）"
    else: strength = f"無明顯趨勢（ADX={adx:.0f}，市場盤整）"
    direction = (f"+DI={di_plus:.1f} > -DI={di_minus:.1f}，多頭方向"
                 if trend else f"-DI={di_minus:.1f} > +DI={di_plus:.1f}，空頭方向")
    return f"{strength}；{direction}"


def _vol_text(ratio: float) -> str:
    if ratio >= 2.0: return f"成交量暴增至均量 {ratio:.1f} 倍，可能為突破確認或主力出貨"
    if ratio >= 1.4: return f"成交量放大至均量 {ratio:.1f} 倍，價量配合良好"
    if ratio <= 0.5: return f"成交量萎縮至均量 {ratio:.1f} 倍，觀望氣氛濃厚"
    return f"成交量正常（均量比 {ratio:.1f}x）"


def _support_resistance(price: float, bb_upper: float, bb_lower: float,
                        sma20: float | None, sma50: float | None) -> dict:
    resistances, supports = [], []
    if bb_upper and bb_upper > price:  resistances.append(round(bb_upper, 2))
    if sma50   and sma50   > price:    resistances.append(round(sma50, 2))
    if sma20   and sma20   > price:    resistances.append(round(sma20, 2))
    if bb_lower and bb_lower < price:  supports.append(round(bb_lower, 2))
    if sma20   and sma20   < price:    supports.append(round(sma20, 2))
    if sma50   and sma50   < price:    supports.append(round(sma50, 2))
    resistances.sort()
    supports.sort(reverse=True)
    return {"nearest_resistance": resistances[:2], "nearest_support": supports[:2]}


def _score_to_signal(score: float) -> tuple[str, str]:
    if score >= 72: return "強力買入", "bullish"
    if score >= 58: return "溫和買入", "mild-bullish"
    if score >= 42: return "中性觀望", "neutral"
    if score >= 28: return "溫和賣出", "mild-bearish"
    return "強力賣出", "bearish"


# ── Phase 2: Bear Case / Kill Signal builders ─────────────────────────────────

def _build_bull_case(ind: dict, ma: list, payload: dict, score: float) -> list[str]:
    """Rule-based Bull Case reasons."""
    rsi_v      = ind.get("rsi") or 50
    macd_h     = ind.get("macd_hist") or 0
    vol_ratio  = payload.get("volume_ratio", 1) or 1
    change_pct = payload.get("change_pct", 0) or 0
    bull_ma    = sum(1 for m in ma if m.get("bullish"))
    total_ma   = len(ma)

    reasons: list[str] = []
    if score >= 70:
        reasons.append(f"動能分數 {score:.0f}/100，技術面強勢")
    if 50 <= rsi_v < 70:
        reasons.append(f"RSI {rsi_v:.0f} 處多頭區間（50-70），動能持續未超買")
    if macd_h > 0:
        reasons.append("MACD 柱狀圖為正，短線多頭動能加速")
    if bull_ma >= 3:
        reasons.append(f"{bull_ma}/{total_ma} 條均線多頭排列，趨勢支撐")
    if vol_ratio >= 1.4:
        reasons.append(f"量能放大至均量 {vol_ratio:.1f}x，買盤積極")
    if change_pct > 1:
        reasons.append(f"今日漲幅 {change_pct:+.2f}%，市場偏正面")

    if not reasons:
        reasons.append("目前多頭因素有限，偏中性格局")
    return reasons


def _build_bear_case(ind: dict, ma: list, payload: dict,
                     score: float, price: float,
                     sma20: float | None, bb_lower: float | None) -> list[str]:
    """Rule-based Bear Case — integrates Phase 1 decision engine results."""
    rsi_v     = ind.get("rsi") or 50
    macd_h    = ind.get("macd_hist") or 0
    bb_pct    = ind.get("bb_pct_b") or 0.5
    vol_ratio = payload.get("volume_ratio", 1) or 1
    bear_ma   = sum(1 for m in ma if not m.get("bullish"))
    total_ma  = len(ma)

    dr = payload.get("decision_results", {})
    cr = dr.get("chase_risk",    {}) or {}
    sd = dr.get("sell_decision", {}) or {}
    sl = dr.get("sector_leadership", {}) or {}  # None = unknown sector → treat as {}

    reasons: list[str] = []

    # ── Technical indicators ──────────────────────────────────────────────────
    if rsi_v > 75:
        reasons.append(f"RSI {rsi_v:.0f} 嚴重超買，短線回調壓力大")
    elif rsi_v > 70:
        reasons.append(f"RSI {rsi_v:.0f} 超買區，警惕獲利了結賣壓")
    if macd_h < 0:
        reasons.append("MACD 柱狀圖翻負，短線動能轉弱")
    if bb_pct > 0.95:
        reasons.append("價格逼近布林上軌，面臨技術阻力區")
    if bear_ma >= 3:
        reasons.append(f"{bear_ma}/{total_ma} 條均線空頭排列，均線壓制")
    if vol_ratio < 0.7 and (payload.get("change_pct") or 0) > 0:
        reasons.append("量縮上漲，上攻動能不足，漲勢可持續性存疑")
    if sma20 and price < sma20:
        reasons.append(f"收盤跌破 MA20（{sma20:.2f}），均線由支撐轉阻力")
    if score < 45:
        reasons.append(f"動能分數 {score:.0f}/100 偏低，趨勢轉弱中")

    # ── Phase 1 — Chase Risk ──────────────────────────────────────────────────
    cr_score = cr.get("score")
    cr_level = cr.get("level", "")
    if cr_score is not None and cr_score > 75:
        reasons.append(f"追價風險評分 {cr_score}/100【EXTREME】，追高後套牢機率高")
    elif cr_score is not None and cr_score > 50:
        reasons.append(f"追價風險評分 {cr_score}/100【HIGH】，建議等待回測再介入")
    for flag in cr.get("warning_flags", []):
        if flag not in ("超買",):          # avoid duplication with RSI reason
            reasons.append(f"技術警示：{flag}")

    # ── Phase 1 — Sell Decision ───────────────────────────────────────────────
    sd_decision = sd.get("decision", "NONE")
    if sd_decision in ("SELL", "STOP_LOSS", "ROTATE"):
        label   = sd.get("decision_label", sd_decision)
        reason0 = (sd.get("reasons") or [""])[0]
        reasons.append(f"賣出決策觸發【{label}】：{reason0}")
    elif sd_decision == "TRIM":
        reasons.append("持倉達獲利目標，建議分批減倉而非加碼")

    # ── Phase 1 — Sector Leadership ──────────────────────────────────────────
    sl_level = sl.get("level", "")
    if sl_level in ("WEAKENING", "LAGGING"):
        sector  = sl.get("sector", "所屬板塊")
        sl_label = sl.get("level_label", sl_level)
        reasons.append(f"{sector} 板塊強度【{sl_label}】，板塊資金不支撐個股上漲")

    if not reasons:
        reasons.append("目前無明確看空訊號，但仍需持續觀察")
    return reasons


def _build_kill_signal(ind: dict, ma: list, payload: dict,
                       price: float, sma20: float | None,
                       bb_lower: float | None) -> dict:
    """
    Build Kill Signal — specific observable conditions that invalidate
    the bullish thesis.  Returns {triggers, primary, kill_sentence}.
    """
    rsi_v     = ind.get("rsi") or 50
    macd_h    = ind.get("macd_hist") or 0
    vol_ratio = payload.get("volume_ratio", 1) or 1

    dr = payload.get("decision_results", {})
    sd = dr.get("sell_decision", {})
    sd_detail = sd.get("detail", {})

    triggers: list[str] = []

    # Price-level triggers (most actionable — go first)
    if sma20 and price > sma20:
        triggers.append(f"收盤跌破 MA20（{sma20:.2f}）且次日無法收復")
    elif sma20 and price <= sma20:
        triggers.append(f"MA20 反彈失敗後再次跌穿（{sma20:.2f}）")
    if bb_lower:
        triggers.append(f"放量跌破布林下軌（{bb_lower:.2f}）")

    # Indicator triggers
    if rsi_v > 60:
        triggers.append(f"RSI 自現位（{rsi_v:.0f}）向下跌破 50 中性線")
    if macd_h > 0:
        triggers.append("MACD 柱狀圖由正轉負（動能反轉確認）")

    # Phase 1 stop-loss price as trigger
    stop_price  = sd_detail.get("stop_price")
    trail_price = sd_detail.get("trail_price")
    if stop_price and stop_price > 0:
        triggers.append(f"現價跌破停損位 {stop_price:.2f}（固定 -8% 停損線）")
    elif trail_price and trail_price > 0:
        triggers.append(f"跌破移動停利線 {trail_price:.2f}（從高點回落超過 15%）")

    # Volume divergence
    if vol_ratio >= 1.5 and (payload.get("change_pct") or 0) > 0:
        triggers.append("量增卻伴隨長上影線收低，顯示主力出貨")

    # Fallback
    if not triggers:
        triggers.append("量能明顯萎縮且跌破近期 5 日低點")

    primary = triggers[0]
    return {
        "triggers":      triggers,
        "primary":       primary,
        "kill_sentence": f"若出現「{primary}」，原本看多判斷失效。",
    }


def _build_chase_risk_text(payload: dict, price: float,
                           sma20: float | None) -> str:
    """Format Chase Risk section from Phase 1 or MA deviation fallback."""
    cr = payload.get("decision_results", {}).get("chase_risk", {})
    cr_score = cr.get("score")
    cr_label = cr.get("level_label", "")
    cr_action = cr.get("suggested_action", "")
    cr_reasons = cr.get("reasons", [])

    if cr_score is not None:
        line = f"追價風險評分：{cr_score}/100【{cr_label}】"
        if cr_reasons:
            line += "  |  " + "；".join(cr_reasons[:2])
        if cr_action:
            line += f"\n  建議：{cr_action}"
        return line

    # Fallback: MA20 deviation only
    if sma20 and price > 0:
        dev = (price - sma20) / sma20 * 100
        if dev > 15:
            return f"現價距 MA20 偏離 +{dev:.1f}%，追價風險高，建議等待拉回後介入"
        if dev > 5:
            return f"現價距 MA20 偏離 +{dev:.1f}%，謹慎追價，可小量試單"
        return f"現價與 MA20 偏離合理（{dev:+.1f}%），追價風險相對可控"
    return "追價風險：無法評估（資料不足）"


def _build_sell_plan(payload: dict, levels: dict,
                     price: float, sma20: float | None,
                     bb_upper: float | None) -> str:
    """Format Sell Plan section from Phase 1 or key-level fallback."""
    sd        = payload.get("decision_results", {}).get("sell_decision", {})
    sd_detail = sd.get("detail", {})
    lines: list[str] = []

    stop_p   = sd_detail.get("stop_price")
    trail_p  = sd_detail.get("trail_price")
    profit_p = sd_detail.get("profit_price")
    pnl_pct  = sd_detail.get("pnl_pct")

    if stop_p   and stop_p   > 0: lines.append(f"固定停損：{stop_p:.2f}（成本 -8%）")
    if trail_p  and trail_p  > 0: lines.append(f"移動停利：{trail_p:.2f}（從高點 -15%）")
    if profit_p and profit_p > 0: lines.append(f"獲利目標：{profit_p:.2f}（成本 +20%），分批了結")
    if pnl_pct  is not None:      lines.append(f"現持倉損益：{pnl_pct:+.1f}%")

    resistances = levels.get("nearest_resistance", [])
    supports    = levels.get("nearest_support", [])
    if resistances:
        lines.append("壓力分批減倉：" + "、".join(str(r) for r in resistances[:2]))
    if supports:
        lines.append("支撐參考：" + "、".join(str(s) for s in supports[:2]) + "（跌破加速出場）")

    if not lines:
        if sma20:  lines.append(f"停損參考：跌破 MA20（{sma20:.2f}）")
        if bb_upper: lines.append(f"獲利目標：布林上軌 {bb_upper:.2f}")
        lines.append("建議設定 5–8% 固定停損，20% 移動停利")

    return "\n  ".join(lines)


def _format_structured(symbol: str, signal: str,
                        bull_case: list[str], bear_case: list[str],
                        chase_risk_text: str, sell_plan: str,
                        kill: dict, final_advice: str) -> str:
    """Render the 7-block structured analysis as a single markdown string."""
    bc_md  = "\n".join(f"  • {r}" for r in bull_case)
    bec_md = "\n".join(f"  • {r}" for r in bear_case)
    ks_md  = "\n".join(f"  • {t}" for t in kill["triggers"])
    return f"""\
**【總結判斷】**
  {symbol} 技術信號：{signal}

**【Bull Case 看多理由】**
{bc_md}

**【Bear Case 看空理由】**
{bec_md}

**【Chase Risk 追高風險】**
  {chase_risk_text}

**【Sell Plan 賣出計畫】**
  {sell_plan}

**【Kill Signal 判斷失效條件】**
{ks_md}

**【最終操作建議】**
  {final_advice}

{kill['kill_sentence']}"""


# ── Main analysis functions ───────────────────────────────────────────────────

def rule_based_analysis(payload: dict) -> dict:
    ind        = payload.get("indicators", {})
    ma         = payload.get("ma_analysis", [])
    symbol     = payload.get("symbol", "")
    price      = payload.get("price", 0)
    change_pct = payload.get("change_pct", 0)
    vol_ratio  = payload.get("volume_ratio", 1)
    score      = payload.get("momentum_score", 50)
    pct_from_high = payload.get("pct_from_high", 0)

    rsi_v   = ind.get("rsi")       or 50
    macd_h  = ind.get("macd_hist") or 0
    macd_v  = ind.get("macd")      or 0
    sig_v   = ind.get("macd_signal") or 0
    stoch_k = ind.get("stoch_k")   or 50
    stoch_d = ind.get("stoch_d")   or 50
    bb_pct  = ind.get("bb_pct_b")  or 0.5
    bb_upper = ind.get("bb_upper") or (price * 1.05 if price else 0)
    bb_lower = ind.get("bb_lower") or (price * 0.95 if price else 0)
    adx_v   = ind.get("adx")       or 0
    di_plus = ind.get("di_plus")   or 0
    di_minus = ind.get("di_minus") or 0

    sma20 = next((m["value"] for m in ma if "20" in m["label"]), None)
    sma50 = next((m["value"] for m in ma if "50" in m["label"]), None)

    signal, signal_class = _score_to_signal(score)
    levels = _support_resistance(price, bb_upper, bb_lower, sma20, sma50)
    bullish_count = sum(1 for m in ma if m.get("bullish"))
    total_ma = len(ma)

    # ── Existing indicator narrative (backward compat) ────────────────────────
    change_dir = "上漲" if change_pct >= 0 else "下跌"
    sign = "+" if change_pct >= 0 else ""
    summary_short = (
        f"{symbol} 今日{change_dir} {sign}{change_pct:.2f}%，"
        f"動能分數 {score:.0f}/100，信號：【{signal}】。"
        f"距 52 週高點 {pct_from_high:.1f}%，"
        f"{bullish_count}/{total_ma} 條均線多頭站位。"
    )

    lines = [
        f"**趨勢概覽**：{_ma_text(ma)}",
        f"**RSI 動能**：{_rsi_text(rsi_v)}",
        f"**MACD 訊號**：{_macd_text(macd_h, macd_v, sig_v)}",
        f"**布林通道**：{_bb_text(bb_pct, price, bb_upper, bb_lower)}",
        f"**隨機指標**：{_stoch_text(stoch_k, stoch_d)}",
        f"**ADX 趨勢強度**：{_adx_text(adx_v, di_plus, di_minus)}",
        f"**成交量**：{_vol_text(vol_ratio)}",
    ]
    confluence_items = []
    if rsi_v > 60 and macd_h > 0 and bullish_count >= 3:
        confluence_items.append("多指標共振看多")
    elif rsi_v < 40 and macd_h < 0 and bullish_count <= 2:
        confluence_items.append("多指標共振看空")
    if adx_v > 25 and di_plus > di_minus and rsi_v > 50:
        confluence_items.append("趨勢動能確認多頭")
    if stoch_k < 20 and rsi_v < 35:
        confluence_items.append("雙重超賣，反彈機率提升")
    if stoch_k > 80 and rsi_v > 70:
        confluence_items.append("雙重超買，注意回調風險")
    if vol_ratio >= 1.4 and macd_h > 0:
        confluence_items.append("量增價升，突破有效性較高")
    conf_text = "；".join(confluence_items) if confluence_items else "指標分歧，觀望為主"

    risk_items = []
    if rsi_v > 70:   risk_items.append("RSI 超買")
    if bb_pct > 0.9: risk_items.append("布林上軌阻力")
    if rsi_v < 30:   risk_items.append("RSI 超賣")
    if macd_h < 0 and adx_v > 25: risk_items.append("空頭趨勢加速")
    risk_text = "，".join(risk_items) if risk_items else "無明顯風險訊號"

    # ── Phase 2: Bear Case / Kill Signal ─────────────────────────────────────
    bull_case = _build_bull_case(ind, ma, payload, score)
    bear_case = _build_bear_case(ind, ma, payload, score, price, sma20, bb_lower)
    kill      = _build_kill_signal(ind, ma, payload, price, sma20, bb_lower)
    chase_risk_text = _build_chase_risk_text(payload, price, sma20)
    sell_plan_text  = _build_sell_plan(payload, levels, price, sma20, bb_upper)

    # Final advice
    if signal_class in ("bullish", "mild-bullish"):
        final_advice = f"整體偏多，{signal}。但須留意 Bear Case 中的風險。{kill['kill_sentence']}"
    elif signal_class in ("bearish", "mild-bearish"):
        final_advice = f"整體偏空，{signal}。建議降低部位或等待訊號好轉。"
    else:
        final_advice = f"中性觀望，等待方向明朗後再操作。"

    # Structured 7-block output (Phase 2 format)
    structured_text = _format_structured(
        symbol, signal, bull_case, bear_case,
        chase_risk_text, sell_plan_text, kill, final_advice,
    )

    return {
        # ── backward-compat fields ──────────────────────────────────────────
        "summary":          structured_text,   # now includes full 7-block
        "analysis":         "\n\n".join(lines),
        "confluence":       conf_text,
        "risk":             risk_text,
        "signal":           signal,
        "signal_class":     signal_class,
        "score":            score,
        "support_resistance": levels,
        "source":           "rule-based",
        # ── Phase 2 new fields ──────────────────────────────────────────────
        "bull_case":        bull_case,
        "bear_case":        bear_case,
        "kill_signal":      kill,
        "kill_sentence":    kill["kill_sentence"],
        "chase_risk_text":  chase_risk_text,
        "sell_plan":        sell_plan_text,
        "structured": {
            "summary_short":    summary_short,
            "bull_case":        bull_case,
            "bear_case":        bear_case,
            "chase_risk":       chase_risk_text,
            "sell_plan":        sell_plan_text,
            "kill_signal":      kill["triggers"],
            "kill_sentence":    kill["kill_sentence"],
            "final_advice":     final_advice,
        },
    }


def claude_analysis(payload: dict, api_key: str) -> dict:
    """Calls Claude API with 7-block forced output. Raises on failure."""
    import anthropic

    ind    = payload.get("indicators", {})
    ma     = payload.get("ma_analysis", [])
    symbol = payload.get("symbol", "")
    price  = payload.get("price", 0)

    ma_str = ", ".join(
        f"{m['label']}={m['value']:.2f}({'↑' if m['bullish'] else '↓'})"
        for m in ma
    )

    # Phase 1 context for Claude
    dr   = payload.get("decision_results", {})
    cr   = dr.get("chase_risk",    {}) or {}
    sd   = dr.get("sell_decision", {}) or {}
    # sector_leadership may be None (unknown sector) or a result dict or missing
    _sl_raw = dr.get("sector_leadership", "NOT_SET")
    sl   = _sl_raw if isinstance(_sl_raw, dict) else {}

    phase1_ctx = ""
    if cr.get("score") is not None:
        phase1_ctx += f"\n追價風險：{cr.get('score')}/100【{cr.get('level_label', '')}】"
        if cr.get("reasons"):
            phase1_ctx += f"（{cr['reasons'][0]}）"
    if sd.get("decision") not in (None, "NONE"):
        phase1_ctx += f"\n賣出決策：【{sd.get('decision_label', sd.get('decision', ''))}】（{(sd.get('reasons') or [''])[0]}）"
        sd_d = sd.get("detail", {})
        if sd_d.get("stop_price"):
            phase1_ctx += f"  停損位：{sd_d['stop_price']:.2f}"
        if sd_d.get("trail_price"):
            phase1_ctx += f"  移動停利：{sd_d['trail_price']:.2f}"
    if _sl_raw is None:
        # api_analyze explicitly set None → sector could not be identified
        phase1_ctx += "\n板塊強度：無法判斷所屬板塊（板塊強度資料不足）"
    elif sl.get("level") in ("WEAKENING", "LAGGING", "LEADING", "IMPROVING"):
        phase1_ctx += f"\n板塊強度：{sl.get('sector', '')} 【{sl.get('level_label', '')}】（評分 {sl.get('score', 'N/A')}）"

    prompt = f"""你是頂尖量化分析師，根據以下 {symbol} 的技術指標與決策引擎數據，給出**客觀雙向判斷**。
不要只輸出看多理由，必須提供多空兩面分析與具體失效條件。

【股票基本資訊】
股票：{symbol}  當前價：{price}
漲跌幅：{payload.get('change_pct', 0):+.2f}%  動能分數：{payload.get('momentum_score', 50):.0f}/100
距 52 週高點：{payload.get('pct_from_high', 0):.1f}%

【技術指標】
- RSI(14): {ind.get('rsi', 'N/A')}
- MACD: {ind.get('macd', 'N/A')} / Signal: {ind.get('macd_signal', 'N/A')} / Hist: {ind.get('macd_hist', 'N/A')}
- Stochastic K/D: {ind.get('stoch_k', 'N/A')}/{ind.get('stoch_d', 'N/A')}
- Bollinger %B: {ind.get('bb_pct_b', 'N/A')}（上軌:{ind.get('bb_upper', 'N/A')} 下軌:{ind.get('bb_lower', 'N/A')}）
- ADX: {ind.get('adx', 'N/A')}  +DI:{ind.get('di_plus', 'N/A')}  -DI:{ind.get('di_minus', 'N/A')}
- 成交量比率: {payload.get('volume_ratio', 'N/A')}x
- 均線: {ma_str}
{f'【決策引擎輸入】{phase1_ctx}' if phase1_ctx else ''}

請以繁體中文，嚴格按照以下格式輸出（每個區塊都必須有內容）：

**【總結判斷】**：（一句話，說明目前偏多/空/中性及主因）

**【Bull Case 看多理由】**：
（條列 2-4 點，具體引用上方數據）

**【Bear Case 看空理由】**：
（條列 2-4 點，必須提出真實的風險，不能寫「無風險」）

**【Chase Risk 追高風險】**：
（評估現在追價的風險：高/中/低，說明原因，給出合理介入方式）

**【Sell Plan 賣出計畫】**：
（條列停損位、移動停利位、獲利了結位，要有具體價格）

**【Kill Signal 判斷失效條件】**：
（條列 2-3 個具體可觀察的失效觸發條件，如「跌破 MA20 且量增」）

**【最終操作建議】**：
（一段話，整合以上分析，給出具體操作策略）

若出現「______」，原本看多判斷失效。（請填入最關鍵的失效條件）"""

    client = anthropic.Anthropic(api_key=api_key)
    msg = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=1200,
        messages=[{"role": "user", "content": prompt}],
    )
    text = msg.content[0].text

    # Best-effort section extraction for structured fields
    sections = _parse_claude_sections(text)

    score  = payload.get("momentum_score", 50)
    signal, signal_class = _score_to_signal(score)

    # Rule-based kill signal as fallback if Claude didn't fill the blank
    sma20   = next((m["value"] for m in ma if "20" in m["label"]), None)
    bb_lower = ind.get("bb_lower")
    rb_kill = _build_kill_signal(ind, ma, payload, price, sma20, bb_lower)

    kill_sentence = sections.get("kill_sentence") or rb_kill["kill_sentence"]

    return {
        # ── backward-compat ─────────────────────────────────────────────────
        "summary":          text,
        "analysis":         "",
        "confluence":       "",
        "risk":             "",
        "signal":           signal,
        "signal_class":     signal_class,
        "score":            score,
        "support_resistance": {},
        "source":           "claude-ai",
        # ── Phase 2 new fields ──────────────────────────────────────────────
        "bull_case":        sections.get("bull_case",  []),
        "bear_case":        sections.get("bear_case",  []),
        "kill_signal":      rb_kill,            # rule-based always populated
        "kill_sentence":    kill_sentence,
        "chase_risk_text":  sections.get("chase_risk", ""),
        "sell_plan":        sections.get("sell_plan",  ""),
        "structured":       sections,
    }


def _parse_claude_sections(text: str) -> dict:
    """
    Best-effort extraction of the 7 sections from Claude's formatted output.
    Returns a dict with keys matching the block labels.
    Falls back to empty strings / lists if a block is missing.
    """
    import re

    section_map = {
        "summary_short":  r"【總結判斷】",
        "bull_case_raw":  r"【Bull Case 看多理由】",
        "bear_case_raw":  r"【Bear Case 看空理由】",
        "chase_risk":     r"【Chase Risk 追高風險】",
        "sell_plan":      r"【Sell Plan 賣出計畫】",
        "kill_signal_raw":r"【Kill Signal 判斷失效條件】",
        "final_advice":   r"【最終操作建議】",
    }

    anchors = [(k, re.search(r"\*\*" + p + r"\*\*[：:]?", text))
               for k, p in section_map.items()]
    anchors = [(k, m) for k, m in anchors if m]

    sections: dict = {}
    for i, (key, match) in enumerate(anchors):
        start = match.end()
        end   = anchors[i + 1][1].start() if i + 1 < len(anchors) else len(text)
        block = text[start:end].strip()
        sections[key] = block

    def _to_list(raw: str) -> list[str]:
        items = []
        for line in raw.splitlines():
            line = line.strip().lstrip("-•·*123456789.）) ")
            if line:
                items.append(line)
        return items

    # Parse kill sentence: 若出現「...」，原本看多判斷失效。
    kill_m = re.search(r"若出現[「『]?(.+?)[」』]?[，,].*?失效", text)
    sections["kill_sentence"] = (
        f"若出現「{kill_m.group(1).strip()}」，原本看多判斷失效。"
        if kill_m else ""
    )

    sections["bull_case"]  = _to_list(sections.get("bull_case_raw",  ""))
    sections["bear_case"]  = _to_list(sections.get("bear_case_raw",  ""))
    sections["kill_signal"] = _to_list(sections.get("kill_signal_raw", ""))

    return sections


def analyze(payload: dict) -> dict:
    """
    Entry point called by app.py.
    Tries Claude first; falls back to rule_based_analysis on any failure.
    Both paths now produce the full Phase 2 structured output.
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if api_key:
        try:
            return claude_analysis(payload, api_key)
        except Exception as e:
            print(f"[analyzer] Claude API error → rule-based fallback: {e}")
    return rule_based_analysis(payload)
