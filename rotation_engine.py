"""
Rotation Decision Engine — Phase 7
Capital efficiency ranking + drag score + alternative candidates + rotation decisions.
Pure functions only: no I/O, no DB, no side effects.

Public API
----------
calc_drag_score(ce_result, holding_days, sell_signal, sector_trend, benchmark_beat) -> dict
find_alternative_candidates(symbol, sector, peer_ohlcv_map, user_watchlist_scores) -> list
make_rotation_decision(ce_result, drag_result, alternatives, stop_price) -> dict
analyze_portfolio(positions, ohlcv_fn, bench_return_fn=None) -> dict
"""
from __future__ import annotations

import portfolio_engine as _pe
import sector_map        as _smap

# ── Constants ─────────────────────────────────────────────────────────────────

ROTATION_LABEL = {
    "KEEP":            "繼續持有",
    "WATCH":           "密切觀察",
    "TRIM":            "考慮減倉",
    "ROTATE_PARTIAL":  "部分轉倉",
    "ROTATE_FULL":     "全部轉倉",
    "STOP_LOSS":       "停損出場",
}

ROTATION_COLOR = {
    "KEEP":           "#3fb950",
    "WATCH":          "#58a6ff",
    "TRIM":           "#e3b341",
    "ROTATE_PARTIAL": "#f0883e",
    "ROTATE_FULL":    "#bc8cff",
    "STOP_LOSS":      "#f85149",
}

DRAG_LEVEL = {
    "LOW":     (0,  25),
    "MEDIUM":  (25, 50),
    "HIGH":    (50, 75),
    "EXTREME": (75, 101),
}


# ── Indicator helpers (pure Python, no pandas) ────────────────────────────────

def _sma(lst: list, n: int) -> float:
    tail = [v for v in lst[-n:] if v and v > 0]
    return sum(tail) / len(tail) if tail else 0.0


def _rsi(closes: list, period: int = 14) -> float:
    if len(closes) < period + 1:
        return 50.0
    gains = losses = 0.0
    for i in range(1, period + 1):
        d = closes[-period - 1 + i] - closes[-period - 2 + i]
        if d > 0:
            gains += d
        else:
            losses -= d
    gains /= period
    losses /= period
    if losses == 0:
        return 100.0
    rs = gains / losses
    return 100 - 100 / (1 + rs)


def _momentum_score(closes: list) -> int:
    """Simple 0–100 momentum score based on MA alignment and RSI."""
    if len(closes) < 20:
        return 50
    c = closes[-1]
    ma5  = _sma(closes, 5)
    ma20 = _sma(closes, 20)
    ma50 = _sma(closes[-50:] if len(closes) >= 50 else closes, min(50, len(closes)))
    rsi  = _rsi(closes)

    score = 50
    if c > ma20:       score += 10
    else:              score -= 10
    if ma5 > ma20:     score += 10
    else:              score -= 10
    if c > ma50:       score += 10
    else:              score -= 10
    if rsi > 60:       score += 10
    elif rsi < 40:     score -= 10
    if c > closes[-5]: score += 5
    else:              score -= 5

    return min(100, max(0, score))


def _chase_risk_score(closes: list) -> int:
    """Simplified chase risk 0–100."""
    if len(closes) < 20:
        return 30
    c    = closes[-1]
    ma20 = _sma(closes, 20)
    if ma20 <= 0:
        return 30
    pct_above = (c - ma20) / ma20 * 100
    if pct_above > 15:  return 85
    if pct_above > 10:  return 70
    if pct_above > 5:   return 55
    if pct_above > 0:   return 40
    return 20


# ── 1. Drag Score ─────────────────────────────────────────────────────────────

