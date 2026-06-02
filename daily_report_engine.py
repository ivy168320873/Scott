"""
Daily Report Engine — Phase 8
pre_market / intraday / post_market reports.

Public API
----------
generate_report(report_type, ohlcv_fn, positions=[], watchlist=[], bench_sym='QQQ', ai_fn=None) -> dict
save_report(report) -> int
get_latest(report_type=None) -> dict | None
get_history(limit=30) -> list[dict]
format_line(report) -> str
format_email_subject(report) -> str
format_email_body(report) -> str
init_db()
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timezone

import decision_engine as _de
import sector_map as _smap
import alert_history as _ah
import portfolio_engine as _pe
import rotation_engine as _re
try:
    import data_provider as _dp
    _HAS_DP = True
except ImportError:
    _HAS_DP = False

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REPORT_TYPES = ("pre_market", "intraday", "post_market")

_REPORT_TYPE_LABEL = {
    "pre_market":  "盤前快報",
    "intraday":    "盤中快報",
    "post_market": "盤後報告",
}

_REGIME_EMOJI = {
    "bull":     "🟢",
    "sideways": "🟡",
    "bear":     "🔴",
}

# Top-5 representative sectors (limit API calls)
_TOP_SECTORS = list(_smap.SECTOR_SYMBOLS.keys())[:5]

_DB_PATH = os.environ.get("USER_DATA_DB", "./user_data.db")
_LOCK = threading.Lock()

DISCLAIMER = "此為決策輔助，不代表自動下單。"


# ---------------------------------------------------------------------------
# DB bootstrap
# ---------------------------------------------------------------------------

def init_db() -> None:
    """Create daily_reports table if it doesn't exist."""
    with _lock_conn() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS daily_reports (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                report_type     TEXT NOT NULL,
                generated_at    TEXT NOT NULL,
                report_json     TEXT NOT NULL,
                market_state    TEXT,
                sa_alert_count  INTEGER DEFAULT 0
            )
        """)
        con.execute(
            "CREATE INDEX IF NOT EXISTS dr_type_ts ON daily_reports(report_type, generated_at)"
        )


def _lock_conn():
    """Context manager: acquire lock + open SQLite connection."""
    class _CM:
        def __enter__(self):
            _LOCK.acquire()
            self._con = sqlite3.connect(_DB_PATH, check_same_thread=False)
            self._con.row_factory = sqlite3.Row
            return self._con

        def __exit__(self, *_):
            self._con.commit()
            self._con.close()
            _LOCK.release()

    return _CM()


# ---------------------------------------------------------------------------
# Private helpers — indicator math (no external deps)
# ---------------------------------------------------------------------------

def _sma(lst: list, n: int) -> float:
    tail = [v for v in lst[-n:] if v and v > 0]
    return sum(tail) / len(tail) if tail else 0.0


# ---------------------------------------------------------------------------
# Private builders
# ---------------------------------------------------------------------------

def _build_market_status(ohlcv_fn) -> dict:
    """
    Fetch SPY + QQQ, compute regime.
    Uses data_provider.market_state when available (real data, is_demo flag).
    Falls back to manual compute via ohlcv_fn otherwise.
    """
    if _HAS_DP:
        return _dp.market_state(ohlcv_fn)

    # Manual fallback
    indices = []
    for sym in ("SPY", "QQQ"):
        try:
            ohlcv = ohlcv_fn(sym)
            if not ohlcv or not ohlcv.get("closes") or len(ohlcv["closes"]) < 22:
                indices.append({
                    "symbol": sym, "change_1d_pct": 0.0,
                    "vs_ma20": "unknown", "momentum_score": 50,
                    "is_demo": True,
                })
                continue
            closes = ohlcv["closes"]
            change_1d = round((closes[-1] - closes[-2]) / closes[-2] * 100, 2) if closes[-2] else 0.0
            change_5d = round((closes[-1] - closes[-6]) / closes[-6] * 100, 2) if len(closes) >= 6 and closes[-6] else 0.0
            ma20 = _sma(closes, 20)
            vs_ma20 = "above" if (ma20 > 0 and closes[-1] > ma20) else "below"
            mom = _re._momentum_score(closes)
            indices.append({
                "symbol":         sym,
                "price":          round(closes[-1], 2),
                "change_1d_pct":  change_1d,
                "change_5d_pct":  change_5d,
                "vs_ma20":        vs_ma20,
                "ma20":           round(ma20, 2),
                "momentum_score": mom,
                "is_demo":        ohlcv.get("is_demo", False),
            })
        except Exception:
            indices.append({
                "symbol": sym, "change_1d_pct": 0.0,
                "vs_ma20": "unknown", "momentum_score": 50,
                "is_demo": True,
            })

    above_count = sum(1 for i in indices if i["vs_ma20"] == "above")
    avg_mom = sum(i.get("momentum_score", 50) for i in indices) / max(len(indices), 1)
    avg_chg = sum(i["change_1d_pct"] for i in indices) / max(len(indices), 1)
    avg_5d  = sum(i.get("change_5d_pct", 0) for i in indices) / max(len(indices), 1)
    any_demo = any(i.get("is_demo") for i in indices)

    if above_count == len(indices) and avg_mom > 55 and avg_chg >= 0:
        overall, regime = "偏多", "bull"
    elif above_count == 0 and avg_mom < 45 and avg_chg <= 0:
        overall, regime = "偏弱", "bear"
    elif avg_chg <= -1.5 or avg_5d <= -3.0:
        overall, regime = "風險升高", "risk_on"
    else:
        overall, regime = "中性", "sideways"

    return {
        "overall":           overall,
        "indices":           indices,
        "regime":            regime,
        "avg_change_1d_pct": round(avg_chg, 2),
        "avg_change_5d_pct": round(avg_5d, 2),
        "is_demo":           any_demo,
    }


def _build_sector_summary(ohlcv_fn) -> dict:
    """
    Run sector leadership for top-5 sectors.
    Use the sector's first symbol as representative when full data isn't available.
    Returns leaders, laggards, rankings.
    """
    rankings = []
    for sector in _TOP_SECTORS:
        symbols = _smap.get_sector_symbols(sector)
        if not symbols:
            continue
        # Build stocks_ohlcv map — use first symbol as proxy; try up to 3
        stocks_ohlcv: dict = {}
        rep_sym = None
        for sym in symbols[:3]:
            try:
                ohlcv = ohlcv_fn(sym)
                if ohlcv and ohlcv.get("closes") and len(ohlcv["closes"]) >= 20:
                    stocks_ohlcv[sym] = ohlcv
                    if rep_sym is None:
                        rep_sym = sym
            except Exception:
                pass

        if not stocks_ohlcv:
            rankings.append({
                "sector": sector, "score": None,
                "trend": "NEUTRAL", "top_symbol": symbols[0],
            })
            continue

        try:
            sl = _de.run_sector_leadership(sector, stocks_ohlcv)
            score = sl.get("score") or 50
            trend = sl.get("level", "NEUTRAL")
        except Exception:
            score = 50
            trend = "NEUTRAL"

        # Momentum of representative symbol
        top_sym = rep_sym or symbols[0]

        rankings.append({
            "sector":     sector,
            "score":      score,
            "trend":      trend,
            "top_symbol": top_sym,
        })

    rankings_sorted = sorted(
        [r for r in rankings if r.get("score") is not None],
        key=lambda x: -(x["score"] or 0),
    )
    leaders  = [r for r in rankings_sorted if r["trend"] in ("LEADING", "IMPROVING")][:3]
    laggards = [r for r in rankings_sorted[::-1] if r["trend"] in ("LAGGING", "WEAKENING")][:3]

    return {
        "leaders":  leaders,
        "laggards": laggards,
        "rankings": rankings_sorted,
    }


def _build_portfolio_summary(positions: list, ohlcv_fn) -> dict:
    """
    Calls portfolio_engine.calc_capital_efficiency per position.
    Returns total, efficiency_score_avg, drag_score_avg, risk_positions, strong_positions.
    """
    if not positions:
        return {
            "total": 0,
            "efficiency_score_avg": None,
            "drag_score_avg": None,
            "risk_positions": [],
            "strong_positions": [],
            "position_details": [],
        }

    scores = []
    drag_scores = []
    risk_positions = []
    strong_positions = []
    position_details = []

    for pos in positions:
        symbol = (pos.get("symbol") or pos.get("sym", "")).upper().strip()
        cost   = float(pos.get("cost") or pos.get("entry") or 0)
        qty    = float(pos.get("qty") or pos.get("shares") or 0)
        buy_date = pos.get("buy_date") or pos.get("buyDate") or ""

        if not symbol or cost <= 0:
            continue

        try:
            ohlcv = ohlcv_fn(symbol)
            if not ohlcv or not ohlcv.get("closes"):
                position_details.append({
                    "symbol": symbol, "level": "HOLD",
                    "score": None, "ok": False,
                })
                continue

            holding = {
                "symbol": symbol, "cost": cost,
                "qty": qty, "buy_date": buy_date,
            }
            ce = _pe.calc_capital_efficiency(holding, ohlcv)
            ce_score = ce.get("score")
            ce_level = ce.get("level", "HOLD")

            # Simple drag score via rotation_engine helper
            closes = ohlcv["closes"]
            mom = _re._momentum_score(closes)
            holding_days = ce.get("detail", {}).get("holding_days", 30)
            pnl_pct = ce.get("detail", {}).get("pnl_pct", 0.0)
            drag = _re.calc_drag_score(
                ce_score=ce_score or 50,
                holding_days=holding_days,
                pnl_pct=pnl_pct,
                benchmark_return=None,
                sector_trend=None,
                sell_signal=ce_level,
                momentum_score=mom,
            )

            d_score = drag.get("drag_score", 0)
            if ce_score is not None:
                scores.append(ce_score)
            drag_scores.append(d_score)

            detail = {
                "symbol":    symbol,
                "level":     ce_level,
                "score":     ce_score,
                "drag_score": d_score,
                "pnl_pct":   pnl_pct,
                "ok":        True,
            }
            position_details.append(detail)

            if ce_level in ("STOP_LOSS", "ROTATE") or d_score >= 60:
                risk_positions.append(symbol)
            elif ce_level in ("ADD", "HOLD") and (ce_score or 0) >= 60:
                strong_positions.append(symbol)

        except Exception:
            position_details.append({
                "symbol": symbol, "level": "HOLD",
                "score": None, "ok": False,
            })

    efficiency_avg = round(sum(scores) / len(scores), 1) if scores else None
    drag_avg = round(sum(drag_scores) / len(drag_scores), 1) if drag_scores else None

    return {
        "total":               len(positions),
        "efficiency_score_avg": efficiency_avg,
        "drag_score_avg":      drag_avg,
        "risk_positions":      risk_positions,
        "strong_positions":    strong_positions,
        "position_details":    position_details,
    }


def _build_alerts_summary(hours: int = 24) -> dict:
    """
    Calls alert_history.get_recent(), returns total, s_count, a_count, urgent.
    """
    try:
        _ah.init_db()
        alerts = _ah.get_recent(hours=hours)
    except Exception:
        alerts = []

    s_count = sum(1 for a in alerts if a.get("level") == "S")
    a_count = sum(1 for a in alerts if a.get("level") == "A")

    # Urgent = top 5 S/A alerts (S first)
    urgent = sorted(
        [a for a in alerts if a.get("level") in ("S", "A")],
        key=lambda x: (0 if x.get("level") == "S" else 1, x.get("created_at", "")),
    )[:5]

    return {
        "total":   len(alerts),
        "s_count": s_count,
        "a_count": a_count,
        "urgent":  urgent,
    }


def _build_do_not_chase_list(symbols: list, ohlcv_fn) -> list:
    """
    Compute chase_risk_score for each symbol.
    Return symbols where chase_risk_score >= 65 (do_not_chase).
    """
    do_not_chase = []
    seen: set = set()
    for sym in symbols:
        sym_up = sym.upper().strip()
        if not sym_up or sym_up in seen:
            continue
        seen.add(sym_up)
        try:
            ohlcv = ohlcv_fn(sym_up)
            if not ohlcv or not ohlcv.get("closes") or len(ohlcv["closes"]) < 20:
                continue
            chase_score = _re._chase_risk_score(ohlcv["closes"])
            if chase_score >= 65:
                do_not_chase.append({
                    "symbol":      sym_up,
                    "chase_score": chase_score,
                })
        except Exception:
            pass
    return do_not_chase


def _build_watchlist_candidates(watchlist: list, ohlcv_fn, do_not_chase: list) -> list:
    """
    Compute momentum_score for each watchlist symbol.
    Return top 5 with score >= 60 and NOT in do_not_chase.
    """
    dnc_symbols = {d["symbol"] for d in do_not_chase}
    candidates = []
    for sym in watchlist:
        sym_up = sym.upper().strip()
        if not sym_up or sym_up in dnc_symbols:
            continue
        try:
            ohlcv = ohlcv_fn(sym_up)
            if not ohlcv or not ohlcv.get("closes") or len(ohlcv["closes"]) < 20:
                continue
            mom = _re._momentum_score(ohlcv["closes"])
            if mom >= 60:
                candidates.append({"symbol": sym_up, "momentum_score": mom})
        except Exception:
            pass

    candidates.sort(key=lambda x: -x["momentum_score"])
    return candidates[:5]


def _build_kill_signal_watch(positions: list, ohlcv_fn) -> list:
    """
    Run decision_engine.run_sell_decision per position.
    Capture STOP_LOSS/ROTATE signals.
    """
    kill_signals = []
    for pos in positions:
        symbol = (pos.get("symbol") or pos.get("sym", "")).upper().strip()
        cost   = float(pos.get("cost") or pos.get("entry") or 0)
        if not symbol or cost <= 0:
            continue
        try:
            ohlcv = ohlcv_fn(symbol)
            if not ohlcv or not ohlcv.get("closes"):
                continue
            sell = _de.run_sell_decision(ohlcv, cost=cost)
            decision = sell.get("decision", "NONE")
            if decision in ("STOP_LOSS", "ROTATE"):
                kill_signals.append({
                    "symbol":   symbol,
                    "decision": decision,
                    "reason":   sell.get("reasons", [""])[0],
                    "urgency":  "HIGH" if decision == "STOP_LOSS" else "MEDIUM",
                })
        except Exception:
            pass
    return kill_signals


def _build_rotation_suggestions(positions: list, ohlcv_fn) -> list:
    """
    Call rotation_engine.analyze_portfolio, extract positions with
    ROTATE_FULL / ROTATE_PARTIAL / STOP_LOSS rotation_action.
    """
    if not positions:
        return []
    try:
        result = _re.analyze_portfolio(positions, ohlcv_fn)
        pos_results = result.get("positions", [])
        actionable = [
            p for p in pos_results
            if p.get("ok") and p.get("rotation_action") in (
                "ROTATE_FULL", "ROTATE_PARTIAL", "STOP_LOSS"
            )
        ]
        return [
            {
                "symbol":          p["symbol"],
                "action":          p["rotation_action"],
                "action_label":    p.get("rotation_label", p["rotation_action"]),
                "reason":          p.get("reason_summary", ""),
                "alternatives":    [
                    c.get("symbol") for c in p.get("to_candidates", [])[:2]
                    if c.get("symbol") not in ("—", None)
                ],
            }
            for p in actionable
        ]
    except Exception:
        return []


def _build_action_plan(
    report_type: str,
    market: dict,
    alerts: dict,
    portfolio: dict,
    do_not_chase: list,
    rotation: list,
    kill_watch: list,
) -> list:
    """
    Build a prioritised action plan (max 7 items).
    Each item: {priority: int, action: str, reason: str, urgency: 'HIGH'|'MEDIUM'|'LOW'}
    Lower priority number = more urgent.
    """
    actions = []
    priority = 1

    # 1. STOP_LOSS signals — always highest priority
    for k in kill_watch:
        if k["decision"] == "STOP_LOSS":
            actions.append({
                "priority": priority,
                "action":   f"停損 {k['symbol']}",
                "reason":   k["reason"],
                "urgency":  "HIGH",
            })
            priority += 1

    # 2. Rotation / rotate suggestions
    for r in rotation:
        if r["action"] in ("ROTATE_FULL", "STOP_LOSS"):
            alts = "→ " + "/".join(r["alternatives"]) if r["alternatives"] else ""
            actions.append({
                "priority": priority,
                "action":   f"轉倉 {r['symbol']} {alts}".strip(),
                "reason":   r["reason"],
                "urgency":  "HIGH",
            })
            priority += 1

    # 3. Urgent S/A alerts
    if alerts.get("s_count", 0) + alerts.get("a_count", 0) > 0:
        urgent = alerts.get("urgent", [])
        if urgent:
            first = urgent[0]
            actions.append({
                "priority": priority,
                "action":   f"檢視急迫警示：{first.get('title', first.get('symbol', '—'))}",
                "reason":   f"過去最近有 S 級 {alerts['s_count']} 筆 / A 級 {alerts['a_count']} 筆警示",
                "urgency":  "HIGH" if alerts["s_count"] > 0 else "MEDIUM",
            })
            priority += 1

    # 4. Market regime actions
    regime = market.get("regime", "sideways")
    if regime == "bear":
        actions.append({
            "priority": priority,
            "action":   "降低整體倉位，保留現金",
            "reason":   "大盤偏弱，SPY/QQQ 跌破均線",
            "urgency":  "MEDIUM",
        })
        priority += 1
    elif regime == "bull" and report_type == "pre_market":
        actions.append({
            "priority": priority,
            "action":   "可積極佈局動能強勢標的",
            "reason":   "大盤偏多，均線多頭排列",
            "urgency":  "LOW",
        })
        priority += 1

    # 5. Partial rotation suggestions
    for r in rotation:
        if r["action"] == "ROTATE_PARTIAL":
            alts = "→ " + "/".join(r["alternatives"]) if r["alternatives"] else ""
            actions.append({
                "priority": priority,
                "action":   f"考慮減倉 {r['symbol']} {alts}".strip(),
                "reason":   r["reason"],
                "urgency":  "MEDIUM",
            })
            priority += 1

    # 6. Do-not-chase reminder
    if do_not_chase:
        dnc_syms = ", ".join(d["symbol"] for d in do_not_chase[:3])
        actions.append({
            "priority": priority,
            "action":   f"勿追高：{dnc_syms}",
            "reason":   "追價風險分數 ≥ 65，現價偏離均線明顯",
            "urgency":  "MEDIUM",
        })
        priority += 1

    # 7. Portfolio efficiency nudge
    eff_avg = portfolio.get("efficiency_score_avg")
    if eff_avg is not None and eff_avg < 45:
        actions.append({
            "priority": priority,
            "action":   "複查持倉整體效率，考慮汰弱換強",
            "reason":   f"投組平均效率分數 {eff_avg:.0f}/100，整體偏弱",
            "urgency":  "MEDIUM",
        })
        priority += 1

    # Sort by priority and cap at 7
    actions.sort(key=lambda x: x["priority"])
    return actions[:7]


def _rule_based_summary(report: dict) -> str:
    """
    Generate a plain-text summary without AI assistance.
    """
    market   = report.get("market_status", {})
    sector   = report.get("sector_summary", {})
    alerts   = report.get("alerts_summary", {})
    rotation = report.get("rotation_suggestions", [])
    kill     = report.get("kill_signal_watch", [])
    plan     = report.get("action_plan", [])

    regime      = market.get("regime", "sideways")
    overall     = market.get("overall", "中性")
    regime_label = {"bull": "多頭", "sideways": "震盪盤整", "bear": "空頭走弱"}.get(regime, "中性")

    # Market sentence
    lines = [f"目前大盤呈 {regime_label} 格局，整體走勢 {overall}。"]

    # Top sector leader
    rankings = sector.get("rankings", [])
    if rankings:
        top = rankings[0]
        lines.append(f"板塊主線：{top['sector']}（{top.get('trend', 'NEUTRAL')}）領先，代表股 {top.get('top_symbol', '—')}。")

    # Top risk: STOP_LOSS or ROTATE_FULL
    sl_syms = [k["symbol"] for k in kill if k.get("decision") == "STOP_LOSS"]
    rot_full = [r["symbol"] for r in rotation if r.get("action") == "ROTATE_FULL"]
    risk_syms = sl_syms + rot_full
    if risk_syms:
        lines.append(f"主要風險：{', '.join(risk_syms[:3])} 出現停損/全部轉倉訊號，需優先處理。")

    # Top 3 action items
    if plan:
        lines.append("建議操作：")
        for item in plan[:3]:
            urgency_tag = "【緊急】" if item.get("urgency") == "HIGH" else ""
            lines.append(f"  {urgency_tag}{item['action']} — {item['reason']}")

    # Worst-case invalidation
    if regime == "bull":
        worst_case = "SPY / QQQ 跌破 MA20"
    elif regime == "bear":
        worst_case = "SPY / QQQ 進一步破前低"
    else:
        worst_case = "大盤量縮跌破今日低點"
    lines.append(f"若出現[{worst_case}]，今日原本判斷失效。")
    lines.append(DISCLAIMER)

    return "\n".join(lines)


def _generate_ai_summary(report: dict, ai_fn) -> str:
    """
    Call ai_fn with a structured prompt. Fall back to _rule_based_summary on any failure.
    """
    if ai_fn is None:
        return _rule_based_summary(report)

    market  = report.get("market_status", {})
    sector  = report.get("sector_summary", {})
    plan    = report.get("action_plan", [])
    rtype   = _REPORT_TYPE_LABEL.get(report.get("report_type", ""), report.get("report_type", ""))

    top_sectors = "; ".join(
        f"{r['sector']}({r['trend']})" for r in sector.get("rankings", [])[:3]
    )
    top_actions = "\n".join(
        f"{i+1}. {a['action']} ({a['urgency']})" for i, a in enumerate(plan[:5])
    )

    prompt = (
        f"你是專業台美股操盤助理，請用繁體中文生成今日{rtype}摘要。\n\n"
        f"大盤狀態：{market.get('overall', '中性')} / 格局：{market.get('regime', 'sideways')}\n"
        f"主要板塊：{top_sectors}\n"
        f"建議操作：\n{top_actions}\n\n"
        f"請以以下格式回覆（約200字）：\n"
        f"1. 大盤現況（一句話）\n"
        f"2. 板塊主線（一句話）\n"
        f"3. 主要風險（如有停損/轉倉訊號）\n"
        f"4. 今日建議操作（前三項）\n"
        f"5. 若出現[最壞情況]，今日原本判斷失效。\n"
        f"6. 結尾必須包含：{DISCLAIMER}"
    )

    try:
        result = ai_fn(prompt)
        if result and isinstance(result, str) and len(result) > 20:
            return result
    except Exception:
        pass

    return _rule_based_summary(report)


# ---------------------------------------------------------------------------
# Public API — generate_report
# ---------------------------------------------------------------------------

def generate_report(
    report_type: str,
    ohlcv_fn,
    positions: list | None = None,
    watchlist: list | None = None,
    bench_sym: str = "QQQ",
    ai_fn=None,
) -> dict:
    """
    Generate a daily report of the specified type.

    Parameters
    ----------
    report_type : 'pre_market' | 'intraday' | 'post_market'
    ohlcv_fn    : callable(symbol: str) -> dict | None
    positions   : list of holding dicts {symbol, cost, qty, buy_date, ...}
    watchlist   : list of symbol strings
    bench_sym   : benchmark symbol (default 'QQQ')
    ai_fn       : callable(prompt: str) -> str | None  (optional)

    Returns
    -------
    dict with all sub-sections and 'ai_summary'.
    """
    if report_type not in REPORT_TYPES:
        raise ValueError(f"report_type must be one of {REPORT_TYPES}")

    positions = positions or []
    watchlist = watchlist or []

    generated_at = datetime.now(timezone.utc).isoformat()

    # --- 1. Market status ---
    market = _build_market_status(ohlcv_fn)

    # --- 2. Sector summary (top 5 only) ---
    sector = _build_sector_summary(ohlcv_fn)

    # --- 3. Portfolio summary ---
    portfolio = _build_portfolio_summary(positions, ohlcv_fn)

    # --- 4. Alerts summary ---
    alert_hours = 8 if report_type == "intraday" else 24
    alerts = _build_alerts_summary(hours=alert_hours)

    # --- 5+6. Do-not-chase (watchlist + position symbols together) ---
    all_syms = list({s.upper() for s in watchlist} |
                    {(p.get("symbol") or p.get("sym", "")).upper() for p in positions if p.get("symbol") or p.get("sym")})
    do_not_chase = _build_do_not_chase_list(all_syms, ohlcv_fn)

    # --- 5. Watchlist candidates (not in do_not_chase) ---
    candidates = _build_watchlist_candidates(watchlist, ohlcv_fn, do_not_chase)

    # --- 7. Kill signal watch ---
    kill_watch = _build_kill_signal_watch(positions, ohlcv_fn)

    # --- 8. Rotation suggestions ---
    rotation = _build_rotation_suggestions(positions, ohlcv_fn)

    # --- 9. Action plan ---
    action_plan = _build_action_plan(
        report_type, market, alerts, portfolio, do_not_chase, rotation, kill_watch
    )

    is_demo = market.get("is_demo", False)
    _DEMO_WARNING = "⚠️ 目前使用模擬資料，不能作為交易決策。"

    # In demo mode: cap action_plan urgency to LOW, remove BUY/STRONG_BUY items
    if is_demo:
        _BLOCKED_ACTIONS = {"強力買進", "立即買進", "積極加碼", "強力買入"}
        action_plan = [
            {**a, "urgency": "LOW"} if a.get("urgency") in ("HIGH", "MEDIUM") else a
            for a in action_plan
            if a.get("action", "") not in _BLOCKED_ACTIONS
        ]

    report: dict = {
        "ok":                    True,
        "report_type":           report_type,
        "generated_at":          generated_at,
        "market_status":         market,
        "market_state":          market.get("overall", "中性"),
        "is_demo":               is_demo,
        "demo_data_warning":     _DEMO_WARNING if is_demo else None,
        "sector_summary":        sector,
        "portfolio_summary":     portfolio,
        "alerts_summary":        alerts,
        "watchlist_candidates":  candidates,
        "do_not_chase":          do_not_chase,
        "kill_signal_watch":     kill_watch,
        "rotation_suggestions":  rotation,
        "action_plan":           action_plan,
        "disclaimer":            DISCLAIMER,
    }

    # --- 10. AI summary ---
    report["ai_summary"] = _generate_ai_summary(report, ai_fn)

    return report


# ---------------------------------------------------------------------------
# Public API — persistence
# ---------------------------------------------------------------------------

def save_report(report: dict) -> int:
    """
    Persist a report to daily_reports table.
    Returns the new row id.
    """
    init_db()
    report_type  = report.get("report_type", "")
    generated_at = report.get("generated_at", datetime.now(timezone.utc).isoformat())
    market_state = report.get("market_status", {}).get("overall")
    alerts       = report.get("alerts_summary", {})
    sa_count     = alerts.get("s_count", 0) + alerts.get("a_count", 0)
    report_json  = json.dumps(report, ensure_ascii=False)

    with _lock_conn() as con:
        cur = con.execute(
            """
            INSERT INTO daily_reports
                (report_type, generated_at, report_json, market_state, sa_alert_count)
            VALUES (?, ?, ?, ?, ?)
            """,
            (report_type, generated_at, report_json, market_state, sa_count),
        )
        return cur.lastrowid


def get_latest(report_type: str | None = None) -> dict | None:
    """
    Return the most recent report, optionally filtered by report_type.
    """
    init_db()
    with _lock_conn() as con:
        if report_type:
            row = con.execute(
                """
                SELECT report_json FROM daily_reports
                WHERE report_type=?
                ORDER BY generated_at DESC LIMIT 1
                """,
                (report_type,),
            ).fetchone()
        else:
            row = con.execute(
                """
                SELECT report_json FROM daily_reports
                ORDER BY generated_at DESC LIMIT 1
                """
            ).fetchone()
    if not row:
        return None
    try:
        return json.loads(row["report_json"])
    except Exception:
        return None


def get_history(limit: int = 30) -> list:
    """
    Return up to `limit` most recent reports (newest first), as list of dicts.
    """
    init_db()
    with _lock_conn() as con:
        rows = con.execute(
            """
            SELECT id, report_type, generated_at, market_state, sa_alert_count, report_json
            FROM daily_reports
            ORDER BY generated_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

    results = []
    for row in rows:
        try:
            r = json.loads(row["report_json"])
        except Exception:
            r = {}
        r["_db_id"]           = row["id"]
        r["_db_report_type"]  = row["report_type"]
        r["_db_generated_at"] = row["generated_at"]
        r["_db_market_state"] = row["market_state"]
        r["_db_sa_alert_count"] = row["sa_alert_count"]
        results.append(r)
    return results


