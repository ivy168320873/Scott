"""
Trade Cost Model — applies realistic fees, slippage, liquidity and
Taiwan circuit-breaker limits to backtest simulations.
"""
from __future__ import annotations
import math


# ── Default parameters ────────────────────────────────────────────────────────

def default_params(symbol: str) -> dict:
    """Returns a complete params dict with smart defaults based on symbol."""
    is_tw = (
        symbol.upper().endswith(".TW") or
        symbol.upper().endswith(".TWO")
    )
    return {
        "enabled":                     True,
        "is_tw":                       is_tw,
        "entry_price_mode":            "next_open",
        "exit_price_mode":             "next_open",
        # Fees
        "commission_buy":              0.001425 if is_tw else 0.0,
        "commission_sell":             0.001425 if is_tw else 0.0,
        "transaction_tax":             0.003    if is_tw else 0.0,
        "min_commission":              20.0     if is_tw else 0.0,
        # Slippage
        "slippage_mode":               "fixed_pct",
        "slippage_pct":                0.001,
        # Liquidity
        "use_liquidity_filter":        True,
        "max_position_pct_of_volume":  0.02,
        "min_dollar_volume":           1_000_000,
        # TW circuit breaker
        "strict_limit_mode":           False,
    }


# ── Price selection helpers ────────────────────────────────────────────────────

def _get_entry_price(ohlcv: list, idx: int, mode: str) -> float:
    """Returns actual entry price for day idx based on mode."""
    if mode == "next_open":
        if idx + 1 < len(ohlcv):
            return float(ohlcv[idx + 1]["open"])
        return float(ohlcv[idx]["close"])
    elif mode == "next_close":
        if idx + 1 < len(ohlcv):
            return float(ohlcv[idx + 1]["close"])
        return float(ohlcv[idx]["close"])
    elif mode == "signal_close":
        return float(ohlcv[idx]["close"])
    elif mode == "vwap_estimate":
        bar = ohlcv[idx]
        return (float(bar["high"]) + float(bar["low"]) + float(bar["close"])) / 3.0
    # default fallback
    return float(ohlcv[idx]["close"])


def _get_exit_price(ohlcv: list, idx: int, mode: str) -> float:
    """Returns actual exit price for day idx based on mode."""
    if mode == "next_open":
        if idx + 1 < len(ohlcv):
            return float(ohlcv[idx + 1]["open"])
        return float(ohlcv[idx]["close"])
    elif mode == "next_close":
        if idx + 1 < len(ohlcv):
            return float(ohlcv[idx + 1]["close"])
        return float(ohlcv[idx]["close"])
    elif mode == "signal_close":
        return float(ohlcv[idx]["close"])
    elif mode == "vwap_estimate":
        bar = ohlcv[idx]
        return (float(bar["high"]) + float(bar["low"]) + float(bar["close"])) / 3.0
    # default fallback
    return float(ohlcv[idx]["close"])


# ── Slippage calculation ───────────────────────────────────────────────────────

def _calc_slippage(
    price: float,
    mode: str,
    slippage_pct: float,
    ohlcv: list,
    idx: int,
) -> float:
    """Returns slippage amount (positive = cost)."""
    if price <= 0:
        return 0.0

    if mode == "fixed_pct":
        return max(0.0, price * slippage_pct)

    elif mode == "volume_based":
        n = len(ohlcv)
        # Use up to last 20 bars ending at idx
        start = max(0, idx - 19)
        vols = [float(ohlcv[j]["volume"]) for j in range(start, min(idx + 1, n))]
        if not vols:
            return max(0.0, price * slippage_pct)
        avg_vol = sum(vols) / len(vols)
        if avg_vol <= 0:
            return max(0.0, price * slippage_pct)
        cur_vol = float(ohlcv[idx]["volume"]) if idx < n else (vols[-1] if vols else 1.0)
        ratio = cur_vol / avg_vol
        ratio = min(ratio, 3.0)  # cap at 3
        slippage = price * slippage_pct * (0.5 + 0.5 * ratio)
        return max(0.0, slippage)

    elif mode == "volatility_based":
        # Compute ATR over last 14 bars
        n = len(ohlcv)
        atr_bars = []
        for j in range(max(1, idx - 13), min(idx + 1, n)):
            h = float(ohlcv[j]["high"])
            l = float(ohlcv[j]["low"])
            c_prev = float(ohlcv[j - 1]["close"]) if j > 0 else float(ohlcv[j]["close"])
            tr = max(h - l, abs(h - c_prev), abs(l - c_prev))
            atr_bars.append(tr)
        atr = sum(atr_bars) / len(atr_bars) if atr_bars else 0.0
        slippage = min(atr * 0.15, price * slippage_pct * 3)
        return max(0.0, slippage)

    # fallback
    return max(0.0, price * slippage_pct)