def calc_drag_score(
    ce_score: int,
    holding_days: int | None,
    pnl_pct: float,
    benchmark_return: float | None,
    sector_trend: str | None,         # LEADING | IMPROVING | NEUTRAL | WEAKENING | LAGGING
    sell_signal: str | None,          # ADD | HOLD | WATCH | TRIM | ROTATE | STOP_LOSS
    momentum_score: int = 50,
) -> dict:
    """
    Returns drag_score (0–100), drag_level, and drag_reasons.
    Higher drag_score → more capital drag → more urgency to rotate.

    holding_days 可為 None（買進日期缺漏或無法解析）——`portfolio_engine`
    刻意不假造天數。此處統一歸零，讓以天數為條件的扣分不觸發，
    避免每個呼叫端各自防護時漏掉（曾因此在 daily_report_engine 拋 TypeError）。
    """
    score = 0
    reasons: list[str] = []

    if holding_days is None:
        holding_days = 0

    # Holding too long without benchmark beat
    if holding_days >= 7 and benchmark_return is not None:
        alpha = pnl_pct - benchmark_return
        if alpha < -5:
            score += 20
            reasons.append(f"持有 {holding_days} 天，跑輸基準 {abs(alpha):.1f}%")
        elif alpha < 0:
            score += 10
            reasons.append(f"持有 {holding_days} 天，輕微跑輸基準")

    # Loss after 10+ days
    if holding_days >= 10 and pnl_pct < 0:
        pts = min(25, int(abs(pnl_pct) * 1.5))
        score += pts
        reasons.append(f"持有 {holding_days} 天仍虧損 {pnl_pct:.1f}%")

    # Low momentum
    if momentum_score < 35:
        score += 20
        reasons.append(f"動能分數低（{momentum_score}/100）")
    elif momentum_score < 50:
        score += 10
        reasons.append(f"動能偏弱（{momentum_score}/100）")

    # Sell signal
    _SELL_PTS = {"STOP_LOSS": 35, "ROTATE": 25, "TRIM": 15, "WATCH": 8, "HOLD": 0, "ADD": 0}
    sig_pts = _SELL_PTS.get(sell_signal or "HOLD", 0)
    if sig_pts:
        score += sig_pts
        reasons.append(f"賣出訊號：{sell_signal}")

    # Low CE score
    if ce_score is not None:
        if ce_score < 35:
            score += 20
            reasons.append(f"資金效率分數低（{ce_score}/100）")
        elif ce_score < 45:
            score += 10
            reasons.append(f"資金效率偏弱（{ce_score}/100）")

    # Sector trend
    _SECTOR_PTS = {"LAGGING": 20, "WEAKENING": 15, "NEUTRAL": 0, "IMPROVING": 0, "LEADING": 0}
    sec_pts = _SECTOR_PTS.get(sector_trend or "NEUTRAL", 0)
    if sec_pts:
        score += sec_pts
        reasons.append(f"板塊趨勢轉弱（{sector_trend}）")

    score = min(100, max(0, score))

    drag_level = "LOW"
    for lvl, (lo, hi) in DRAG_LEVEL.items():
        if lo <= score < hi:
            drag_level = lvl
            break

    return {
        "drag_score":  score,
        "drag_level":  drag_level,
        "drag_reasons": reasons or ["無明顯拖累資金因素"],
    }


# ── 2. Alternative Candidates ─────────────────────────────────────────────────

def find_alternative_candidates(
    symbol: str,
    sector: str | None,
    peer_scores: dict[str, int],      # {peer_symbol: momentum_score}
    current_momentum: int = 50,
    watchlist_scores: dict[str, int] | None = None,
) -> list[dict]:
    """
    Returns up to 5 alternative candidates, scored relative to current holding.
    Each candidate: {symbol, sector, momentum_score, relative_strength, reason}
    """
    candidates: list[dict] = []
    seen: set[str] = {symbol.upper()}

    # Sector peers that significantly outperform current
    for peer, pscore in sorted(peer_scores.items(), key=lambda x: -x[1]):
        if peer.upper() in seen:
            continue
        diff = pscore - current_momentum
        if diff >= 15:
            candidates.append({
                "symbol":            peer,
                "sector":            sector or "—",
                "momentum_score":    pscore,
                "relative_strength": f"+{diff}",
                "reason":            f"同板塊動能領先 +{diff} 分（{pscore}/100）",
                "source":            "sector_peer",
            })
            seen.add(peer.upper())
        if len(candidates) >= 3:
            break

    # Watchlist symbols that outperform
    if watchlist_scores:
        for wl_sym, wscore in sorted(watchlist_scores.items(), key=lambda x: -x[1]):
            if wl_sym.upper() in seen:
                continue
            diff = wscore - current_momentum
            if diff >= 10:
                candidates.append({
                    "symbol":            wl_sym,
                    "sector":            _smap.get_sector(wl_sym) or "—",
                    "momentum_score":    wscore,
                    "relative_strength": f"+{diff}",
                    "reason":            f"自選股中動能更強（+{diff} 分）",
                    "source":            "watchlist",
                })
                seen.add(wl_sym.upper())
            if len(candidates) >= 5:
                break

    # Fallback: benchmark ETFs if no candidates found
    if not candidates and sector:
        is_tw = symbol.upper().endswith(".TW") or symbol.upper().endswith(".TWO")
        fallback = "2330.TW" if is_tw else "QQQ"
        if fallback.upper() not in seen:
            candidates.append({
                "symbol":            fallback,
                "sector":            "ETF/指數",
                "momentum_score":    60,
                "relative_strength": "N/A",
                "reason":            f"可考慮轉換至{'台股龍頭' if is_tw else 'QQQ'} 等待更佳機會",
                "source":            "fallback_etf",
            })

    if not candidates:
        candidates.append({
            "symbol":            "—",
            "sector":            "—",
            "momentum_score":    None,
            "relative_strength": "—",
            "reason":            "暫無明顯更佳替代標的",
            "source":            "none",
        })

    return candidates[:5]


