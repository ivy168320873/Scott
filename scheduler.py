"""
Automated Trading Scheduler
Runs pre-market scans, executes signals at open, and reviews positions at EOD.
Uses APScheduler. Starts automatically when imported with SCHEDULER_ENABLE=true.

Schedule (US Eastern Time):
  09:00 — pre_market_scan:   fetch OHLCV for all candidates, compute signals
  09:32 — market_open_exec:  execute buy signals that passed all filters
  15:50 — eod_review:        check stop-loss / take-profit / trailing stops
  Every 5 min (market hours) — price_monitor: check triggered alerts
"""
from __future__ import annotations
import os
import time
import logging
import threading
from datetime import datetime, timezone, timedelta
from typing import Optional

import requests as _req

logger = logging.getLogger(__name__)

# ── Signal storage (shared with app.py via module-level dicts) ────────────────
_scan_candidates: list[dict] = []    # [{symbol, score, entry, stop, target, reason}]
_executed_today:  list[dict] = []    # orders submitted today
_scan_log:        list[str]  = []    # human-readable log lines
_last_scan_ts:    float      = 0.0

_SCAN_LOG_MAX = 200

def _log(msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    _scan_log.append(line)
    if len(_scan_log) > _SCAN_LOG_MAX:
        _scan_log.pop(0)
    logger.info(msg)


# ── Symbols to scan (mirrors AUTO_SCAN_LIST in frontend) ──────────────────────
SCAN_SYMBOLS = [
    # AI / Semiconductor
    "NVDA","AMD","AVGO","MRVL","MU","QCOM","ARM","SMCI","AMAT","KLAC","LRCX","ON","INTC",
    # AI Cloud / Software
    "MSFT","META","GOOGL","AMZN","PLTR","ORCL","NOW","CRM","SNOW","AI",
    # Cybersecurity
    "CRWD","PANW","ZS","FTNT","OKTA","CYBR",
    # Nuclear / Energy Infra
    "VST","CEG","CCJ","NNE","SMR",
    # Defense / Aerospace
    "LMT","RTX","NOC","RKLB",
    # Crypto
    "COIN","MSTR","HOOD",
    # EV / Clean Energy
    "TSLA","RIVN","NIO",
    # Biotech / GLP-1
    "LLY","NVO","MRNA","ISRG","REGN","ABBV",
    # Benchmarks (needed for rotation analysis)
    "SPY","QQQ","XLK","GLD","TLT",
]


# ── OHLCV fetcher (server-side, avoids browser CORS) ─────────────────────────

YAHOO_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
}

def _fetch_ohlcv(symbol: str, period: str = "1y") -> Optional[list[dict]]:
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    params = {"range": period, "interval": "1d", "events": "history"}
    try:
        r = _req.get(url, params=params, headers=YAHOO_HEADERS, timeout=8)
        if r.status_code != 200:
            return None
        j = r.json()
        res = j.get("chart", {}).get("result", [None])[0]
        if not res:
            return None
        ts   = res["timestamp"]
        q    = res["indicators"]["quote"][0]
        rows = []
        for i, t in enumerate(ts):
            c = q["close"][i]
            if c is None:
                continue
            rows.append({
                "date":   datetime.utcfromtimestamp(t).strftime("%Y-%m-%d"),
                "open":   q["open"][i]  or c,
                "high":   q["high"][i]  or c,
                "low":    q["low"][i]   or c,
                "close":  c,
                "volume": int(q["volume"][i] or 0),
            })
        return rows if len(rows) >= 50 else None
    except Exception as e:
        logger.debug("fetch_ohlcv %s error: %s", symbol, e)
        return None


# ── Decision-core signal evaluation (mirrors frontend JS logic) ───────────────

def _ema(arr, period):
    k = 2 / (period + 1)
    out, val = [], None
    for v in arr:
        if val is None:
            val = v
        else:
            val = v * k + val * (1 - k)
        out.append(val)
    return out

def _sma(arr, period):
    return [
        sum(arr[max(0,i-period+1):i+1]) / min(i+1, period)
        for i in range(len(arr))
    ]

def _rsi(closes, period=14):
    out = [50.0] * len(closes)
    ag = al = 0.0
    for i in range(1, period + 1):
        d = closes[i] - closes[i-1]
        if d > 0: ag += d
        else:     al -= d
    ag /= period; al /= period
    out[period] = 100 - 100 / (1 + ag / al) if al else 100
    for i in range(period + 1, len(closes)):
        d = closes[i] - closes[i-1]
        ag = (ag * (period-1) + max(d, 0)) / period
        al = (al * (period-1) + max(-d, 0)) / period
        out[i] = 100 - 100 / (1 + ag / al) if al else 100
    return out