# ---------------------------------------------------------------------------
# Public API — formatters
# ---------------------------------------------------------------------------

def format_line(report: dict) -> str:
    """
    Chinese LINE message, max 400 chars.
    """
    rtype   = report.get("report_type", "pre_market")
    label   = _REPORT_TYPE_LABEL.get(rtype, rtype)
    market  = report.get("market_status", {})
    sector  = report.get("sector_summary", {})
    portfolio = report.get("portfolio_summary", {})
    do_not_chase = report.get("do_not_chase", [])
    plan    = report.get("action_plan", [])

    regime  = market.get("regime", "sideways")
    emoji   = _REGIME_EMOJI.get(regime, "🟡")
    overall = market.get("overall", "中性")

    # Top 2 sectors
    rankings = sector.get("rankings", [])
    top2_sectors = "、".join(r["sector"] for r in rankings[:2]) if rankings else "—"

    # Risk positions
    risk_pos = portfolio.get("risk_positions", [])
    risk_str = "、".join(risk_pos[:3]) if risk_pos else "無"

    # First do-not-chase symbol
    dnc_str = do_not_chase[0]["symbol"] if do_not_chase else "無"

    # First action
    action_str = plan[0]["action"] if plan else "持倉觀察"

    msg = (
        f"【{label}】\n"
        f"大盤：{emoji} {overall}\n"
        f"主線板塊：{top2_sectors}\n"
        f"持倉風險：{risk_str}\n"
        f"今日注意：{dnc_str}\n"
        f"操作建議：{action_str}\n"
        f"---\n"
        f"{DISCLAIMER}"
    )
    # Trim to 400 chars
    return msg[:400]