# ── 3. Rotation Decision ──────────────────────────────────────────────────────

def make_rotation_decision(
    symbol: str,
    ce_score: int,
    ce_level: str,
    drag_score: int,
    drag_level: str,
    alternatives: list[dict],
    momentum_score: int,
    chase_risk_score: int,
    sell_signal: str,
    stop_loss_breached: bool = False,
) -> dict:
    """
    Returns rotation_action, confidence, reason_summary.
    """
    # Rule 1: Stop loss breached
    if stop_loss_breached or sell_signal == "STOP_LOSS" or ce_level == "STOP_LOSS":
        action = "STOP_LOSS"
        confidence = "HIGH"
        summary = "持股跌破停損線或達到停損條件，建議立即出場"

    # Rule 2: High drag + good alternatives → full rotate
    elif drag_level in ("HIGH", "EXTREME") and any(
        c.get("source") != "none" and c.get("relative_strength") not in ("N/A", "—", None)
        for c in alternatives
    ):
        action = "ROTATE_FULL"
        confidence = "HIGH" if drag_level == "EXTREME" else "MEDIUM"
        best = alternatives[0]
        summary = f"資金拖累嚴重（drag={drag_score}），{best.get('symbol')} 動能更強，建議全部轉倉"

    # Rule 3: Moderate drag + alternatives → partial rotate
    elif drag_level == "MEDIUM" and ce_score is not None and ce_score < 50:
        action = "ROTATE_PARTIAL"
        confidence = "MEDIUM"
        summary = f"資金效率偏低（{ce_score}/100），可考慮部分轉倉至更強標的"

    # Rule 4: Sell signal ROTATE or TRIM
    elif sell_signal in ("ROTATE",):
        action = "ROTATE_PARTIAL"
        confidence = "MEDIUM"
        summary = "賣出模組建議換股，可考慮部分轉倉"

    elif sell_signal == "TRIM":
        action = "TRIM"
        confidence = "MEDIUM"
        summary = "賣出模組建議減倉，可鎖定部分獲利"

    # Rule 5: High chase risk → trim
    elif chase_risk_score >= 75:
        action = "TRIM"
        confidence = "HIGH"
        summary = f"追高風險分數高（{chase_risk_score}/100），建議減倉以降低風險"

    # Rule 6: Good score, beats benchmark → keep
    elif ce_score is not None and ce_score >= 55 and momentum_score >= 50:
        action = "KEEP"
        confidence = "HIGH"
        summary = "資金效率良好，動能持續，繼續持有"

    # Rule 7: Borderline → watch
    else:
        action = "WATCH"
        confidence = "LOW"
        summary = "目前狀況中性，密切觀察動能變化"

    return {
        "rotation_action":             action,
        "rotation_label":              ROTATION_LABEL.get(action, action),
        "rotation_color":              ROTATION_COLOR.get(action, "#8b949e"),
        "confidence":                  confidence,
        "from_symbol":                 symbol,
        "to_candidates":               alternatives,
        "reason_summary":              summary,
        "suggested_allocation_change": _alloc_change(action),
        "disclaimer":                  "此為決策輔助，不代表自動下單。",
    }


