"""
Automated Trading Scheduler — 24/7 Operation
Runs pre-market scans, executes signals, monitors positions intraday, and
reviews EOD. Integrates with monitor.py for state persistence and alerts.

Schedule (US Eastern Time):
  09:00 — pre_market_scan:      fetch OHLCV, compute signals, check regime
  09:32 — market_open_exec:     execute buy signals that passed all filters
  11:30 — midday_scan:          rescan for new setups mid-session
  15:30 — pre_close_review:     tighten stops, flag weak positions
  16:30 — eod_summary:          log P&L, reset daily stats, build watchlist
  Every 5 min (market hours) — position_monitor: check stops + trail
  Every 60 min (off hours) — health_ping: log system status
"""
from __future__ import annotations
import os
import time
import logging
import threading
from datetime import datetime, timezone, timedelta
from typing import Optional

import requests as _req

import monitor as _mon

logger = logging.getLogger(__name__)

# ── Signal storage (also mirrored to StateManager) ───────────────────────────
_scan_candidates: list[dict] = []
_executed_today:  list[dict] = []
_scan_log:        list[str]  = []
_last_scan_ts:    float      = 0.0

_SCAN_LOG_MAX = 300


def _log(msg: str):
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    _scan_log.append(line)
    if len(_scan_log) > _SCAN_LOG_MAX:
        _scan_log.pop(0)
    logger.info(msg)


# ── Symbols to scan ───────────────────────────────────────────────────────────
SCAN_SYMBOLS = [
    # AI / Semiconductor
    "NVDA","AMD","AVGO","MRVL","MU","QCOM","ARM","SMCI","AMAT","KLAC","LRCX","ON","INTC",
    # AI Cloud / Software
    "MSFT","META","GOOGL","AMZN","PLTR","ORCL","NOW","CRM","SNOW","AI",
    # Cybersecurity
    "CRWD","PANW","ZS","FTNT","OKTA","CYBR",
    # Nuclear / Energy
    "VST","CEG","CCJ","NNE","SMR",
    # Defense
    "LMT","RTX","NOC","RKLB",
    # Crypto
    "COIN","MSTR","HOOD",
    # EV / Clean Energy
    "TSLA","RIVN","NIO",
    # Biotech / GLP-1
    "LLY","NVO","MRNA","ISRG","REGN","ABBV",
    # Benchmarks (used for SPY regime check)
    "SPY","QQQ","XLK","GLD","TLT",
]

YAHOO_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
}


# ── OHLCV Fetcher ─────────────────────────────────────────────────────────────

def _fetch_ohlcv(symbol: str, period: str = "1y") -> Optional[list[dict]]:
    url    = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    params = {"range": period, "interval": "1d", "events": "history"}
    for attempt in range(3):
        try:
            r = _req.get(url, params=params, headers=YAHOO_HEADERS, timeout=10)
            if r.status_code != 200:
                break
            j   = r.json()
            res = j.get("chart", {}).get("result", [None])[0]
            if not res:
                break
            ts   = res["timestamp"]
            q    = res["indicators"]["quote"][0]
            rows = []
            for i, t in enumerate(ts):
                c = q["close"][i]
                if c is None:
                    continue
                rows.append({
                    "date":   datetime.utcfromtimestamp(t).strftime("%Y-%m-%d"),
                    "open":   q["open"][i]   or c,
                    "high":   q["high"][i]   or c,
                    "low":    q["low"][i]    or c,
                    "close":  c,
                    "volume": int(q["volume"][i] or 0),
                })
            return rows if len(rows) >= 50 else None
        except Exception as e:
            logger.debug("fetch_ohlcv %s attempt %d: %s", symbol, attempt + 1, e)
            if attempt < 2:
                time.sleep(2 ** attempt)
    return None


# ── Indicator helpers ─────────────────────────────────────────────────────────

def _ema(arr, period):
    k, val, out = 2 / (period + 1), None, []
    for v in arr:
        val = v if val is None else v * k + val * (1 - k)
        out.append(val)
    return out


