"""
Backtesting engine.
Receives OHLCV list from the frontend and simulates multiple strategies.
No look-ahead bias: signals generated from day i are executed at close of day i.
Commission: 0.1% per trade (buy + sell).
"""
from __future__ import annotations
import math
from typing import Any

# ── Indicator helpers (pure Python, no pandas) ────────────────────────────────

def _ema(arr: list, period: int) -> list:
    k = 2 / (period + 1)
    out = [None] * len(arr)
    s, cnt = 0.0, 0
    for i, v in enumerate(arr):
        if v is None:
            continue
        if cnt < period:
            s += v; cnt += 1
            if cnt == period:
                out[i] = s / period
        else:
            out[i] = v * k + (out[i - 1] or v) * (1 - k)
    # Fill initial Nones with first available value for multi-TF EMA
    first = next((v for v in out if v is not None), None)
    if first is not None:
        out = [v if v is not None else first for v in out]
    return out


def _sma(arr: list, period: int) -> list:
    out = [None] * len(arr)
    for i in range(period - 1, len(arr)):
        sl = arr[i - period + 1:i + 1]
        if None in sl:
            continue
        out[i] = sum(sl) / period
    return out


def _rsi(closes: list, period: int = 14) -> list:
    out = [None] * len(closes)
    avg_gain = avg_loss = 0.0
    for i in range(1, period + 1):
        d = closes[i] - closes[i - 1]
        if d > 0: avg_gain += d
        else:     avg_loss -= d
    avg_gain /= period; avg_loss /= period
    rs = avg_gain / avg_loss if avg_loss else float('inf')
    out[period] = 100 - 100 / (1 + rs)
    for i in range(period + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        avg_gain = (avg_gain * (period - 1) + max(d, 0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-d, 0)) / period
        rs = avg_gain / avg_loss if avg_loss else 100
        out[i] = 100 - 100 / (1 + rs)
    return out


def _macd(closes: list, fast=12, slow=26, signal=9):
    ef = _ema(closes, fast)
    es = _ema(closes, slow)
    ml = [ef[i] - es[i] if ef[i] is not None and es[i] is not None else None for i in range(len(closes))]
    sl = _ema(ml, signal)
    hist = [ml[i] - sl[i] if ml[i] is not None and sl[i] is not None else None for i in range(len(closes))]
    return ml, sl, hist


def _bb(closes: list, period=20, std_dev=2):
    mid = _sma(closes, period)
    upper = [None] * len(closes)
    lower = [None] * len(closes)
    pct_b = [None] * len(closes)
    for i in range(period - 1, len(closes)):
        sl = closes[i - period + 1:i + 1]
        if None in sl or mid[i] is None:
            continue
        std = math.sqrt(sum((v - mid[i]) ** 2 for v in sl) / period)
        upper[i] = mid[i] + std_dev * std
        lower[i] = mid[i] - std_dev * std
        rng = upper[i] - lower[i]
        pct_b[i] = (closes[i] - lower[i]) / rng if rng > 0 else 0.5
    return upper, mid, lower, pct_b


# ── Trade simulation ──────────────────────────────────────────────────────────

COMMISSION = 0.001   # 0.1% per leg


def _simulate(closes: list, dates: list, buy_sig: list, sell_sig: list,
              initial: float = 100_000.0) -> dict:
    """
    Long-only simulation. Signals fire at day i; execution at close of day i.
    Returns trades list and daily equity curve.
    """
    equity = [initial] * len(closes)
    cash = initial
    position = 0.0     # shares held
    entry_price = 0.0
    entry_idx = 0
    trades = []

    for i in range(len(closes)):
        c = closes[i]
        equity[i] = cash + position * c

        if position == 0 and buy_sig[i]:
            shares = cash * (1 - COMMISSION) / c
            position = shares
            entry_price = c
            entry_idx = i
            cash = 0.0

        elif position > 0 and (sell_sig[i] or i == len(closes) - 1):
            proceeds = position * c * (1 - COMMISSION)
            pnl_pct = (c / entry_price - 1) * 100 - COMMISSION * 200
            holding = i - entry_idx
            trades.append({
                "entry_date":  dates[entry_idx],
                "exit_date":   dates[i],
                "entry_price": round(entry_price, 4),
                "exit_price":  round(c, 4),
                "pnl_pct":     round(pnl_pct, 2),
                "holding_days": holding,
                "win":         pnl_pct > 0,
            })
            cash = proceeds
            position = 0.0
            equity[i] = cash

    return {"trades": trades, "equity": equity}


# ── Max drawdown ───────────────────────────────────────────────────────────────

def _max_drawdown(equity: list) -> float:
    peak = equity[0]
    max_dd = 0.0
    for v in equity:
        if v > peak:
            peak = v
        dd = (v - peak) / peak * 100
        if dd < max_dd:
            max_dd = dd
    return round(max_dd, 2)


def _sharpe(equity: list, risk_free=0.02) -> float:
    if len(equity) < 2:
        return 0.0
    rets = [(equity[i] / equity[i - 1] - 1) for i in range(1, len(equity))
            if equity[i - 1] > 0 and equity[i] != equity[i - 1]]
    if len(rets) < 5:
        return 0.0
    rf_daily = risk_free / 252
    excess = [r - rf_daily for r in rets]
    mean_e = sum(excess) / len(excess)
    variance = sum((r - mean_e) ** 2 for r in excess) / len(excess)
    std = math.sqrt(variance) if variance > 0 else 0
    if std == 0:
        return 0.0
    return round(mean_e / std * math.sqrt(252), 2)


def _sortino(equity: list, risk_free=0.02) -> float:
    """Sortino ratio — only penalises downside volatility."""
    if len(equity) < 2:
        return 0.0
    rets = [(equity[i] / equity[i - 1] - 1) for i in range(1, len(equity))
            if equity[i - 1] > 0 and equity[i] != equity[i - 1]]
    if len(rets) < 5:
        return 0.0
    rf_daily = risk_free / 252
    excess = [r - rf_daily for r in rets]
    mean_e = sum(excess) / len(excess)
    downside = [r for r in excess if r < 0]
    if not downside:
        return 9.99
    down_var = sum(r ** 2 for r in downside) / len(downside)
    down_std = math.sqrt(down_var)
    if down_std == 0:
        return 9.99
    return round(mean_e / down_std * math.sqrt(252), 2)


def _calmar(equity: list, initial: float) -> float:
    """Calmar ratio = annualised return / max drawdown."""
    n_days = len(equity)
    if n_days < 2:
        return 0.0
    annual_ret = ((equity[-1] / initial) ** (252 / n_days) - 1) * 100
    mdd = abs(_max_drawdown(equity))
    return round(annual_ret / mdd, 2) if mdd > 0 else 9.99


def _stats(trades: list, equity: list, initial: float, closes: list, dates: list) -> dict:
    total_ret = (equity[-1] / initial - 1) * 100
    n_days = len(closes)
    annual_ret = ((equity[-1] / initial) ** (252 / max(n_days, 1)) - 1) * 100

    wins = [t for t in trades if t["win"]]
    losses = [t for t in trades if not t["win"]]
    win_rate = len(wins) / len(trades) * 100 if trades else 0
    avg_win = sum(t["pnl_pct"] for t in wins) / len(wins) if wins else 0
    avg_loss = sum(t["pnl_pct"] for t in losses) / len(losses) if losses else 0
    gross_profit = sum(t["pnl_pct"] for t in wins)
    gross_loss = abs(sum(t["pnl_pct"] for t in losses))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')
    avg_holding = sum(t["holding_days"] for t in trades) / len(trades) if trades else 0

    # buy & hold
    bh_ret = (closes[-1] / closes[0] - 1) * 100
    bh_equity = [initial * (c / closes[0]) for c in closes]

    # Consecutive wins/losses
    max_consec_win = max_consec_loss = cur_w = cur_l = 0
    for t in trades:
        if t["win"]:  cur_w += 1; cur_l = 0
        else:         cur_l += 1; cur_w = 0
        max_consec_win  = max(max_consec_win,  cur_w)
        max_consec_loss = max(max_consec_loss, cur_l)

    # Expectancy per trade (in %)
    expectancy = round(win_rate / 100 * avg_win + (1 - win_rate / 100) * avg_loss, 2) if trades else 0

    return {
        "total_return":       round(total_ret, 2),
        "annual_return":      round(annual_ret, 2),
        "win_rate":           round(win_rate, 1),
        "num_trades":         len(trades),
        "avg_win":            round(avg_win, 2),
        "avg_loss":           round(avg_loss, 2),
        "profit_factor":      round(profit_factor, 2) if profit_factor != float('inf') else 999,
        "max_drawdown":       _max_drawdown(equity),
        "sharpe":             _sharpe(equity),
        "sortino":            _sortino(equity),
        "calmar":             _calmar(equity, initial),
        "expectancy":         expectancy,
        "max_consec_win":     max_consec_win,
        "max_consec_loss":    max_consec_loss,
        "avg_holding":        round(avg_holding, 1),
        "bh_return":          round(bh_ret, 2),
        "bh_equity":          bh_equity,
    }


# ── Strategy entry points ─────────────────────────────────────────────────────

def _run(ohlcv, buy_sig, sell_sig, initial=100_000.0):
    closes = [d["close"] for d in ohlcv]
    dates  = [d["date"]  for d in ohlcv]
    sim = _simulate(closes, dates, buy_sig, sell_sig, initial)
    stats = _stats(sim["trades"], sim["equity"], initial, closes, dates)
    return {**sim, **stats}


def strategy_rsi(ohlcv: list, period=14, buy_thr=30, sell_thr=70) -> dict:
    """Buy when RSI exits oversold (crosses UP through buy_thr). Sell when RSI exits overbought."""
    closes = [d["close"] for d in ohlcv]
    rsi = _rsi(closes, period)
    in_pos = False
    buy_sig  = [False] * len(closes)
    sell_sig = [False] * len(closes)
    for i in range(1, len(closes)):
        prev, curr = rsi[i - 1], rsi[i]
        if prev is None or curr is None:
            continue
        # Buy: RSI crosses UP through buy_thr (exits oversold zone)
        if not in_pos and prev < buy_thr and curr >= buy_thr:
            buy_sig[i] = True; in_pos = True
        # Sell: RSI crosses DOWN through sell_thr (exits overbought zone)
        elif in_pos and prev > sell_thr and curr <= sell_thr:
            sell_sig[i] = True; in_pos = False
    return _run(ohlcv, buy_sig, sell_sig)


def strategy_macd(ohlcv: list, fast=12, slow=26, signal=9) -> dict:
    closes = [d["close"] for d in ohlcv]
    _, _, hist = _macd(closes, fast, slow, signal)
    in_pos = False
    buy_sig  = [False] * len(closes)
    sell_sig = [False] * len(closes)
    for i in range(1, len(closes)):
        prev, curr = hist[i - 1], hist[i]
        if prev is None or curr is None:
            continue
        if not in_pos and prev <= 0 and curr > 0:
            buy_sig[i] = True; in_pos = True
        elif in_pos and prev >= 0 and curr < 0:
            sell_sig[i] = True; in_pos = False
    return _run(ohlcv, buy_sig, sell_sig)


def strategy_ma_cross(ohlcv: list, fast=20, slow=60) -> dict:
    """Golden/death cross on SMA fast vs SMA slow."""
    closes = [d["close"] for d in ohlcv]
    fast_ma = _sma(closes, fast)
    slow_ma = _sma(closes, slow)
    in_pos = False
    buy_sig  = [False] * len(closes)
    sell_sig = [False] * len(closes)
    for i in range(1, len(closes)):
        pf, cf = fast_ma[i - 1], fast_ma[i]
        ps, cs = slow_ma[i - 1], slow_ma[i]
        if None in (pf, cf, ps, cs):
            continue
        # Golden cross: fast crosses ABOVE slow
        if not in_pos and pf < ps and cf >= cs:
            buy_sig[i] = True; in_pos = True
        # Death cross: fast crosses BELOW slow
        elif in_pos and pf > ps and cf <= cs:
            sell_sig[i] = True; in_pos = False
    return _run(ohlcv, buy_sig, sell_sig)


def strategy_bollinger(ohlcv: list, period=20, std_dev=2) -> dict:
    """Buy when price touches lower band (%B < 0.1); sell when it touches upper band (%B > 0.9)."""
    closes = [d["close"] for d in ohlcv]
    upper, mid, lower, pct_b = _bb(closes, period, std_dev)
    in_pos = False
    buy_sig  = [False] * len(closes)
    sell_sig = [False] * len(closes)
    for i in range(1, len(closes)):
        pp, cp = pct_b[i - 1], pct_b[i]
        if pp is None or cp is None:
            continue
        # Enter when price crosses back above lower band (bounce from oversold)
        if not in_pos and pp < 0.1 and cp >= 0.1:
            buy_sig[i] = True; in_pos = True
        # Exit when price crosses back below upper band (reversal from overbought)
        elif in_pos and pp > 0.9 and cp <= 0.9:
            sell_sig[i] = True; in_pos = False
    return _run(ohlcv, buy_sig, sell_sig)


def strategy_combined(ohlcv: list, rsi_period=14, fast_ma=20, slow_ma=60) -> dict:
    """Multi-indicator: MACD turns positive AND RSI < 60 AND price > fast MA. Sell on reversal."""
    closes = [d["close"] for d in ohlcv]
    rsi    = _rsi(closes, rsi_period)
    _, _, hist = _macd(closes)
    ma20   = _sma(closes, fast_ma)
    in_pos = False
    buy_sig  = [False] * len(closes)
    sell_sig = [False] * len(closes)
    for i in range(1, len(closes)):
        r, h, m, c = rsi[i], hist[i], ma20[i], closes[i]
        ph = hist[i - 1]
        if None in (r, h, m, ph):
            continue
        # Buy: MACD histogram turns positive AND RSI not overbought AND above MA
        if not in_pos and ph <= 0 and h > 0 and r < 65 and c > m:
            buy_sig[i] = True; in_pos = True
        # Sell: MACD histogram turns negative OR RSI very overbought
        elif in_pos and (h < 0 or r > 75):
            sell_sig[i] = True; in_pos = False
    return _run(ohlcv, buy_sig, sell_sig)


def strategy_decision_core(
    ohlcv: list,
    sm_threshold:  int = 60,    # 主力控盤 min
    bp_threshold:  int = 30,    # 空方壓力 max
    cr_threshold:  int = 55,    # 追高風險 max
    rs_threshold:  int = 60,    # 多週期共振 min (%)
) -> dict:
    """
    Decision Core 四模組策略 (mirrors TradingView Pine Script).
    Entry:  sm > sm_threshold AND bp < bp_threshold AND cr < cr_threshold AND resonance >= rs_threshold
    Exit:   cr > 75 OR bp > 65 OR ATR trailing stop hit
    """
    closes  = [d["close"]  for d in ohlcv]
    opens   = [d["open"]   for d in ohlcv]
    highs   = [d["high"]   for d in ohlcv]
    lows    = [d["low"]    for d in ohlcv]
    volumes = [d["volume"] for d in ohlcv]
    n = len(closes)

    # Pre-compute indicators
    rsi_arr   = _rsi(closes)
    _, _, mh  = _macd(closes)
    ema20_arr = _ema(closes, 20)
    ema50_arr = _ema(closes, 50)
    ema100    = _ema(closes, 100)
    ema250    = _ema(closes, 250) if n >= 250 else [closes[0]] * n
    ema400    = _ema(closes, 400) if n >= 400 else [closes[0]] * n
    sma20_arr = _sma(closes, 20)
    vol_ma_arr = _sma(volumes, 20)

    # ATR
    atr_arr = [0.0] * n
    for i in range(1, n):
        tr = max(highs[i]-lows[i], abs(highs[i]-closes[i-1]), abs(lows[i]-closes[i-1]))
        atr_arr[i] = tr if i < 14 else (atr_arr[i-1] * 13 + tr) / 14

    # OBV
    obv = [0.0]
    for i in range(1, n):
        obv.append(obv[-1] + volumes[i] if closes[i] > closes[i-1]
                   else obv[-1] - volumes[i] if closes[i] < closes[i-1]
                   else obv[-1])
    obv_ema = _ema(obv, 20)

    buy_sig  = [False] * n
    sell_sig = [False] * n
    in_pos   = False
    trail_stop = 0.0

    for i in range(max(60, 400 if n >= 400 else 250), n):
        c = closes[i]; rsi = rsi_arr[i]; mhv = mh[i] or 0
        vm = vol_ma_arr[i] or 1; vr = volumes[i] / vm

        # ── Module 1: Smart Money ────────────────────────────────────────────
        oe = obv_ema[i] or 1
        obv_norm   = min(max((obv[i] - oe) / abs(oe) * 100, -50), 50)
        vol_score  = min(vr * 20, 50)
        pc5        = (c - closes[i-5]) / (closes[i-5] or 1) * 100 if i >= 5 else 0
        vol_conf   = min(pc5 * vr * 5, 50) if pc5 > 0 else 0
        smart_money = round(min(max((obv_norm + vol_score + vol_conf) / 3 * 2, 0), 100))

        # ── Module 2: Bear Pressure ─────────────────────────────────────────
        is_dn      = c < opens[i]
        bear_ratio = 100.0 if is_dn else 0.0
        bear_rsi   = (50 - rsi) * 2 if rsi < 50 else 0
        bear_pres  = round(min(bear_ratio * 0.6 + bear_rsi * 0.4, 100))

        # ── Module 3: Chase Risk ────────────────────────────────────────────
        rsi_risk  = (rsi - 70) * 3 if rsi > 70 else 0
        ma20_v    = sma20_arr[i] or c
        dev_risk  = max((c - ma20_v) / ma20_v * 100 * 2, 0)
        chg5      = c - closes[i-5] if i >= 5 else 0
        atr_risk  = min(chg5 / atr_arr[i] * 10, 30) if atr_arr[i] > 0 else 0
        chase_risk = round(min(rsi_risk + dev_risk + atr_risk, 100))

        # ── Module 4: Resonance ─────────────────────────────────────────────
        daily_b   = c > ema20_arr[i] and ema20_arr[i] > ema50_arr[i]
        weekly_b  = c > ema100[i] and ema100[i] > ema250[i]
        monthly_b = c > ema400[i]
        resonance = sum([daily_b, weekly_b, monthly_b, mhv > 0, rsi > 50]) * 20

        # ── Entry / Exit ────────────────────────────────────────────────────
        if not in_pos:
            if (smart_money > sm_threshold and bear_pres < bp_threshold
                    and chase_risk < cr_threshold and resonance >= rs_threshold):
                buy_sig[i] = True
                in_pos = True
                trail_stop = c - atr_arr[i] * 1.5
        else:
            # Update trailing stop
            new_trail = c - atr_arr[i] * 1.5
            trail_stop = max(trail_stop, new_trail)

            exit_signal = (
                chase_risk > 75 or
                bear_pres  > 65 or
                c < trail_stop
            )
            if exit_signal:
                sell_sig[i] = True
                in_pos = False

    return _run(ohlcv, buy_sig, sell_sig)


def walk_forward(
    ohlcv: list,
    strategy: str = "decision_core",
    train_pct: float = 0.7,
    params: dict | None = None,
) -> dict:
    """
    Walk-forward test: train on first train_pct of data, test on remainder.
    Returns in-sample, out-of-sample, and combined stats.
    """
    n = len(ohlcv)
    split = int(n * train_pct)
    if split < 60 or (n - split) < 30:
        return {"error": "數據不足以進行走向前測試（需至少 200 天）"}

    train_data = ohlcv[:split]
    test_data  = ohlcv[split:]

    fn = STRATEGIES.get(strategy, strategy_decision_core)
    p  = {k: v for k, v in (params or {}).items() if isinstance(v, (int, float))}

    in_sample  = fn(train_data, **p)
    out_sample = fn(test_data,  **p)

    # Robustness: out-of-sample return / in-sample return
    robustness = round(out_sample["total_return"] / in_sample["total_return"], 2) \
                 if in_sample["total_return"] != 0 else 0

    return {
        "in_sample":   {k: v for k, v in in_sample.items()  if k not in ("equity", "bh_equity", "trades")},
        "out_sample":  {k: v for k, v in out_sample.items() if k not in ("equity", "bh_equity", "trades")},
        "in_period":   f"{train_data[0]['date']} → {train_data[-1]['date']}",
        "out_period":  f"{test_data[0]['date']}  → {test_data[-1]['date']}",
        "robustness":  robustness,
        "note": ("✅ 策略穩健" if robustness > 0.5 else
                 "⚠️ 過度擬合風險" if in_sample["total_return"] > 0 else "❌ 策略整體無效"),
    }


# ── Dispatcher ────────────────────────────────────────────────────────────────

STRATEGIES = {
    "rsi":           strategy_rsi,
    "macd":          strategy_macd,
    "ma_cross":      strategy_ma_cross,
    "bollinger":     strategy_bollinger,
    "combined":      strategy_combined,
    "decision_core": strategy_decision_core,
}

STRATEGY_NAMES = {
    "rsi":           "RSI 反轉策略",
    "macd":          "MACD 趨勢策略",
    "ma_cross":      "MA 雙線交叉策略",
    "bollinger":     "布林通道策略",
    "combined":      "多指標綜合策略",
    "decision_core": "決策核心四模組策略",
}


def run(ohlcv: list[dict], strategy: str, params: dict) -> dict:
    fn = STRATEGIES.get(strategy, strategy_rsi)
    params = {k: v for k, v in params.items() if isinstance(v, (int, float))}
    result = fn(ohlcv, **params)

    # Format equity curve as relative % for chart
    initial = 100_000.0
    result["equity_pct"] = [round((v / initial - 1) * 100, 2) for v in result["equity"]]
    result["bh_pct"]     = [round((v / initial - 1) * 100, 2) for v in result["bh_equity"]]
    del result["equity"]
    del result["bh_equity"]

    # Format trades for JSON
    for t in result["trades"]:
        if hasattr(t["entry_date"], "strftime"):
            t["entry_date"] = t["entry_date"].strftime("%Y-%m-%d")
        if hasattr(t["exit_date"], "strftime"):
            t["exit_date"] = t["exit_date"].strftime("%Y-%m-%d")

    result["strategy_name"] = STRATEGY_NAMES.get(strategy, strategy)
    return result