def _alloc_change(action: str) -> str:
    return {
        "KEEP":           "維持現有倉位",
        "WATCH":          "維持，設置警戒線",
        "TRIM":           "減倉 25–50%",
        "ROTATE_PARTIAL": "減倉 50%，轉至替代標的",
        "ROTATE_FULL":    "全部出場，轉至替代標的",
        "STOP_LOSS":      "全部停損出場",
    }.get(action, "依個人判斷")


# ── 4. Full Portfolio Analysis ────────────────────────────────────────────────

def analyze_portfolio(
    positions: list[dict],
    ohlcv_fn,               # callable(symbol) -> dict | None
    bench_return: float | None = None,    # pre-computed benchmark return
    sector_ohlcv_map: dict | None = None, # {sector: {sym: ohlcv}} for peer scoring
    watchlist: list[str] | None = None,
) -> dict:
    """
    Full portfolio rotation analysis.
    Returns per-position analyses + portfolio-level summary.
    """
    if not positions:
        return _empty_portfolio()

    results: list[dict] = []
    watchlist_scores: dict[str, int] = {}

    # Pre-compute watchlist momentum scores
    if watchlist:
        for wl_sym in watchlist[:20]:
            try:
                ohlcv = ohlcv_fn(wl_sym)
                if ohlcv and ohlcv.get("closes"):
                    watchlist_scores[wl_sym.upper()] = _momentum_score(ohlcv["closes"])
            except Exception:
                pass

    for pos in positions:
        symbol   = (pos.get("symbol") or pos.get("sym", "")).upper().strip()
        cost     = float(pos.get("cost") or pos.get("entry") or 0)
        qty      = float(pos.get("qty") or pos.get("shares") or 0)
        buy_date = pos.get("buy_date") or pos.get("buyDate") or ""
        stop_loss = float(pos.get("stop_loss") or pos.get("stopLoss") or 0)

        if not symbol or cost <= 0:
            continue

        try:
            ohlcv = ohlcv_fn(symbol)
            if not ohlcv or not ohlcv.get("closes"):
                results.append(_error_position(symbol))
                continue

            closes = ohlcv["closes"]
            current_price = closes[-1]
            pnl_pct = (current_price - cost) / cost * 100

            # Capital efficiency
            holding = {"symbol": symbol, "cost": cost, "qty": qty, "buy_date": buy_date}
            ce = _pe.calc_capital_efficiency(holding, ohlcv, benchmark_return=bench_return)
            ce_score   = ce.get("score") or 50
            ce_level   = ce.get("level", "HOLD")
            # 可能為 None（買進日期缺漏或無法解析）；calc_drag_score 會自行歸零。
            # 注意 .get(key, default) 在鍵存在但值為 None 時不會套用 default。
            holding_days = ce.get("detail", {}).get("holding_days")
            sell_signal  = ce_level  # CE level == sell signal for drag purposes

            # Momentum + chase risk
            mom_score   = _momentum_score(closes)
            chase_score = _chase_risk_score(closes)

            # Sector trend
            sector = _smap.get_sector(symbol)
            sector_trend = _get_sector_trend(sector, sector_ohlcv_map)

            # Peer scores for alternatives
            peer_scores: dict[str, int] = {}
            if sector:
                peers = _smap.get_sector_symbols(sector)
                for peer in peers[:8]:
                    if peer.upper() == symbol:
                        continue
                    try:
                        if sector_ohlcv_map and sector in sector_ohlcv_map and peer in sector_ohlcv_map[sector]:
                            poc = sector_ohlcv_map[sector][peer]
                        else:
                            poc = ohlcv_fn(peer)
                        if poc and poc.get("closes"):
                            peer_scores[peer] = _momentum_score(poc["closes"])
                    except Exception:
                        pass

            # Stop-loss check
            stop_breached = bool(stop_loss > 0 and current_price < stop_loss)

            # Drag score
            drag = calc_drag_score(
                ce_score=ce_score,
                holding_days=holding_days,
                pnl_pct=pnl_pct,
                benchmark_return=bench_return,
                sector_trend=sector_trend,
                sell_signal=sell_signal,
                momentum_score=mom_score,
            )

            # Alternatives
            alts = find_alternative_candidates(
                symbol=symbol,
                sector=sector,
                peer_scores=peer_scores,
                current_momentum=mom_score,
                watchlist_scores=watchlist_scores,
            )

            # Rotation decision
            rot = make_rotation_decision(
                symbol=symbol,
                ce_score=ce_score,
                ce_level=ce_level,
                drag_score=drag["drag_score"],
                drag_level=drag["drag_level"],
                alternatives=alts,
                momentum_score=mom_score,
                chase_risk_score=chase_score,
                sell_signal=sell_signal,
                stop_loss_breached=stop_breached,
            )

            results.append({
                "symbol":                symbol,
                "cost":                  cost,
                "qty":                   qty,
                "current_price":         round(current_price, 4),
                "unrealized_pnl":        round((current_price - cost) * qty, 2),
                "unrealized_pnl_pct":    round(pnl_pct, 2),
                "holding_return_pct":    round(pnl_pct, 2),   # alias, matches API spec
                "holding_days":          holding_days,
                "benchmark_return_pct":  bench_return,
                "relative_to_benchmark": round(pnl_pct - bench_return, 2) if bench_return is not None else None,
                "momentum_score":        mom_score,
                "chase_risk_score":      chase_score,
                "capital_efficiency_score": ce_score,
                "capital_efficiency_level": ce_level,
                "alternative_candidates": [],          # filled after build_summary
                "sell_signal":           sell_signal,
                "sector":                sector,
                "sector_trend":          sector_trend,
                **drag,
                **rot,
                "ce_detail":             ce,
                "ok":                    True,
            })

        except Exception as exc:
            results.append({**_error_position(symbol), "error": str(exc)})

    if not results:
        return _empty_portfolio()

    return _build_summary(results)