def _sma(arr, period):
    return [
        sum(arr[max(0, i - period + 1):i + 1]) / min(i + 1, period)
        for i in range(len(arr))
    ]


def _rsi(closes, period=14):
    out = [50.0] * len(closes)
    ag = al = 0.0
    for i in range(1, period + 1):
        d = closes[i] - closes[i - 1]
        if d > 0: ag += d
        else:     al -= d
    ag /= period; al /= period
    out[period] = 100 - 100 / (1 + ag / al) if al else 100
    for i in range(period + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        ag = (ag * (period - 1) + max(d, 0)) / period
        al = (al * (period - 1) + max(-d, 0)) / period
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
        if closes[i] > closes[i - 1]:   out.append(out[-1] + volumes[i])
        elif closes[i] < closes[i - 1]: out.append(out[-1] - volumes[i])
        else:                            out.append(out[-1])
    return out


def _bb_width(closes):
    import math
    n = len(closes)
    if n < 20:
        return None, None
    widths = []
    for j in range(20, n + 1):
        sl = closes[j - 20:j]
        mid = sum(sl) / 20
        std = math.sqrt(sum((v - mid) ** 2 for v in sl) / 20)
        widths.append(2 * std / mid if mid else 0)
    current = widths[-1] if widths else None
    avg_20  = sum(widths[-20:]) / min(len(widths), 20) if widths else None
    return current, avg_20


def _atr(highs, lows, closes, period=14):
    trs = []
    for i in range(1, len(closes)):
        trs.append(max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i]  - closes[i - 1]),
        ))
    if not trs:
        return closes[-1] * 0.02
    atr = sum(trs[:period]) / period
    for tr in trs[period:]:
        atr = (atr * (period - 1) + tr) / period
    return atr


# ── Market Regime (SPY) ───────────────────────────────────────────────────────

_regime_cache: dict = {'ts': 0.0, 'regime': {}}
_REGIME_TTL = 3600   # refresh every hour


def _get_regime() -> dict:
    """Fetch SPY and compute market regime. Cached for 1 hour."""
    global _regime_cache
    if time.time() - _regime_cache['ts'] < _REGIME_TTL and _regime_cache['regime']:
        return _regime_cache['regime']
    ohlcv = _fetch_ohlcv('SPY', '2y')
    if not ohlcv:
        return {'bullish': True, 'label': '無法獲取 SPY 數據'}
    closes = [d['close'] for d in ohlcv]
    regime = _mon.check_market_regime(closes)
    _regime_cache = {'ts': time.time(), 'regime': regime}
    _mon.get_state().set('regime', regime)
    _log(f"📊 市場氣氛：{regime['label']}  SPY=${regime['spy_price']}  EMA50=${regime['ema50']}")
    return regime


# ── V2 Signal Evaluator (strict filters matching strategy_decision_core_v2) ──