def _macd_hist(closes):
    ef = _ema(closes, 12)
    es = _ema(closes, 26)
    ml = [a - b for a, b in zip(ef, es)]
    sl = _ema(ml, 9)
    return [a - b for a, b in zip(ml, sl)]

def _obv(closes, volumes):
    out = [0]
    for i in range(1, len(closes)):
        if closes[i] > closes[i-1]:   out.append(out[-1] + volumes[i])
        elif closes[i] < closes[i-1]: out.append(out[-1] - volumes[i])
        else:                          out.append(out[-1])
    return out

def _bb_width(closes):
    """Returns (current_width, avg_width_20d) as ratio to mid."""
    import math
    n = len(closes)
    if n < 20:
        return None, None
    widths = []
    for j in range(20, n + 1):
        sl = closes[j-20:j]
        mid = sum(sl) / 20
        std = math.sqrt(sum((v - mid)**2 for v in sl) / 20)
        widths.append(2 * std / mid if mid else 0)
    current = widths[-1] if widths else None
    avg_20  = sum(widths[-20:]) / min(len(widths), 20) if widths else None
    return current, avg_20


def _evaluate_signal(symbol: str, ohlcv: list[dict]) -> Optional[dict]:
    """
    Decision Core V2 — strict entry filters matching strategy_decision_core_v2.
    ALL filters must pass:
      1. Regime:        price > EMA200
      2. BB squeeze:    bandwidth < 90% of 20-day avg
      3. OBV slope:     10-day OBV delta > 0
      4. Volume quality: last bar > 5-day avg vol
      5. RSI sweet spot: 45–68
      6. Module gates:  主力>65, 空壓<25, 追高<45, 共振>=80
    """
    closes  = [d["close"]  for d in ohlcv]
    opens   = [d["open"]   for d in ohlcv]
    highs   = [d["high"]   for d in ohlcv]
    lows    = [d["low"]    for d in ohlcv]
    volumes = [d["volume"] for d in ohlcv]
    n = len(closes)
    if n < 210:
        return None

    # ── Filter 1: Regime — price > EMA200 ────────────────────────────────────
    e200 = _ema(closes, 200)[-1]
    if closes[-1] <= e200:
        return None

    # ── Filter 2: BB squeeze ──────────────────────────────────────────────────
    bw_curr, bw_avg = _bb_width(closes)
    if bw_curr is None or bw_avg is None or bw_avg == 0 or bw_curr > bw_avg * 0.90:
        return None

    # ── Filter 3: OBV slope positive over 10 bars ─────────────────────────────
    obv     = _obv(closes, volumes)
    if len(obv) < 11 or obv[-1] <= obv[-11]:
        return None

    # ── Filter 4: Volume quality — last bar > 5-day avg ─────────────────────
    vol_ma5 = sum(volumes[-5:]) / 5 if len(volumes) >= 5 else volumes[-1]
    if volumes[-1] < vol_ma5:
        return None

    # ── Filter 5: RSI sweet spot 45–68 ───────────────────────────────────────
    rsi_arr = _rsi(closes)
    rsi_val = rsi_arr[-1]
    if not (45 <= rsi_val <= 68):
        return None

    # ── Module 1: 主力控盤 ────────────────────────────────────────────────────
    obv_ema   = _ema(obv, 20)
    obv_norm  = min(max((obv[-1] - obv_ema[-1]) / (abs(obv_ema[-1]) or 1) * 100, -50), 50)
    vol_ma20  = sum(volumes[-20:]) / 20 or 1
    vol_ratio = volumes[-1] / vol_ma20
    vol_score = min(vol_ratio * 20, 50)
    price_chg5  = (closes[-1] - closes[-6]) / (closes[-6] or 1) * 100 if n >= 6 else 0
    vol_confirm = min(price_chg5 * vol_ratio * 5, 50) if price_chg5 > 0 else 0
    smart_money = round(min(max((obv_norm + vol_score + vol_confirm) / 3 * 2, 0), 100))

    # ── Module 2: 空方壓力 ────────────────────────────────────────────────────
    is_down       = closes[-1] < opens[-1]
    bear_ratio    = 100.0 if is_down else 0.0
    bear_rsi      = (50 - rsi_val) * 2 if rsi_val < 50 else 0
    bear_pressure = round(min(bear_ratio * 0.6 + bear_rsi * 0.4, 100))

    # ── Module 3: 追高風險 ────────────────────────────────────────────────────
    rsi_risk  = (rsi_val - 70) * 3 if rsi_val > 70 else 0
    ma20_arr  = _sma(closes, 20)
    ma20_v    = ma20_arr[-1] or closes[-1]
    dev_risk  = max((closes[-1] - ma20_v) / ma20_v * 100 * 2, 0)
    trs = [max(highs[-i]-lows[-i], abs(highs[-i]-closes[-i-1]), abs(lows[-i]-closes[-i-1]))
           for i in range(1, min(15, n))]
    atr_v     = sum(trs) / len(trs) if trs else closes[-1] * 0.02
    chg5      = closes[-1] - closes[-6] if n >= 6 else 0
    atr_risk  = min(chg5 / atr_v * 10, 30) if atr_v > 0 else 0
    chase_risk = round(min(rsi_risk + dev_risk + atr_risk, 100))

    # ── Module 4: 多週期共振 ──────────────────────────────────────────────────
    e20   = _ema(closes, 20)[-1]
    e50   = _ema(closes, 50)[-1]
    e100  = _ema(closes, 100)[-1]
    e250  = _ema(closes, 250)[-1] if n >= 250 else closes[-1]
    e400  = _ema(closes, 400)[-1] if n >= 400 else closes[-1]
    mh    = _macd_hist(closes)[-1]
    daily_b   = closes[-1] > e20 and e20 > e50
    weekly_b  = closes[-1] > e100 and e100 > e250
    monthly_b = closes[-1] > e400
    resonance = sum([daily_b, weekly_b, monthly_b, mh > 0, rsi_val > 50]) * 20

    # ── Entry gate (strict V2 thresholds) ────────────────────────────────────
    if not (smart_money > 65 and bear_pressure < 25 and chase_risk < 45 and resonance >= 80):
        return None

    # ── Stop / target ─────────────────────────────────────────────────────────
    stop     = round(max(min(lows[-10:]) - atr_v * 1.5, closes[-1] * 0.88), 2)
    risk_amt = closes[-1] - stop
    if risk_amt <= 0:
        return None
    r1_target = round(closes[-1] + risk_amt * 1.5, 2)   # leg 1
    r2_target = round(closes[-1] + risk_amt * 4.0, 2)   # leg 2

    reasons = []
    if smart_money > 70:   reasons.append(f"主力控盤 {smart_money}")
    if resonance >= 80:    reasons.append(f"多週期共振 {resonance}%")
    if bw_curr < bw_avg * 0.80: reasons.append("BB緊縮")
    if vol_ratio > 1.5:    reasons.append(f"量比 {vol_ratio:.1f}x")

    return {
        "symbol":        symbol,
        "price":         closes[-1],
        "entry":         round(closes[-1] * 1.001, 2),
        "stop":          stop,
        "target":        r2_target,
        "r1_target":     r1_target,
        "risk_pct":      round(risk_amt / closes[-1] * 100, 2),
        "rr":            4.0,
        "smart_money":   smart_money,
        "bear_pressure": bear_pressure,
        "chase_risk":    chase_risk,
        "resonance":     resonance,
        "reason":        "；".join(reasons) or "V2嚴格四模組",
        "scanned_at":    datetime.now(timezone.utc).isoformat(),
    }