# ── TW circuit breaker detection ──────────────────────────────────────────────

def _is_limit_up(ohlcv: list, idx: int, is_tw: bool) -> bool:
    """Returns True if the day looks like a TW limit-up lock."""
    if not is_tw or idx >= len(ohlcv):
        return False
    bar = ohlcv[idx]
    c = float(bar["close"])
    o = float(bar["open"])
    h = float(bar["high"])
    return c >= o * 1.095 and c == h


def _is_limit_down(ohlcv: list, idx: int, is_tw: bool) -> bool:
    """Returns True if TW limit-down: close <= open * 0.905 and close == low."""
    if not is_tw or idx >= len(ohlcv):
        return False
    bar = ohlcv[idx]
    c = float(bar["close"])
    o = float(bar["open"])
    l = float(bar["low"])
    return c <= o * 0.905 and c == l


# ── Liquidity check ───────────────────────────────────────────────────────────

def _check_liquidity(
    ohlcv: list,
    idx: int,
    trade_value: float,
    params: dict,
) -> tuple:
    """Returns (is_liquid, warning_msg)."""
    n = len(ohlcv)
    start = max(0, idx - 19)
    dollar_vols = [
        float(ohlcv[j]["close"]) * float(ohlcv[j]["volume"])
        for j in range(start, min(idx + 1, n))
    ]
    if not dollar_vols:
        return True, ""

    avg_dollar_vol = sum(dollar_vols) / len(dollar_vols)
    min_dv = params.get("min_dollar_volume", 1_000_000)
    max_pct = params.get("max_position_pct_of_volume", 0.02)

    if avg_dollar_vol < min_dv:
        return False, "低成交金額"
    if trade_value > avg_dollar_vol * max_pct:
        return False, "交易量超過日成交2%"
    return True, ""


# ── Net equity helpers ────────────────────────────────────────────────────────

def _net_sharpe(equity: list, risk_free: float = 0.02) -> float:
    """Compute Sharpe ratio from an equity curve."""
    if len(equity) < 2:
        return 0.0
    rets = [
        (equity[i] / equity[i - 1] - 1)
        for i in range(1, len(equity))
        if equity[i - 1] > 0 and equity[i] != equity[i - 1]
    ]
    if len(rets) < 5:
        return 0.0
    rf_daily = risk_free / 252
    excess = [r - rf_daily for r in rets]
    mean_e = sum(excess) / len(excess)
    variance = sum((r - mean_e) ** 2 for r in excess) / len(excess)
    std = math.sqrt(variance) if variance > 0 else 0.0
    if std == 0.0:
        return 0.0
    return round(mean_e / std * math.sqrt(252), 2)


def _net_mdd(equity: list) -> float:
    """Compute max drawdown percentage from an equity curve."""
    if not equity:
        return 0.0
    peak = equity[0]
    max_dd = 0.0
    for v in equity:
        if v > peak:
            peak = v
        if peak > 0:
            dd = (v - peak) / peak * 100
            if dd < max_dd:
                max_dd = dd
    return round(max_dd, 2)


# ── Main cost application function ───────────────────────────────────────────

