"""
Top-Tier Decision Engine — Phase 13
Integrates all analysis modules into a final 10-dimension decision layer.
Outputs action levels: A1 / A2 / B1 / B2 / C1 / C2 / NO
"""


def run_top_tier(payload: dict) -> dict:
    """
    Input payload keys:
      symbol, price, change_pct, momentum_score, volume_ratio,
      pct_from_high, indicators (dict), sector_data (list),
      ai_result (dict), watchlist (list), nasdaq_meta (dict)

    Returns:
      ok, action_level, action_label, action_color, composite_score,
      dimensions (list), summary
    """
    try:
        sym        = str(payload.get("symbol", "") or "").upper()
        price      = float(payload.get("price", 0) or 0)
        mom_score  = float(payload.get("momentum_score", 50) or 50)
        vol_ratio  = float(payload.get("volume_ratio", 1) or 1)
        pct_hi     = float(payload.get("pct_from_high", 0) or 0)
        ind        = payload.get("indicators", {}) or {}
        sector_data = payload.get("sector_data", []) or []
        ai_result  = payload.get("ai_result", {}) or {}
        nasdaq_meta = payload.get("nasdaq_meta", {}) or {}
        watchlist  = payload.get("watchlist", []) or []

        dims = []

        # ── D1: 市場狀態 ──────────────────────────────────────────────
        mkt_regime  = str(nasdaq_meta.get("regime", "") or "")
        mkt_mom     = float(nasdaq_meta.get("momentum", 50) or 50)
        spy_chg     = float(nasdaq_meta.get("spy_change", 0) or 0)

        if mkt_regime in ("strong_bull", "bull") or mkt_mom >= 65:
            d1 = 78
        elif mkt_regime == "neutral" or 40 <= mkt_mom < 65:
            d1 = 52
        elif mkt_regime in ("bear", "strong_bear") or mkt_mom < 40:
            d1 = 28
        else:
            d1 = 50
        d1 = min(100, max(0, d1 + spy_chg * 1.5))

        dims.append({
            "id": "d1", "name": "市場狀態", "icon": "🌍",
            "score": round(d1),
            "grade": _grade(d1),
            "note": f"動能{round(mkt_mom)} · {_regime_label(mkt_regime)}",
            "weight": 0.15,
        })

        # ── D2: 板塊主線 ──────────────────────────────────────────────
        sym_sector   = _find_sector(sym, sector_data)
        sector_score = 50
        sector_name  = sym_sector or "未知板塊"

        if sector_data:
            sorted_sec = sorted(sector_data, key=lambda x: x.get("avgScore", 0), reverse=True)
            n = len(sorted_sec)
            pos = next((i for i, s in enumerate(sorted_sec) if s.get("sector") == sym_sector), n // 2)
            sector_score = max(10, min(95, 95 - (pos / max(1, n - 1)) * 80))

        ai_dr  = (ai_result.get("decision_results") or {})
        sl_ai  = str((ai_dr.get("sector_leadership") or {}).get("grade", "") or "")
        if sl_ai in ("A", "A+"):
            sector_score = min(100, sector_score + 15)
        elif sl_ai == "B":
            sector_score = min(100, sector_score + 5)
        elif sl_ai in ("C", "D"):
            sector_score = max(10, sector_score - 20)

        dims.append({
            "id": "d2", "name": "板塊主線", "icon": "📊",
            "score": round(sector_score),
            "grade": _grade(sector_score),
            "note": f"{sector_name} · AI評{sl_ai or '—'}",
            "weight": 0.12,
        })

        # ── D3: 個股品質 ──────────────────────────────────────────────
        rsi  = float(ind.get("rsi", 50) or 50)
        macd = float(ind.get("macd", ind.get("macd_line", 0)) or 0)
        adx  = float(ind.get("adx", 20) or 20)

        q = mom_score * 0.5
        if 45 <= rsi <= 68:
            q += 18
        elif rsi > 68:
            q += 6
        elif rsi < 32:
            q += 8
        if macd > 0:
            q += 10
        if adx >= 25:
            q += 12
        elif adx >= 20:
            q += 6
        q_score = min(100, max(0, q))

        dims.append({
            "id": "d3", "name": "個股品質", "icon": "⭐",
            "score": round(q_score),
            "grade": _grade(q_score),
            "note": f"RSI={round(rsi)} · MACD={'▲' if macd > 0 else '▼'} · ADX={round(adx)}",
            "weight": 0.15,
        })

        # ── D4: 追高風險 ──────────────────────────────────────────────
        chase    = float(ind.get("chaseRisk", 50) or 50)
        bear_p   = float(ind.get("bearPressure", 50) or 50)
        bb_pct_b = float(ind.get("bb_pct_b", 0.5) or 0.5)

        cr_raw   = chase * 0.55 + bear_p * 0.30 + max(0, bb_pct_b - 0.8) * 60
        cr_score = min(100, max(0, 100 - cr_raw))

        dims.append({
            "id": "d4", "name": "追高風險", "icon": "🎯",
            "score": round(cr_score),
            "grade": _grade(cr_score),
            "note": f"追高={round(chase)} · 空壓={round(bear_p)} · BB={round(bb_pct_b*100)}%",
            "weight": 0.12,
        })

        # ── D5: 賣出條件 ──────────────────────────────────────────────
        ai_sell      = (ai_dr.get("sell_decision") or {})
        sell_action  = str(ai_sell.get("action", "") or "")
        sk           = float(ind.get("stoch_k", ind.get("stochK", 50)) or 50)

        if sell_action in ("STRONG_SELL", "SELL"):
            sell_score = 15
        elif sell_action == "WATCH":
            sell_score = 45
        elif sell_action in ("HOLD", "BUY"):
            sell_score = 75
        else:
            sell_score = 55

        if sk > 80:
            sell_score = max(15, sell_score - 15)
        elif sk < 20:
            sell_score = min(85, sell_score + 10)

        dims.append({
            "id": "d5", "name": "賣出條件", "icon": "🚦",
            "score": round(sell_score),
            "grade": _grade(sell_score),
            "note": f"AI={sell_action or '—'} · Stoch={round(sk)}",
            "weight": 0.10,
        })

        # ── D6: 資金效率 ──────────────────────────────────────────────
        bb_upper = float(ind.get("bb_upper", ind.get("bbUpper", price * 1.02)) or price * 1.02)
        bb_lower = float(ind.get("bb_lower", ind.get("bbLower", price * 0.98)) or price * 0.98)
        bb_range = bb_upper - bb_lower

        bb_pos = ((price - bb_lower) / bb_range) if bb_range > 0 and price > 0 else 0.5

        cap_eff = 40
        if vol_ratio >= 2.0:
            cap_eff += 30
        elif vol_ratio >= 1.5:
            cap_eff += 20
        elif vol_ratio >= 1.0:
            cap_eff += 10

        if 0.25 <= bb_pos <= 0.72:
            cap_eff += 20
        elif bb_pos > 0.88:
            cap_eff -= 12

        pct_hi_abs = abs(pct_hi)
        if pct_hi_abs < 5:
            cap_eff += 10
        elif pct_hi_abs > 30:
            cap_eff -= 10

        cap_eff = min(100, max(0, cap_eff))

        dims.append({
            "id": "d6", "name": "資金效率", "icon": "💰",
            "score": round(cap_eff),
            "grade": _grade(cap_eff),
            "note": f"量比={round(vol_ratio, 1)}x · BB位={round(bb_pos*100)}%",
            "weight": 0.10,
        })

        # ── D7: 替代標的 ──────────────────────────────────────────────
        alt_score = 60
        alt_note  = "自選清單未提供"

        if watchlist:
            wl_scores = [
                float(w.get("score", 0) or 0)
                for w in watchlist
                if str(w.get("symbol", "")).upper() != sym
            ]
            if wl_scores:
                better = sum(1 for s in wl_scores if s > mom_score + 10)
                alt_score = max(20, 80 - better * 8)
                alt_note = f"優於本股有{better}檔" if better else "本股優於清單"

        dims.append({
            "id": "d7", "name": "替代標的", "icon": "🔄",
            "score": round(alt_score),
            "grade": _grade(alt_score),
            "note": alt_note,
            "weight": 0.08,
        })

        # ── D8: 部位大小 ──────────────────────────────────────────────
        ai_final = str(ai_result.get("final_call", "") or "")
        win_rate = 0.55
        if any(k in ai_final for k in ("strong", "強力", "最強")):
            win_rate = 0.65
        elif any(k in ai_final.lower() for k in ("buy", "進場", "買進", "突破")):
            win_rate = 0.58
        elif any(k in ai_final.lower() for k in ("sell", "賣出", "停損", "出場")):
            win_rate = 0.38

        rr_ratio = 2.0
        kelly_f  = max(0, min(0.25, (win_rate * rr_ratio - (1 - win_rate)) / rr_ratio))
        if d1 < 40:
            kelly_f = kelly_f * 0.7
        pos_score = round(kelly_f * 400)

        dims.append({
            "id": "d8", "name": "部位大小", "icon": "📦",
            "score": round(pos_score),
            "grade": _grade(pos_score),
            "note": f"建議≈{round(kelly_f*100, 1)}% (Kelly)",
            "weight": 0.08,
        })

        # ── D9: 風險預算 ──────────────────────────────────────────────
        rb = d1 * 0.40 + cr_score * 0.40 + sell_score * 0.20
        if d1 < 40:
            rb *= 0.72
        risk_budget = min(100, max(0, rb))

        dims.append({
            "id": "d9", "name": "風險預算", "icon": "🛡",
            "score": round(risk_budget),
            "grade": _grade(risk_budget),
            "note": f"市場{round(d1)} × 個股{round(cr_score)}",
            "weight": 0.05,
        })

        # ── D10: 最終操作等級 ─────────────────────────────────────────
        w_sum     = sum(d["weight"] for d in dims)
        composite = sum(d["score"] * d["weight"] for d in dims) / w_sum

        action_level, action_label, action_color = _action_level(
            composite, d1, q_score, cr_score, sell_score
        )

        dims.append({
            "id": "d10", "name": "最終操作等級", "icon": "🏆",
            "score": round(composite),
            "grade": action_level,
            "note": action_label,
            "weight": 0,
        })

        return {
            "ok": True,
            "symbol": sym,
            "price": price,
            "action_level": action_level,
            "action_label": action_label,
            "action_color": action_color,
            "composite_score": round(composite),
            "dimensions": dims,
            "summary": _build_summary(action_level, sym, round(composite)),
        }

    except Exception as e:
        import traceback
        traceback.print_exc()
        return {"ok": False, "error": str(e)}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _grade(score: float) -> str:
    if score >= 80: return "A"
    if score >= 65: return "B"
    if score >= 45: return "C"
    if score >= 30: return "D"
    return "F"


def _regime_label(r: str) -> str:
    m = {
        "strong_bull": "強勢多頭", "bull": "多頭",
        "neutral": "震盪中性", "bear": "空頭", "strong_bear": "強勢空頭",
    }
    return m.get(r, r or "未知")


def _find_sector(sym: str, sector_data: list) -> str:
    for sd in (sector_data or []):
        if sym in [str(s).upper() for s in (sd.get("symbols") or [])]:
            return sd.get("sector", "")
    return ""


def _action_level(composite, mkt, quality, chase_score, sell_score):
    if sell_score < 28 or (mkt < 28 and quality < 38):
        return "C1", "停損警戒", "#f85149"
    if mkt < 25 or composite < 24:
        return "C2", "空倉觀望", "#f0883e"
    if composite < 35:
        return "NO", "不操作", "#8b949e"
    if composite < 46 or (mkt < 45 and quality < 55):
        return "B2", "等待觀察", "#e3b341"
    if composite < 58 or chase_score < 38:
        return "B1", "謹慎試水", "#f0883e"
    if composite < 72 or mkt < 65:
        return "A2", "積極進場", "#3fb950"
    return "A1", "最優先進場", "#58a6ff"


def _build_summary(level: str, sym: str, score: int) -> str:
    texts = {
        "A1": f"{sym} 具備頂級進場條件：市場、板塊、個股三重共振（綜合分 {score}），優先配置。",
        "A2": f"{sym} 進場條件良好（{score}分），可積極建倉，注意板塊輪動節奏。",
        "B1": f"{sym} 條件尚可（{score}分），建議試水倉位，設緊停損觀察突破。",
        "B2": f"{sym} 信號混雜（{score}分），等待更明確的方向確認後再行動。",
        "C1": f"{sym} 出現停損信號（{score}分），若已持倉應考慮減倉或出場。",
        "C2": f"市場環境不佳（{score}分），建議保持空倉，等待環境改善。",
        "NO": f"{sym} 當前信號不足（{score}分），暫時觀望，不採取行動。",
    }
    return texts.get(level, f"{sym} 決策完成，等級={level}，綜合分={score}。")