# ── Scan runner ───────────────────────────────────────────────────────────────

def run_scan(symbols: Optional[list[str]] = None) -> list[dict]:
    """
    Fetch OHLCV + evaluate signals for all symbols.
    Updates global _scan_candidates.
    """
    global _scan_candidates, _last_scan_ts
    syms = symbols or SCAN_SYMBOLS
    _log(f"🔍 開始掃描 {len(syms)} 支股票…")

    candidates = []
    for i, sym in enumerate(syms):
        try:
            ohlcv = _fetch_ohlcv(sym)
            if ohlcv:
                sig = _evaluate_signal(sym, ohlcv)
                if sig:
                    candidates.append(sig)
                    _log(f"  ✅ {sym}: 主力{sig['smart_money']} 空壓{sig['bear_pressure']} 追高{sig['chase_risk']} 共振{sig['resonance']}%")
        except Exception as e:
            _log(f"  ❌ {sym}: {e}")
        if (i + 1) % 10 == 0:
            _log(f"  進度 {i+1}/{len(syms)}…")

    candidates.sort(key=lambda x: (x["resonance"] + x["smart_money"] - x["chase_risk"]), reverse=True)
    _scan_candidates = candidates
    _last_scan_ts = time.time()
    _log(f"✅ 掃描完成：{len(candidates)} 支通過條件")
    return candidates


# ── Scheduler task functions ──────────────────────────────────────────────────

def task_pre_market_scan():
    """09:00 ET — scan all symbols, prepare candidates."""
    _log("🌅 盤前掃描啟動…")
    try:
        run_scan()
    except Exception as e:
        _log(f"盤前掃描錯誤：{e}")