def apply_costs(
    trades: list,
    ohlcv: list,
    params: dict,
    initial: float = 100_000.0,
) -> dict:
    """
    Apply realistic trade costs to a list of trades.

    Returns dict with:
      - trades: enhanced trade list with cost fields
      - cost_summary: aggregated cost statistics
      - net_equity: equity curve adjusted for costs
    """
    # Build date → index lookup
    date_to_idx: dict = {}
    for i, bar in enumerate(ohlcv):
        d = bar.get("date", "")
        if d:
            date_to_idx[str(d)] = i

    is_tw = params.get("is_tw", False)
    entry_mode = params.get("entry_price_mode", "next_open")
    exit_mode = params.get("exit_price_mode", "next_open")
    slip_mode = params.get("slippage_mode", "fixed_pct")
    slip_pct = float(params.get("slippage_pct", 0.001))
    comm_buy = float(params.get("commission_buy", 0.0))
    comm_sell = float(params.get("commission_sell", 0.0))
    tax_rate = float(params.get("transaction_tax", 0.0))
    min_comm = float(params.get("min_commission", 0.0))
    strict_limit = bool(params.get("strict_limit_mode", False))
    use_liq = bool(params.get("use_liquidity_filter", True))

    enhanced_trades = []
    total_fees = 0.0
    total_taxes = 0.0
    total_slippage = 0.0
    liquidity_warnings = 0
    skipped_trades = 0
    skipped_trade_list = []

    # Build equity curve
    net_equity = [initial]
    portfolio_value = initial

    for trade in trades:
        # Resolve entry / exit indices
        entry_idx = trade.get("entry_idx")
        exit_idx = trade.get("exit_idx")

        # Fall back to date lookup if idx fields are missing
        if entry_idx is None:
            entry_date = str(trade.get("entry_date", ""))
            entry_idx = date_to_idx.get(entry_date)
        if exit_idx is None:
            exit_date = str(trade.get("exit_date", ""))
            exit_idx = date_to_idx.get(exit_date)

        # Fallback: use signal_close prices if indices still not found
        if entry_idx is None or exit_idx is None:
            entry_price_actual = float(trade.get("entry_price", 0.0))
            exit_price_actual = float(trade.get("exit_price", 0.0))
            entry_idx_use = None
            exit_idx_use = None
            use_fallback = True
        else:
            entry_idx_use = int(entry_idx)
            exit_idx_use = int(exit_idx)
            use_fallback = False

        if not use_fallback:
            # Clamp indices to valid range
            n = len(ohlcv)
            entry_idx_use = max(0, min(entry_idx_use, n - 1))
            exit_idx_use = max(0, min(exit_idx_use, n - 1))
            entry_price_actual = _get_entry_price(ohlcv, entry_idx_use, entry_mode)
            exit_price_actual = _get_exit_price(ohlcv, exit_idx_use, exit_mode)

        if entry_price_actual <= 0:
            entry_price_actual = float(trade.get("entry_price", 1.0)) or 1.0
        if exit_price_actual <= 0:
            exit_price_actual = float(trade.get("exit_price", 1.0)) or 1.0

        # Execution status
        execution_status = "FILLED"
        execution_note = ""

        # Check TW circuit breaker on entry
        if is_tw and not use_fallback:
            if _is_limit_up(ohlcv, entry_idx_use, is_tw):
                if strict_limit:
                    execution_status = "CANNOT_ENTER_LIMIT_UP"
                    execution_note = "漲停鎖死，無法進場"
                else:
                    execution_status = "PARTIAL"
                    execution_note = "漲停警告，部分成交"

            if _is_limit_down(ohlcv, exit_idx_use, is_tw):
                if strict_limit:
                    execution_status = "CANNOT_EXIT_LIMIT_DOWN"
                    execution_note = "跌停鎖死，無法出場"
                else:
                    if execution_status == "FILLED":
                        execution_status = "PARTIAL"
                        execution_note = "跌停警告，部分成交"

        # Estimate trade value for liquidity check
        trade_value = portfolio_value * 0.98

        # Liquidity check
        liq_ok = True
        liq_warn = ""
        if use_liq and not use_fallback:
            liq_ok_entry, liq_warn_entry = _check_liquidity(
                ohlcv, entry_idx_use, trade_value, params
            )
            liq_ok_exit, liq_warn_exit = _check_liquidity(
                ohlcv, exit_idx_use, trade_value, params
            )
            if not liq_ok_entry or not liq_ok_exit:
                liq_ok = False
                liq_warn = liq_warn_entry or liq_warn_exit
                liquidity_warnings += 1

        if not liq_ok:
            if strict_limit:
                execution_status = "LOW_LIQUIDITY"
                execution_note = liq_warn
                skipped_trades += 1
                skipped_trade_list.append({
                    "entry_date": trade.get("entry_date"),
                    "exit_date": trade.get("exit_date"),
                    "reason": liq_warn,
                })
                # Keep gross trade in list but mark it
                enhanced = dict(trade)
                enhanced.update({
                    "entry_price_actual": round(entry_price_actual, 4),
                    "exit_price_actual":  round(exit_price_actual, 4),
                    "fee_buy":            0.0,
                    "fee_sell":           0.0,
                    "tax_sell":           0.0,
                    "slippage_buy":       0.0,
                    "slippage_sell":      0.0,
                    "total_cost":         0.0,
                    "pnl_pct_gross":      float(trade.get("pnl_pct", 0.0)),
                    "pnl_pct_net":        float(trade.get("pnl_pct", 0.0)),
                    "execution_status":   execution_status,
                    "execution_note":     execution_note,
                    "win":                bool(trade.get("pnl_pct", 0.0) > 0),
                })
                enhanced_trades.append(enhanced)
                net_equity.append(net_equity[-1])
                continue
            else:
                if execution_status == "FILLED":
                    execution_status = "LOW_LIQUIDITY"
                    execution_note = liq_warn

        # Compute slippage
        slip_idx = entry_idx_use if not use_fallback else 0
        slip_exit_idx = exit_idx_use if not use_fallback else 0
        slippage_buy = _calc_slippage(
            entry_price_actual, slip_mode, slip_pct,
            ohlcv, slip_idx
        )
        slippage_sell = _calc_slippage(
            exit_price_actual, slip_mode, slip_pct,
            ohlcv, slip_exit_idx
        )

        # Compute fees and taxes
        entry_value = portfolio_value * 0.98
        # Shares estimated (approximate, for fee purposes)
        shares = entry_value / entry_price_actual if entry_price_actual > 0 else 0.0
        actual_entry_value = shares * entry_price_actual
        actual_exit_value = shares * exit_price_actual

        fee_buy = max(actual_entry_value * comm_buy, min_comm if comm_buy > 0 else 0.0)
        fee_sell = max(actual_exit_value * comm_sell, min_comm if comm_sell > 0 else 0.0)
        tax_sell = actual_exit_value * tax_rate
        tax_sell = max(0.0, tax_sell)
        fee_buy = max(0.0, fee_buy)
        fee_sell = max(0.0, fee_sell)
        slippage_buy = max(0.0, slippage_buy)
        slippage_sell = max(0.0, slippage_sell)

        total_cost_trade = fee_buy + fee_sell + tax_sell + slippage_buy + slippage_sell

        # Compute net pnl
        pnl_pct_gross = float(trade.get("pnl_pct", 0.0))

        # Net pnl: price gain minus slippage per share, minus fee/tax as pct
        if entry_price_actual > 0:
            price_gain_pct = (exit_price_actual - entry_price_actual) / entry_price_actual * 100
            slippage_pct_total = (slippage_buy + slippage_sell) / entry_price_actual * 100
            # Fees and taxes as % of entry value
            fee_tax_pct = (fee_buy + fee_sell + tax_sell) / actual_entry_value * 100 if actual_entry_value > 0 else 0.0
            pnl_pct_net = price_gain_pct - slippage_pct_total - fee_tax_pct
        else:
            pnl_pct_net = pnl_pct_gross

        pnl_pct_net = round(pnl_pct_net, 4)

        # Update portfolio value
        pnl_abs = portfolio_value * 0.98 * (pnl_pct_net / 100.0)
        portfolio_value = portfolio_value + pnl_abs
        portfolio_value = max(portfolio_value, 0.01)

        total_fees += fee_buy + fee_sell
        total_taxes += tax_sell
        total_slippage += slippage_buy + slippage_sell

        net_equity.append(portfolio_value)

        enhanced = dict(trade)
        enhanced.update({
            "entry_price_actual": round(entry_price_actual, 4),
            "exit_price_actual":  round(exit_price_actual, 4),
            "fee_buy":            round(fee_buy, 4),
            "fee_sell":           round(fee_sell, 4),
            "tax_sell":           round(tax_sell, 4),
            "slippage_buy":       round(slippage_buy, 4),
            "slippage_sell":      round(slippage_sell, 4),
            "total_cost":         round(total_cost_trade, 4),
            "pnl_pct_gross":      round(pnl_pct_gross, 4),
            "pnl_pct_net":        round(pnl_pct_net, 4),
            "execution_status":   execution_status,
            "execution_note":     execution_note,
            "win":                pnl_pct_net > 0,
        })
        enhanced_trades.append(enhanced)

    # If no trades, ensure net_equity has at least initial value
    if not enhanced_trades:
        net_equity = [initial]

    # Ensure net_equity starts at initial
    if not net_equity:
        net_equity = [initial]
    elif net_equity[0] != initial:
        net_equity = [initial] + net_equity

    total_cost_all = total_fees + total_taxes + total_slippage
    cost_drag_pct = (total_cost_all / initial * 100) if initial > 0 else 0.0
    slippage_drag_pct = (total_slippage / initial * 100) if initial > 0 else 0.0
    n_trades = len(enhanced_trades)
    avg_slippage = (total_slippage / n_trades) if n_trades > 0 else 0.0
    executable = n_trades - skipped_trades
    executable_ratio = (executable / n_trades) if n_trades > 0 else 1.0

    cost_summary = {
        "total_fees":              round(total_fees, 4),
        "total_taxes":             round(total_taxes, 4),
        "total_slippage":          round(total_slippage, 4),
        "cost_drag_pct":           round(cost_drag_pct, 4),
        "slippage_drag_pct":       round(slippage_drag_pct, 4),
        "avg_slippage_per_trade":  round(avg_slippage, 4),
        "liquidity_warnings":      liquidity_warnings,
        "skipped_trades":          skipped_trades,
        "executable_trade_ratio":  round(executable_ratio, 4),
        "skipped_trade_list":      skipped_trade_list,
    }

    return {
        "trades":      enhanced_trades,
        "cost_summary": cost_summary,
        "net_equity":  net_equity,
    }