def _get_sector_trend(sector: str | None, sector_ohlcv_map: dict | None) -> str | None:
    if not sector or not sector_ohlcv_map or sector not in sector_ohlcv_map:
        return None
    try:
        import decision_engine as _de
        sl = _de.run_sector_leadership(sector, sector_ohlcv_map[sector])
        return sl.get("level")
    except Exception:
        return None


def _build_summary(results: list[dict]) -> dict:
    ok_results = [r for r in results if r.get("ok")]
    if not ok_results:
        return {"positions": results, "summary": {}, "ok": False}

    scores    = [r["capital_efficiency_score"] for r in ok_results if r.get("capital_efficiency_score") is not None]
    drag_scores = [r["drag_score"] for r in ok_results if r.get("drag_score") is not None]

    portfolio_score = round(sum(scores) / len(scores), 1) if scores else 0
    portfolio_drag  = round(sum(drag_scores) / len(drag_scores), 1) if drag_scores else 0

    by_action: dict[str, list[str]] = {
        "KEEP": [], "WATCH": [], "TRIM": [], "ROTATE_PARTIAL": [], "ROTATE_FULL": [], "STOP_LOSS": [],
    }
    for r in ok_results:
        a = r.get("rotation_action", "WATCH")
        by_action.setdefault(a, []).append(r["symbol"])

    best  = max(ok_results, key=lambda x: x.get("capital_efficiency_score", 0))
    worst = min(ok_results, key=lambda x: x.get("capital_efficiency_score", 100))

    # Efficiency ranking
    ranking_map = {}
    for r in ok_results:
        s = r.get("capital_efficiency_score", 50)
        d = r.get("drag_score", 0)
        if r.get("rotation_action") == "STOP_LOSS":   ranking_map[r["symbol"]] = "建議停損"
        elif r.get("rotation_action") in ("ROTATE_FULL","ROTATE_PARTIAL"): ranking_map[r["symbol"]] = "建議轉倉"
        elif d >= 50 or s < 40:     ranking_map[r["symbol"]] = "拖累資金"
        elif s < 55:                ranking_map[r["symbol"]] = "效率偏弱"
        elif s >= 70:               ranking_map[r["symbol"]] = "資金效率最佳"
        else:                       ranking_map[r["symbol"]] = "可續抱"

    for r in ok_results:
        r["efficiency_rank"] = ranking_map.get(r["symbol"], "可續抱")
        # Copy to_candidates → alternative_candidates for API spec alignment
        r["alternative_candidates"] = r.get("to_candidates", [])

    # Build structured rotation_recommendations list (all positions, sorted by urgency)
    _ACTION_PRIORITY = {"STOP_LOSS": 0, "ROTATE_FULL": 1, "ROTATE_PARTIAL": 2,
                        "TRIM": 3, "WATCH": 4, "KEEP": 5}
    rotation_recommendations = sorted(
        [
            {
                "symbol":             r["symbol"],
                "action":             r["rotation_action"],
                "action_label":       r["rotation_label"],
                "action_color":       r["rotation_color"],
                "confidence":         r.get("confidence", "LOW"),
                "reason":             r.get("reason_summary", ""),
                "allocation_change":  r.get("suggested_allocation_change", ""),
                "drag_score":         r.get("drag_score", 0),
                "capital_efficiency_score": r.get("capital_efficiency_score"),
                "alternatives":       [
                    c for c in r.get("to_candidates", []) if c.get("symbol") != "—"
                ],
                "disclaimer":         "此為決策輔助，不代表自動下單。",
            }
            for r in ok_results
        ],
        key=lambda x: _ACTION_PRIORITY.get(x["action"], 9),
    )

    summary = {
        "portfolio_efficiency_score": portfolio_score,
        "portfolio_drag_score":       portfolio_drag,
        "best_position":              best["symbol"],
        "worst_position":             worst["symbol"],
        "total_positions":            len(ok_results),
        "positions_to_hold":          by_action.get("KEEP", []) + by_action.get("WATCH", []),
        "positions_to_trim":          by_action.get("TRIM", []),
        "positions_to_rotate":        by_action.get("ROTATE_PARTIAL", []) + by_action.get("ROTATE_FULL", []),
        "positions_to_stop_loss":     by_action.get("STOP_LOSS", []),
        "rotation_recommendations":   rotation_recommendations,
        "cash_redeployment_suggestions": _cash_suggestions(results),
        "disclaimer":                 "此為決策輔助，不代表自動下單。",
    }

    return {
        "ok":        True,
        "positions": results,
        "summary":   summary,
    }