def _evaluate_signal(symbol: str, ohlcv: list[dict]) -> Optional[dict]:
    """
    Decision Core V2 — all 5 entry filters must pass:
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
    if n < 60:
        return None

    # Filter 1: Regime — price > EMA200
    e200 = _ema(closes, min(200, n))[-1]
    if closes[-1] <= e200:
        return None

    # Filter 2: BB squeeze — bandwidth < 90% of 20-day avg
    bw_curr, bw_avg = _bb_width(closes)
    if bw_curr is None or bw_avg is None or bw_avg == 0 or bw_curr > bw_avg * 0.90:
        return None

    # Filter 3: OBV 10-day slope positive
    obv = _obv(closes, volumes)
    if len(obv) < 11 or obv[-1] <= obv[-11]:
        return None

    # Filter 4: Volume quality — last bar > 5-day avg
    vol_ma5 = sum(volumes[-5:]) / 5 if len(volumes) >= 5 else volumes[-1]
    if volumes[-1] < vol_ma5:
        return None

    # Filter 5: RSI sweet spot 45–68
    rsi_arr = _rsi(closes)
    rsi_val = rsi_arr[-1]
    if not (45 <= rsi_val <= 68):
        return None

    # Module 1: 主力控盤
    obv_ema   = _ema(obv, 20)
    obv_norm  = min(max((obv[-1] - obv_ema[-1]) / (abs(obv_ema[-1]) or 1) * 100, -50), 50)
    vol_ma20  = sum(volumes[-20:]) / 20 or 1
    vol_ratio = volumes[-1] / vol_ma20
    vol_score = min(vol_ratio * 20, 50)
    pc5       = (closes[-1] - closes[-6]) / (closes[-6] or 1) * 100 if n >= 6 else 0
    vol_conf  = min(pc5 * vol_ratio * 5, 50) if pc5 > 0 else 0
    smart     = round(min(max((obv_norm + vol_score + vol_conf) / 3 * 2, 0), 100))

    # Module 2: 空方壓力
    is_down  = closes[-1] < opens[-1]
    bear_rsi = (50 - rsi_val) * 2 if rsi_val < 50 else 0
    bear_p   = round(min((100.0 if is_down else 0.0) * 0.6 + bear_rsi * 0.4, 100))

    # Module 3: 追高風險
    ma20_arr  = _sma(closes, 20)
    ma20_v    = ma20_arr[-1] or closes[-1]
    dev_risk  = max((closes[-1] - ma20_v) / ma20_v * 100 * 2, 0)
    atr_v     = _atr(highs, lows, closes)
    chg5      = closes[-1] - closes[-6] if n >= 6 else 0
    atr_risk  = min(chg5 / atr_v * 10, 30) if atr_v > 0 else 0
    chase     = round(min(dev_risk + atr_risk, 100))

    # Module 4: 多週期共振
    e20, e50  = _ema(closes, 20)[-1], _ema(closes, 50)[-1]
    e100      = _ema(closes, 100)[-1]
    e250      = _ema(closes, min(250, n))[-1]
    e400      = _ema(closes, min(400, n))[-1]
    mh        = _macd_hist(closes)[-1]
    resonance = sum([
        closes[-1] > e20 and e20 > e50,
        closes[-1] > e100 and e100 > e250,
        closes[-1] > e400,
        mh > 0,
        rsi_val > 50,
    ]) * 20

    # Entry gate (V2 strict)
    if not (smart > 65 and bear_p < 25 and chase < 45 and resonance >= 80):
        return None

    # Stop / targets
    stop     = round(max(min(lows[-10:]) - atr_v * 1.5, closes[-1] * 0.88), 2)
    risk_amt = closes[-1] - stop
    if risk_amt <= 0:
        return None
    r1 = round(closes[-1] + risk_amt * 1.5, 2)
    r2 = round(closes[-1] + risk_amt * 4.0, 2)

    # Volume surge annotation
    vol_surges = _mon.detect_volume_surge(volumes)

    reasons = []
    if smart > 70:          reasons.append(f"主力控盤 {smart}")
    if resonance >= 80:     reasons.append(f"多週期共振 {resonance}%")
    if bw_curr < bw_avg * 0.80: reasons.append("BB緊縮")
    if vol_surges['surge']: reasons.append(vol_surges['label'])

    return {
        "symbol":        symbol,
        "price":         closes[-1],
        "entry":         round(closes[-1] * 1.001, 2),
        "stop":          stop,
        "r1_target":     r1,
        "target":        r2,
        "risk_pct":      round(risk_amt / closes[-1] * 100, 2),
        "rr":            4.0,
        "atr":           round(atr_v, 4),
        "smart_money":   smart,
        "bear_pressure": bear_p,
        "chase_risk":    chase,
        "resonance":     resonance,
        "vol_ratio":     round(vol_ratio, 2),
        "reason":        "；".join(reasons) or "V2嚴格四模組",
        "scanned_at":    datetime.now(timezone.utc).isoformat(),
    }


# ── Scan Runner ───────────────────────────────────────────────────────────────

def run_scan(symbols: Optional[list[str]] = None, skip_regime: bool = False) -> list[dict]:
    """
    Fetch OHLCV + evaluate signals. Updates _scan_candidates and state.
    Skips all signals in bear regime unless skip_regime=True.
    """
    global _scan_candidates, _last_scan_ts
    syms = symbols or SCAN_SYMBOLS

    # Market regime check
    if not skip_regime:
        regime = _get_regime()
        if not regime.get('bullish', True):
            _log("⚠️  大盤空頭，跳過全部訊號（若要強制掃描請使用 skip_regime=True）")
            _mon.alert_warn("大盤空頭：暫停自動掃描")
            _scan_candidates = []
            _mon.get_state().set_candidates([])
            return []

    _log(f"🔍 開始掃描 {len(syms)} 支股票…")
    candidates = []

    for i, sym in enumerate(syms):
        # Skip benchmark ETFs as trade candidates
        if sym in ('SPY', 'QQQ', 'XLK', 'GLD', 'TLT'):
            continue
        try:
            ohlcv = _fetch_ohlcv(sym)
            if ohlcv:
                sig = _evaluate_signal(sym, ohlcv)
                if sig:
                    candidates.append(sig)
                    _log(f"  ✅ {sym}: 主力{sig['smart_money']} 空壓{sig['bear_pressure']} 追高{sig['chase_risk']} 共振{sig['resonance']}% 量比{sig['vol_ratio']}x")
        except Exception as e:
            _log(f"  ❌ {sym}: {e}")
        if (i + 1) % 10 == 0:
            _log(f"  進度 {i + 1}/{len(syms)}…")
        time.sleep(0.1)   # gentle rate limit

    # Score: resonance + smart_money - chase_risk
    candidates.sort(
        key=lambda x: x['resonance'] + x['smart_money'] - x['chase_risk'],
        reverse=True,
    )
    _scan_candidates = candidates
    _last_scan_ts = time.time()
    _mon.get_state().set_candidates(candidates)
    _log(f"✅ 掃描完成：{len(candidates)} 支通過 V2 嚴格條件")
    return candidates


# ── Intraday Position Monitor ─────────────────────────────────────────────────

def task_position_monitor():
    """
    Every 5 min during market hours:
    - Fetch current prices for all tracked positions
    - Check if stop or target was hit
    - Update trailing stops
    - Enforce daily loss kill switch
    """
    if not _mon.is_market_open():
        return

    from trader import get_engine
    engine    = get_engine()
    state     = _mon.get_state()
    positions = engine.get_positions()
    acct      = engine.get_account()
    equity    = acct.get('equity', 0)
    sod_eq    = state.get('sod_equity')

    # Set start-of-day equity on first monitor call
    if sod_eq is None and equity > 0:
        state.set('sod_equity', equity)
        sod_eq = equity

    # Kill switch check
    if _mon.check_kill_switch(equity, sod_eq):
        return

    if not positions:
        return

    _log(f"📡 部位監控：{len(positions)} 個部位  資產 ${equity:,.0f}")

    for pos in positions:
        sym   = pos['symbol']
        curr  = pos.get('current_price', 0)
        entry = pos.get('avg_entry', curr)
        if not curr or not entry:
            continue

        # Retrieve persisted position data (stop, r1, leg1_closed)
        tracked = state.get_positions().get(sym, {})
        stop    = tracked.get('stop', curr * 0.92)
        r1      = tracked.get('r1', entry * 1.05)
        atr     = tracked.get('atr', curr * 0.02)
        shares  = pos.get('qty', 0)
        leg1    = tracked.get('leg1_closed', False)

        pnl = _mon.calc_pnl(entry, curr, stop, shares)
        _log(f"  {sym}: ${curr:.2f}  P&L {pnl['pnl_pct']:+.1f}%  {pnl['r_multiple']:+.2f}R  止損 ${stop:.2f}")

        # Leg-1 target hit
        if not leg1 and curr >= r1 and shares > 0:
            half = max(1, int(shares / 2))
            result = engine.close_position(sym, qty=half)
            if result.get('ok'):
                state.update_position(sym, {'leg1_closed': True})
                leg1 = True
                _mon.alert_info(f"🎯 {sym}: L1 目標達成 +{pnl['pnl_pct']:.1f}%，賣出 {half}股")
                _log(f"  🎯 {sym}: L1 鎖利 {half}股 @ ${curr:.2f}")

        # Stop loss hit → close all
        if curr <= stop:
            result = engine.close_position(sym)
            if result.get('ok'):
                state.remove_position(sym)
                _mon.alert_warn(f"🛑 {sym}: 停損觸發 ${curr:.2f}  P&L {pnl['pnl_pct']:+.1f}%")
                _log(f"  🛑 {sym}: 停損出場 @ ${curr:.2f}")
            continue

        # Update trailing stop
        new_stop = _mon.update_trailing_stop(stop, curr, atr, mult=2.0, leg1_closed=leg1)
        if new_stop > stop:
            state.update_position(sym, {'stop': new_stop})
            _log(f"  ↑ {sym}: 止損上移 ${stop:.2f} → ${new_stop:.2f}")


# ── Scheduled Task Functions ──────────────────────────────────────────────────

def task_pre_market_scan():
    """09:00 ET — regime check + full scan + build watchlist."""
    _log("🌅 盤前掃描啟動…")
    try:
        _get_regime()
        run_scan()
        _mon.alert_info(f"盤前掃描完成：{len(_scan_candidates)} 個候選標的")
    except Exception as e:
        _log(f"盤前掃描錯誤：{e}")
        _mon.alert_warn(f"盤前掃描失敗：{e}")


def task_market_open_exec():
    """09:32 ET — execute top-ranked candidates."""
    from trader import get_engine
    from risk_manager import get_risk_manager

    state = _mon.get_state()
    _log("📊 開盤執行信號…")

    if state.is_kill_switch():
        _log("🛑 Kill switch 已觸發，跳過開盤執行")
        return

    if not _scan_candidates:
        _log("  沒有候選標的，跳過")
        return

    engine   = get_engine()
    rm       = get_risk_manager()
    acct     = engine.get_account()
    equity   = acct.get('equity', 0)
    open_pos = len(engine.get_positions())

    # Set start-of-day equity
    if state.get('sod_equity') is None and equity > 0:
        state.set('sod_equity', equity)

    executed = 0
    for sig in _scan_candidates[:5]:
        if state.is_kill_switch():
            break
        ok, shares, details = rm.validate_order(
            portfolio_value    = equity,
            start_of_day_value = equity,
            open_positions     = open_pos + executed,
            entry              = sig['entry'],
            stop               = sig['stop'],
        )
        if not ok:
            _log(f"  ⛔ {sig['symbol']}: {details.get('reason', '風控拒絕')}")
            continue

        result = engine.submit_order(
            symbol      = sig['symbol'],
            shares      = shares,
            entry       = sig['entry'],
            stop_price  = sig['stop'],
            take_profit = sig['target'],
            note        = sig['reason'],
        )
        if result.get('ok'):
            _executed_today.append({**result, 'details': details})
            # Persist position tracking data
            state.update_position(sig['symbol'], {
                'entry':       sig['entry'],
                'stop':        sig['stop'],
                'r1':          sig['r1_target'],
                'atr':         sig.get('atr', sig['entry'] * 0.02),
                'leg1_closed': False,
                'shares':      shares,
            })
            executed += 1
            _mon.alert_info(f"✅ 開盤買入 {sig['symbol']} {shares}股 @ ${sig['entry']:.2f}  止損${sig['stop']:.2f}")
            _log(f"  ✅ {sig['symbol']}: BUY {shares}股 @ ${sig['entry']:.2f}")
        else:
            _log(f"  ❌ {sig['symbol']}: {result.get('error', '下單失敗')}")

    _log(f"開盤執行完畢：{executed} 筆訂單")
    state.set('executed_today', _executed_today)


def task_midday_scan():
    """11:30 ET — rescan for new setups that emerged mid-session."""
    if not _mon.is_market_open():
        return
    _log("☀️  午盤補充掃描…")
    try:
        regime = _get_regime()
        if not regime.get('bullish', True):
            _log("  大盤空頭，午盤掃描略過")
            return
        # Scan a subset (AI + semi + cyber) for speed
        subset = [
            "NVDA","AMD","AVGO","MRVL","MSFT","META","PLTR",
            "CRWD","PANW","ZS","LLY","NVO","COIN","TSLA",
        ]
        new_sig = run_scan(subset, skip_regime=True)
        if new_sig:
            _mon.alert_info(f"午盤新訊號：{', '.join(s['symbol'] for s in new_sig[:3])}")
    except Exception as e:
        _log(f"午盤掃描錯誤：{e}")


def task_pre_close_review():
    """15:30 ET — tighten stops on positions held < 0.5R; flag time-stops."""
    if not _mon.is_market_open():
        return

    from trader import get_engine
    engine   = get_engine()
    state    = _mon.get_state()
    positions = engine.get_positions()

    _log(f"🕞 盤前收盤檢查：{len(positions)} 個部位")
    for pos in positions:
        sym   = pos['symbol']
        curr  = pos.get('current_price', 0)
        entry = pos.get('avg_entry', curr)
        tracked = state.get_positions().get(sym, {})
        stop    = tracked.get('stop', curr * 0.95)
        atr     = tracked.get('atr', curr * 0.02)
        risk    = entry - stop if stop < entry else entry * 0.05
        r_mult  = (curr - entry) / risk if risk > 0 else 0

        # If position is < 0.3R and we're 30 min from close → time stop
        if r_mult < 0.3:
            _log(f"  ⏰ {sym}: 時間止損觸發（{r_mult:.2f}R < 0.3R），盤後平倉")
            _mon.alert_warn(f"⏰ {sym}: 時間止損（{r_mult:.2f}R）")
            result = engine.close_position(sym)
            if result.get('ok'):
                state.remove_position(sym)
        elif r_mult >= 1.0:
            # Tighten trailing stop to 1×ATR from current price
            tight_stop = curr - atr * 1.0
            new_stop = max(stop, tight_stop)
            if new_stop > stop:
                state.update_position(sym, {'stop': new_stop})
                _log(f"  🔒 {sym}: 止損收緊至 ${new_stop:.2f}（{r_mult:.1f}R 保護）")


def task_eod_summary():
    """16:30 ET — log P&L, reset daily state, prepare overnight watchlist."""
    from trader import get_engine
    engine    = get_engine()
    state     = _mon.get_state()
    positions = engine.get_positions()
    acct      = engine.get_account()
    equity    = acct.get('equity', 0)
    sod_eq    = state.get('sod_equity') or equity
    daily_pnl = (equity - sod_eq) / sod_eq * 100 if sod_eq else 0

    _log(f"🌆 收盤總結：持倉 {len(positions)} 個  日損益 {daily_pnl:+.1f}%  資產 ${equity:,.0f}")
    _mon.alert_info(f"收盤：日損益 {daily_pnl:+.1f}%  資產 ${equity:,.0f}  持倉 {len(positions)}個")

    for pos in positions:
        sym = pos['symbol']
        pnl = pos.get('unrealized_pnl_pct', 0)
        _log(f"  {sym}: P&L {pnl:+.1f}%  市值 ${pos.get('market_value', 0):,.0f}")

    # Reset daily state for tomorrow
    state.set('sod_equity', None)
    state.set('daily_pnl_pct', round(daily_pnl, 2))
    state.reset_kill_switch()

    # Pre-build overnight watchlist (non-blocking)
    _log("📋 準備隔夜觀察清單…")
    try:
        run_scan(skip_regime=True)
        _log(f"  隔夜觀察：{len(_scan_candidates)} 個候選")
    except Exception as e:
        _log(f"  隔夜掃描失敗：{e}")


def task_health_ping():
    """Every 60 min (off hours) — log system status."""
    health = _mon.system_health()
    mkt    = health['market']
    _log(
        f"💚 系統健康：{mkt['label']}  "
        f"候選 {health['candidates']}  "
        f"持倉 {health['positions']}  "
        f"警報 {health['alerts_total']}"
    )


# ── APScheduler Setup ─────────────────────────────────────────────────────────

_scheduler     = None
_scheduler_lock = threading.Lock()


def start_scheduler():
    """
    Start the background scheduler. Only runs if SCHEDULER_ENABLE=true.
    Safe to call multiple times (idempotent).
    """
    global _scheduler
    if os.environ.get("SCHEDULER_ENABLE", "false").lower() != "true":
        logger.info("Scheduler disabled — set SCHEDULER_ENABLE=true to enable")
        return

    with _scheduler_lock:
        if _scheduler is not None:
            return

        try:
            from apscheduler.schedulers.background import BackgroundScheduler
            from apscheduler.triggers.cron import CronTrigger
            from apscheduler.triggers.interval import IntervalTrigger
        except ImportError:
            logger.warning("apscheduler not installed — scheduler disabled. pip install apscheduler")
            return

        tz = "America/New_York"
        _scheduler = BackgroundScheduler(timezone=tz)

        # ── Daily fixed tasks ─────────────────────────────────────────────
        _scheduler.add_job(
            task_pre_market_scan, CronTrigger(
                day_of_week="mon-fri", hour=9, minute=0, timezone=tz),
            id="pre_market_scan", replace_existing=True)

        _scheduler.add_job(
            task_market_open_exec, CronTrigger(
                day_of_week="mon-fri", hour=9, minute=32, timezone=tz),
            id="market_open_exec", replace_existing=True)

        _scheduler.add_job(
            task_midday_scan, CronTrigger(
                day_of_week="mon-fri", hour=11, minute=30, timezone=tz),
            id="midday_scan", replace_existing=True)

        _scheduler.add_job(
            task_pre_close_review, CronTrigger(
                day_of_week="mon-fri", hour=15, minute=30, timezone=tz),
            id="pre_close_review", replace_existing=True)

        _scheduler.add_job(
            task_eod_summary, CronTrigger(
                day_of_week="mon-fri", hour=16, minute=30, timezone=tz),
            id="eod_summary", replace_existing=True)

        # ── Intraday monitor: every 5 min during market hours ─────────────
        _scheduler.add_job(
            task_position_monitor,
            IntervalTrigger(minutes=5, timezone=tz),
            id="position_monitor", replace_existing=True)

        # ── Off-hours health ping: every 60 min ───────────────────────────
        _scheduler.add_job(
            task_health_ping,
            IntervalTrigger(minutes=60, timezone=tz),
            id="health_ping", replace_existing=True)

        _scheduler.start()
        _log(
            "⏰ 排程器啟動：09:00盤前 / 09:32開盤 / 11:30午盤 / "
            "15:30盤前收盤 / 16:30收盤總結 / 每5分鐘部位監控"
        )
        _mon.alert_info("排程器啟動，24/7監控模式開啟")


def stop_scheduler():
    global _scheduler
    with _scheduler_lock:
        if _scheduler and getattr(_scheduler, 'running', False):
            _scheduler.shutdown(wait=False)
            _scheduler = None
            _log("排程器已停止")


def get_scheduler_status() -> dict:
    return {
        "running":        _scheduler is not None and getattr(_scheduler, 'running', False),
        "enabled":        os.environ.get("SCHEDULER_ENABLE", "false").lower() == "true",
        "last_scan":      (datetime.utcfromtimestamp(_last_scan_ts).strftime("%H:%M:%S UTC")
                           if _last_scan_ts else "尚未掃描"),
        "candidates":     len(_scan_candidates),
        "executed_today": len(_executed_today),
        "log":            _scan_log[-60:],
        "market":         _mon.market_status(),
        "regime":         _regime_cache.get('regime', {}),
    }


def trigger_scan_now(symbols: Optional[list[str]] = None) -> list[dict]:
    """Called by /api/scheduler/scan for on-demand scanning."""
    return run_scan(symbols, skip_regime=False)
