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
            out[i] = v * k + out[i - 1] * (1 - k)
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
    # Only compute on days with actual position (non-flat equity)
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

    return {
        "total_return":   round(total_ret, 2),
        "annual_return":  round(annual_ret, 2),
        "win_rate":       round(win_rate, 1),
        "num_trades":     len(trades),
        "avg_win":        round(avg_win, 2),
        "avg_loss":       round(avg_loss, 2),
        "profit_factor":  round(profit_factor, 2) if profit_factor != float('inf') else 999,
        "max_drawdown":   _max_drawdown(equity),
        "sharpe":         _sharpe(equity),
        "avg_holding":    round(avg_holding, 1),
        "bh_return":      round(bh_ret, 2),
        "bh_equity":      bh_equity,
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


# ── Dispatcher ────────────────────────────────────────────────────────────────

STRATEGIES = {
    "rsi":       strategy_rsi,
    "macd":      strategy_macd,
    "ma_cross":  strategy_ma_cross,
    "bollinger": strategy_bollinger,
    "combined":  strategy_combined,
}

STRATEGY_NAMES = {
    "rsi":       "RSI 反轉策略",
    "macd":      "MACD 趨勢策略",
    "ma_cross":  "MA 雙線交叉策略",
    "bollinger": "布林通道策略",
    "combined":  "多指標綜合策略",
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