def format_email_subject(report: dict) -> str:
    """
    e.g. "[Scott] 盤前快報 2026-06-02 大盤偏多"
    """
    rtype   = report.get("report_type", "pre_market")
    label   = _REPORT_TYPE_LABEL.get(rtype, rtype)
    gen_at  = report.get("generated_at", "")
    date_str = gen_at[:10] if gen_at else datetime.now(timezone.utc).date().isoformat()
    overall = report.get("market_status", {}).get("overall", "中性")
    return f"[Scott] {label} {date_str} 大盤{overall}"


def format_email_body(report: dict) -> str:
    """
    Full report in plain text with sections.
    """
    rtype   = report.get("report_type", "pre_market")
    label   = _REPORT_TYPE_LABEL.get(rtype, rtype)
    gen_at  = report.get("generated_at", "")
    market  = report.get("market_status", {})
    sector  = report.get("sector_summary", {})
    portfolio = report.get("portfolio_summary", {})
    alerts  = report.get("alerts_summary", {})
    candidates = report.get("watchlist_candidates", [])
    do_not_chase = report.get("do_not_chase", [])
    kill_watch = report.get("kill_signal_watch", [])
    rotation = report.get("rotation_suggestions", [])
    plan    = report.get("action_plan", [])
    ai_sum  = report.get("ai_summary", "")

    lines = []
    sep = "=" * 50

    # Header
    lines.append(sep)
    lines.append(f"Scott 投資決策系統 — {label}")
    lines.append(f"生成時間：{gen_at[:19].replace('T', ' ')} UTC")
    lines.append(sep)

    # AI / Rule-based Summary
    lines.append("\n【摘要】")
    lines.append(ai_sum)

    # Market status
    lines.append(f"\n{'─' * 40}")
    lines.append("【大盤狀態】")
    overall = market.get("overall", "中性")
    regime  = market.get("regime", "sideways")
    lines.append(f"整體：{overall}（{regime}）")
    for idx in market.get("indices", []):
        vs = "↑均線上方" if idx["vs_ma20"] == "above" else "↓均線下方"
        lines.append(
            f"  {idx['symbol']}：1日漲跌 {idx['change_1d_pct']:+.2f}%，"
            f"動能 {idx['momentum_score']}/100，{vs}"
        )

    # Sector summary
    lines.append(f"\n{'─' * 40}")
    lines.append("【板塊分析（前5大）】")
    for r in sector.get("rankings", []):
        lines.append(
            f"  {r['sector']}：{r.get('trend', 'NEUTRAL')}，"
            f"分數 {r.get('score', '—')}，代表股 {r.get('top_symbol', '—')}"
        )
    if sector.get("leaders"):
        leaders_str = "、".join(r["sector"] for r in sector["leaders"])
        lines.append(f"  板塊領先：{leaders_str}")
    if sector.get("laggards"):
        laggards_str = "、".join(r["sector"] for r in sector["laggards"])
        lines.append(f"  板塊落後：{laggards_str}")

    # Portfolio summary
    lines.append(f"\n{'─' * 40}")
    lines.append("【持倉分析】")
    lines.append(f"  持倉數：{portfolio.get('total', 0)}")
    eff = portfolio.get("efficiency_score_avg")
    drag = portfolio.get("drag_score_avg")
    if eff is not None:
        lines.append(f"  平均效率分數：{eff:.0f}/100")
    if drag is not None:
        lines.append(f"  平均拖累分數：{drag:.0f}/100")
    risk_pos = portfolio.get("risk_positions", [])
    if risk_pos:
        lines.append(f"  風險持倉：{', '.join(risk_pos)}")
    strong_pos = portfolio.get("strong_positions", [])
    if strong_pos:
        lines.append(f"  強勢持倉：{', '.join(strong_pos)}")

    # Kill signals
    if kill_watch:
        lines.append(f"\n{'─' * 40}")
        lines.append("【停損/換股訊號】")
        for k in kill_watch:
            lines.append(f"  [{k['urgency']}] {k['symbol']}：{k['decision']} — {k['reason']}")

    # Rotation suggestions
    if rotation:
        lines.append(f"\n{'─' * 40}")
        lines.append("【轉倉建議】")
        for r in rotation:
            alts = " → " + "/".join(r["alternatives"]) if r["alternatives"] else ""
            lines.append(f"  {r['symbol']}：{r['action_label']}{alts}")
            lines.append(f"    {r['reason']}")

    # Alerts summary
    lines.append(f"\n{'─' * 40}")
    lines.append("【警示摘要】")
    lines.append(
        f"  總計 {alerts.get('total', 0)} 筆，S級 {alerts.get('s_count', 0)} 筆，"
        f"A級 {alerts.get('a_count', 0)} 筆"
    )
    for u in alerts.get("urgent", []):
        lines.append(f"  [{u.get('level', '?')}] {u.get('symbol', '?')}：{u.get('title', '—')}")

    # Watchlist candidates
    if candidates:
        lines.append(f"\n{'─' * 40}")
        lines.append("【觀察名單候選（動能 ≥ 60）】")
        for c in candidates:
            lines.append(f"  {c['symbol']}：動能 {c['momentum_score']}/100")

    # Do-not-chase
    if do_not_chase:
        lines.append(f"\n{'─' * 40}")
        lines.append("【勿追清單（追價風險 ≥ 65）】")
        for d in do_not_chase:
            lines.append(f"  {d['symbol']}：追高風險分數 {d['chase_score']}/100")

    # Action plan
    lines.append(f"\n{'─' * 40}")
    lines.append("【今日操作建議】")
    if plan:
        for item in plan:
            urgency_tag = "【緊急】" if item["urgency"] == "HIGH" else (
                "【注意】" if item["urgency"] == "MEDIUM" else ""
            )
            lines.append(f"  {item['priority']}. {urgency_tag}{item['action']}")
            lines.append(f"     原因：{item['reason']}")
    else:
        lines.append("  無特別建議，持倉觀察。")

    # Footer
    lines.append(f"\n{sep}")
    lines.append(DISCLAIMER)
    lines.append(sep)

    return "\n".join(lines)