# ── Net statistics merger ─────────────────────────────────────────────────────

def net_stats(
    gross_stats: dict,
    enhanced_trades: list,
    net_equity: list,
    initial: float,
    cost_summary: dict,
    params: dict | None = None,
) -> dict:
    """
    Merges gross_stats with net cost-adjusted versions.
    Returns a combined dict preserving all original fields plus net fields.
    """
    if not net_equity:
        net_equity = [initial]

    net_return = round((net_equity[-1] / initial - 1) * 100, 2) if initial > 0 else 0.0

    result = dict(gross_stats)
    result.update({
        "gross_return":       gross_stats.get("total_return", 0.0),
        "net_return":         net_return,
        "gross_sharpe":       gross_stats.get("sharpe", 0.0),
        "net_sharpe":         _net_sharpe(net_equity),
        "gross_max_drawdown": gross_stats.get("max_drawdown", 0.0),
        "net_max_drawdown":   _net_mdd(net_equity),
        "return_before_cost": gross_stats.get("total_return", 0.0),
        "return_after_cost":  net_return,
        "cost_drag_pct":      cost_summary.get("cost_drag_pct", 0.0),
        "slippage_drag_pct":  cost_summary.get("slippage_drag_pct", 0.0),
    })
    result.update(cost_summary)
    if params is not None:
        result["cost_params_used"] = params

    return result