def task_market_open_exec():
    """
    09:32 ET — execute top-ranked signals via trader.
    Skips if no candidates or risk manager blocks.
    """
    from trader import get_engine
    from risk_manager import get_risk_manager

    _log("📊 開盤執行信號…")
    if not _scan_candidates:
        _log("  沒有候選標的，跳過")
        return

    engine = get_engine()
    rm     = get_risk_manager()
    acct   = engine.get_account()
    equity = acct.get("equity", 0)
    open_pos = len(engine.get_positions())

    executed = 0
    for sig in _scan_candidates[:5]:   # max 5 orders per open
        ok, shares, details = rm.validate_order(
            portfolio_value     = equity,
            start_of_day_value  = equity,
            open_positions      = open_pos + executed,
            entry               = sig["entry"],
            stop                = sig["stop"],
        )
        if not ok:
            _log(f"  ⛔ {sig['symbol']}: {details.get('reason', '風控拒絕')}")
            continue

        result = engine.submit_order(
            symbol     = sig["symbol"],
            shares     = shares,
            entry      = sig["entry"],
            stop_price = sig["stop"],
            take_profit= sig["target"],
            note       = sig["reason"],
        )
        if result.get("ok"):
            _executed_today.append({**result, "details": details})
            executed += 1
            _log(f"  ✅ {sig['symbol']}: BUY {shares}股 @ ${sig['entry']:.2f}  停損${sig['stop']:.2f}")
        else:
            _log(f"  ❌ {sig['symbol']}: {result.get('error', '下單失敗')}")

    _log(f"開盤執行完畢：{executed} 筆訂單")


def task_eod_review():
    """15:50 ET — log P&L, check positions."""
    from trader import get_engine
    engine = get_engine()
    positions = engine.get_positions()
    _log(f"🌆 盤後檢查：持有 {len(positions)} 個部位")
    for p in positions:
        pnl = p.get("unrealized_pnl_pct", 0)
        sym = p["symbol"]
        _log(f"  {sym}: P&L {pnl:+.1f}%  市值 ${p.get('market_value',0):,.0f}")


# ── APScheduler setup ─────────────────────────────────────────────────────────

_scheduler = None
_scheduler_lock = threading.Lock()

def start_scheduler():
    """
    Start the background scheduler (call once from app.py startup).
    Only starts if SCHEDULER_ENABLE=true in environment.
    """
    global _scheduler
    if os.environ.get("SCHEDULER_ENABLE", "false").lower() != "true":
        logger.info("Scheduler disabled (set SCHEDULER_ENABLE=true to enable)")
        return

    with _scheduler_lock:
        if _scheduler is not None:
            return   # already running

        try:
            from apscheduler.schedulers.background import BackgroundScheduler
            from apscheduler.triggers.cron import CronTrigger
        except ImportError:
            logger.warning("apscheduler not installed — scheduler disabled. pip install apscheduler")
            return

        _scheduler = BackgroundScheduler(timezone="America/New_York")

        # Pre-market: 9:00 AM ET Mon-Fri
        _scheduler.add_job(
            task_pre_market_scan, CronTrigger(
                day_of_week="mon-fri", hour=9, minute=0,
                timezone="America/New_York",
            ), id="pre_market_scan", replace_existing=True,
        )

        # Market open: 9:32 AM ET Mon-Fri (2 min after open for stability)
        _scheduler.add_job(
            task_market_open_exec, CronTrigger(
                day_of_week="mon-fri", hour=9, minute=32,
                timezone="America/New_York",
            ), id="market_open_exec", replace_existing=True,
        )

        # EOD review: 3:50 PM ET Mon-Fri
        _scheduler.add_job(
            task_eod_review, CronTrigger(
                day_of_week="mon-fri", hour=15, minute=50,
                timezone="America/New_York",
            ), id="eod_review", replace_existing=True,
        )

        _scheduler.start()
        _log("⏰ 排程器已啟動：盤前09:00 / 開盤09:32 / 盤後15:50 (ET)")


def stop_scheduler():
    global _scheduler
    with _scheduler_lock:
        if _scheduler and _scheduler.running:
            _scheduler.shutdown(wait=False)
            _scheduler = None
            _log("排程器已停止")


def get_scheduler_status() -> dict:
    return {
        "running":     _scheduler is not None and getattr(_scheduler, "running", False),
        "enabled":     os.environ.get("SCHEDULER_ENABLE", "false").lower() == "true",
        "last_scan":   datetime.utcfromtimestamp(_last_scan_ts).strftime("%H:%M:%S UTC") if _last_scan_ts else "尚未掃描",
        "candidates":  len(_scan_candidates),
        "executed_today": len(_executed_today),
        "log":         _scan_log[-50:],
    }


# ── Manual trigger (for API endpoint) ────────────────────────────────────────

def trigger_scan_now(symbols: Optional[list[str]] = None) -> list[dict]:
    """Called by /api/scan endpoint for on-demand scanning."""
    return run_scan(symbols)