def _cash_suggestions(results: list[dict]) -> list[str]:
    sug = []
    stop_syms = [r["symbol"] for r in results if r.get("rotation_action") == "STOP_LOSS"]
    rot_syms  = [r["symbol"] for r in results if "ROTATE" in r.get("rotation_action", "")]
    if stop_syms:
        sug.append(f"停損 {', '.join(stop_syms)} 釋出資金，尋找更強標的")
    if rot_syms:
        best_alts = []
        for r in results:
            if "ROTATE" in r.get("rotation_action", ""):
                for alt in r.get("to_candidates", [])[:1]:
                    if alt.get("symbol") not in ("—", None):
                        best_alts.append(alt["symbol"])
        if best_alts:
            sug.append(f"轉倉候選：{', '.join(dict.fromkeys(best_alts))}")
    if not sug:
        sug.append("投組整體效率良好，維持現有配置")
    return sug


def _error_position(symbol: str) -> dict:
    return {
        "symbol":              symbol,
        "ok":                  False,
        "error":               "無法取得行情資料",
        "drag_score":          0,
        "drag_level":          "LOW",
        "drag_reasons":        ["無法取得資料"],
        "rotation_action":     "WATCH",
        "rotation_label":      ROTATION_LABEL["WATCH"],
        "rotation_color":      ROTATION_COLOR["WATCH"],
        "confidence":          "LOW",
        "reason_summary":      "無法取得行情，暫時觀察",
        "to_candidates":       [],
        "efficiency_rank":     "資料不足",
        "disclaimer":          "此為決策輔助，不代表自動下單。",
    }


def _empty_portfolio() -> dict:
    return {
        "ok":        True,
        "positions": [],
        "summary": {
            "portfolio_efficiency_score": 0,
            "portfolio_drag_score":       0,
            "best_position":              "—",
            "worst_position":             "—",
            "total_positions":            0,
            "positions_to_hold":          [],
            "positions_to_trim":          [],
            "positions_to_rotate":        [],
            "positions_to_stop_loss":     [],
            "cash_redeployment_suggestions": ["尚未新增持倉"],
            "disclaimer": "此為決策輔助，不代表自動下單。",
        },
    }
