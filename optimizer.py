"""
Advanced Backtesting Optimizer

Three analysis modules:
  1. Rolling Walk-Forward — find robust parameters across multiple time windows
  2. Monte Carlo Simulation — bootstrap resample for future outcome distributions
  3. Market Regime Analysis — condition strategy performance on bull/bear/sideways

All functions are pure Python, no external dependencies beyond backtest.py.
"""
from __future__ import annotations
import math
import random
from typing import Optional
import backtest as _bt

# ── Parameter Grid ────────────────────────────────────────────────────────────
# 3×2×2×2×2×2 = 96 combinations — fast enough for full rolling WF search

_GRID_V3 = [
    {
        "sm_threshold":  sm,
        "bp_threshold":  bp,
        "cr_threshold":  cr,
        "rs_threshold":  rs,
        "rsi_lo":        rl,
        "max_5d_gain":   mg,
    }
    for sm in (60, 65, 70)
    for bp in (25, 30)
    for cr in (40, 50)
    for rs in (60, 80)
    for rl in (40, 45)
    for mg in (8.0, 12.0)
]   # 96 combos

_GRID_V2 = [
    {"sm_threshold": sm, "bp_threshold": bp, "cr_threshold": cr,
     "rs_threshold": rs, "rsi_lo": rl}
    for sm in (60, 65, 70)
    for bp in (25, 30)
    for cr in (40, 45, 50)
    for rs in (60, 80)
    for rl in (40, 45)
]   # 72 combos


def _grid(strategy: str) -> list[dict]:
    return _GRID_V3 if "v3" in strategy else _GRID_V2


def _fn(strategy: str):
    return _bt.STRATEGIES.get(strategy, _bt.strategy_decision_core_v3)


def _score(r: dict) -> float:
    """Composite in-sample score: balances win rate, Sortino, profit factor."""
    if r["num_trades"] < 2:
        return -999.0
    wr  = r.get("win_rate", 0)
    sr  = min(r.get("sortino", 0), 5)
    pf  = min(r.get("profit_factor", 1), 6)
    exp = r.get("expectancy", 0)
    return wr * 0.40 + sr * 15 + pf * 8 + exp * 4


# ── 1. Rolling Walk-Forward Optimization ─────────────────────────────────────

def rolling_walk_forward(
    ohlcv: list,
    strategy: str = "decision_core_v3",
    train_bars: int = 252,   # ≈1 year
    test_bars:  int = 63,    # ≈1 quarter
    step_bars:  int = 42,    # ≈2 months per step
) -> dict:
    """
    Slides a training window across history:
      - Grid-searches for best params on training bars
      - Evaluates those params on the next unseen test_bars (true OOS)
      - Steps forward by step_bars and repeats

    Returns per-window results, recommended stable params, robustness verdict.
    """
    n = len(ohlcv)
    if n < train_bars + test_bars:
        return {"error": f"數據不足（需至少 {train_bars + test_bars} 個交易日）"}

    fn   = _fn(strategy)
    grid = _grid(strategy)
    windows: list[dict] = []
    start = 0

    while start + train_bars + test_bars <= n:
        train = ohlcv[start : start + train_bars]
        test  = ohlcv[start + train_bars : start + train_bars + test_bars]

        # ── Find best params on training period ───────────────────────────
        best_score   = -999.0
        best_params  = {}
        best_is_stats = {}

        for params in grid:
            try:
                r = fn(train, **params)
                s = _score(r)
                if s > best_score:
                    best_score    = s
                    best_params   = params
                    best_is_stats = r
            except Exception:
                continue

        if not best_params:
            start += step_bars
            continue

        # ── Apply best params on unseen test period ───────────────────────
        try:
            oos = fn(test, **best_params)
        except Exception:
            start += step_bars
            continue

        windows.append({
            "train_period":  f"{train[0]['date']} → {train[-1]['date']}",
            "test_period":   f"{test[0]['date']} → {test[-1]['date']}",
            "is_win_rate":   round(best_is_stats.get("win_rate", 0), 1),
            "is_trades":     best_is_stats.get("num_trades", 0),
            "oos_win_rate":  round(oos.get("win_rate", 0), 1),
            "oos_return":    round(oos.get("total_return", 0), 2),
            "oos_trades":    oos.get("num_trades", 0),
            "oos_sortino":   oos.get("sortino", 0),
            "oos_pf":        oos.get("profit_factor", 0),
            "params":        best_params,
        })
        start += step_bars

    if not windows:
        return {"error": "無法生成走向前視窗（數據可能太短或策略無訊號）"}

    valid = [w for w in windows if w["oos_trades"] > 0]

    # ── Parameter frequency analysis ──────────────────────────────────────
    combo_freq: dict[str, int] = {}
    for w in valid:
        p = w["params"]
        key = (f"主力>{p.get('sm_threshold',65)} "
               f"空壓<{p.get('bp_threshold',25)} "
               f"追高<{p.get('cr_threshold',45)} "
               f"共振≥{p.get('rs_threshold',80)}")
        combo_freq[key] = combo_freq.get(key, 0) + 1

    best_key = max(combo_freq, key=combo_freq.get) if combo_freq else None
    stability = round(combo_freq[best_key] / len(valid) * 100, 0) if best_key and valid else 0

    # ── Recommended params (most frequently optimal) ──────────────────────
    rec_params = {}
    if best_key:
        # Find the full param dict that generated best_key
        for w in valid:
            p = w["params"]
            key = (f"主力>{p.get('sm_threshold',65)} "
                   f"空壓<{p.get('bp_threshold',25)} "
                   f"追高<{p.get('cr_threshold',45)} "
                   f"共振≥{p.get('rs_threshold',80)}")
            if key == best_key:
                rec_params = p
                break

    avg_is_wr  = round(sum(w["is_win_rate"]  for w in valid) / len(valid), 1) if valid else 0
    avg_oos_wr = round(sum(w["oos_win_rate"] for w in valid) / len(valid), 1) if valid else 0
    avg_oos_ret= round(sum(w["oos_return"]   for w in valid) / len(valid), 2) if valid else 0
    avg_oos_pf = round(sum(w["oos_pf"]       for w in valid) / len(valid), 2) if valid else 0

    overfit_gap = avg_is_wr - avg_oos_wr

    if avg_oos_wr >= 65 and stability >= 40:
        verdict = "✅ 策略穩健——可上線交易"
    elif avg_oos_wr >= 50 and overfit_gap <= 20:
        verdict = "⚠️  策略可接受——持續監控"
    elif overfit_gap > 25:
        verdict = "❌ 過度擬合——樣本內外差距過大"
    else:
        verdict = "❌ 策略一致性不佳——暫勿使用"

    return {
        "windows":          windows,
        "total_windows":    len(windows),
        "valid_windows":    len(valid),
        "avg_is_win_rate":  avg_is_wr,
        "avg_oos_win_rate": avg_oos_wr,
        "avg_oos_return":   avg_oos_ret,
        "avg_oos_pf":       avg_oos_pf,
        "overfit_gap":      round(overfit_gap, 1),
        "stability_pct":    stability,
        "param_frequency":  combo_freq,
        "recommended_params": rec_params,
        "verdict":          verdict,
    }


# ── 2. Monte Carlo Bootstrap Simulation ──────────────────────────────────────

def monte_carlo(
    trades: list,
    n_sims: int = 3000,
    initial: float = 100_000.0,
    n_forward: int = 30,   # simulate N trades forward
) -> dict:
    """
    Bootstraps historical trade P&Ls to simulate N future equity curves.

    Returns:
      - P5 / P25 / P50 / P75 / P95 percentiles for return, drawdown, win rate
      - Probability of profit
      - Three representative equity curves (pessimistic / median / optimistic)
    """
    if len(trades) < 5:
        return {"error": "交易次數不足（需至少 5 筆歷史交易）"}

    pnls   = [t["pnl_pct"] / 100 for t in trades]
    is_win = [1 if t["win"] else 0 for t in trades]
    rng    = random.Random(2024)

    sim_rets, sim_dds, sim_wrs = [], [], []

    for _ in range(n_sims):
        sampled = rng.choices(pnls, k=n_forward)
        equity  = initial
        peak    = initial
        max_dd  = 0.0
        wins    = 0
        for r in sampled:
            equity *= (1 + r)
            if equity > peak:
                peak = equity
            dd = (equity - peak) / peak * 100
            if dd < max_dd:
                max_dd = dd
            if r > 0:
                wins += 1
        sim_rets.append((equity / initial - 1) * 100)
        sim_dds.append(max_dd)
        sim_wrs.append(wins / n_forward * 100)

    sim_rets.sort(); sim_dds.sort(); sim_wrs.sort()

    def pc(arr, p):
        idx = max(0, min(int(len(arr) * p / 100), len(arr) - 1))
        return round(arr[idx], 2)

    # Generate 3 representative equity curves (P5 / P50 / P95)
    curves = {}
    for label, seed in [("p5", 501), ("p50", 502), ("p95", 503)]:
        rng2 = random.Random(seed)
        eq, pts = initial, [100.0]
        for _ in range(n_forward):
            r = rng2.choice(pnls)
            eq *= (1 + r)
            pts.append(round((eq / initial - 1) * 100, 2))
        curves[label] = pts

    # Historical trade distribution (win / loss buckets)
    win_pnls  = [t["pnl_pct"] for t in trades if t["win"]]
    loss_pnls = [t["pnl_pct"] for t in trades if not t["win"]]

    return {
        "n_sims":       n_sims,
        "n_forward":    n_forward,
        "input_trades": len(trades),
        "historical": {
            "win_rate":  round(sum(is_win) / len(is_win) * 100, 1),
            "avg_win":   round(sum(win_pnls)  / len(win_pnls)  if win_pnls  else 0, 2),
            "avg_loss":  round(sum(loss_pnls) / len(loss_pnls) if loss_pnls else 0, 2),
        },
        "return": {
            "p5":  pc(sim_rets, 5),
            "p25": pc(sim_rets, 25),
            "p50": pc(sim_rets, 50),
            "p75": pc(sim_rets, 75),
            "p95": pc(sim_rets, 95),
        },
        "drawdown": {
            "p5":  pc(sim_dds, 5),
            "p50": pc(sim_dds, 50),
            "p95": pc(sim_dds, 95),
        },
        "win_rate_sim": {
            "p5":  pc(sim_wrs, 5),
            "p50": pc(sim_wrs, 50),
            "p95": pc(sim_wrs, 95),
        },
        "prob_profit":  round(sum(1 for r in sim_rets if r > 0) / n_sims * 100, 1),
        "curves":       curves,
    }


# ── 3. Market Regime Conditional Analysis ────────────────────────────────────

def regime_analysis(
    ohlcv: list,
    strategy: str = "decision_core_v3",
) -> dict:
    """
    1. Runs strategy on the full dataset to collect all historical trades.
    2. For each trade, determines the market regime at entry (bull / bear / sideways)
       using the stock's own EMA50/EMA200 relationship.
    3. Computes conditional win rate, avg win/loss, expectancy per regime.
    4. Recommends regime-specific parameter adjustments.
    """
    n = len(ohlcv)
    if n < 80:
        return {"error": "數據不足"}

    # ── Run strategy on full dataset ──────────────────────────────────────
    fn     = _fn(strategy)
    result = fn(ohlcv)
    trades = result.get("trades", [])
    if not trades:
        return {
            "error": "此股票在目前參數下無歷史交易訊號",
            "regime_results": {},
        }

    # ── Date → regime mapping ─────────────────────────────────────────────
    closes = [d["close"] for d in ohlcv]
    ema50  = _bt._ema(closes, min(50, n))
    ema200 = _bt._ema(closes, min(200, n))
    date_idx = {d["date"]: i for i, d in enumerate(ohlcv)}

    def classify(date_str: str) -> str:
        idx = date_idx.get(date_str)
        if idx is None:
            return "side"
        c, e50, e200 = closes[idx], ema50[idx], ema200[idx]
        if c > e50 and e50 > e200:
            return "bull"
        if c < e50 and e50 < e200:
            return "bear"
        return "side"

    # ── Segregate trades by regime ────────────────────────────────────────
    regime_trades: dict[str, list] = {"bull": [], "bear": [], "side": []}
    for t in trades:
        r = classify(t.get("entry_date", ""))
        if r in regime_trades:
            regime_trades[r].append(t)

    # ── Compute stats per regime ──────────────────────────────────────────
    labels = {"bull": "多頭市場 🟢", "bear": "空頭市場 🔴", "side": "盤整震盪 🟡"}
    results: dict = {}
    advice: list[str] = []

    for regime, rts in regime_trades.items():
        if not rts:
            results[regime] = {"num_trades": 0}
            continue

        wins   = [t for t in rts if t["win"]]
        losses = [t for t in rts if not t["win"]]
        wr     = len(wins) / len(rts) * 100
        avg_w  = sum(t["pnl_pct"] for t in wins)  / len(wins)  if wins  else 0
        avg_l  = sum(t["pnl_pct"] for t in losses) / len(losses) if losses else 0
        exp    = wr / 100 * avg_w + (1 - wr / 100) * avg_l
        avg_h  = sum(t["holding_days"] for t in rts) / len(rts)
        pf     = abs(avg_w * len(wins)) / abs(avg_l * len(losses)) if losses and avg_l != 0 else 999

        results[regime] = {
            "label":       labels[regime],
            "num_trades":  len(rts),
            "win_rate":    round(wr, 1),
            "avg_win":     round(avg_w, 2),
            "avg_loss":    round(avg_l, 2),
            "expectancy":  round(exp, 2),
            "profit_factor": round(pf, 2) if pf < 900 else 999,
            "avg_holding": round(avg_h, 1),
        }

        # Advice
        if regime == "bull" and wr >= 70:
            advice.append(f"✅ 多頭市場勝率 {wr:.0f}%——維持現有參數")
        elif regime == "bull" and wr < 55:
            advice.append(f"⚠️  多頭市場勝率偏低（{wr:.0f}%）——建議縮小 RSI 甜區至 50-65")
        if regime == "side" and len(rts) > 3:
            advice.append(f"⚠️  盤整期有 {len(rts)} 筆交易——建議開啟 BB 緊縮持續≥3天過濾")
        if regime == "bear" and len(rts) > 0:
            advice.append(f"🔴 空頭市場出現 {len(rts)} 筆交易——EMA200 過濾可能失效，建議加強共振門檻至 ≥100")

    # ── Optimal regime for this strategy ─────────────────────────────────
    best_regime = max(
        (r for r in results if results[r].get("num_trades", 0) > 0),
        key=lambda r: results[r].get("win_rate", 0),
        default="bull"
    )

    return {
        "regime_results": results,
        "total_trades":   len(trades),
        "best_regime":    best_regime,
        "best_label":     labels.get(best_regime, ""),
        "advice":         advice,
    }


# ── 4. Parameter Stability Analysis ──────────────────────────────────────────

def parameter_stability(
    ohlcv: list,
    strategy: str = "decision_core_v3",
    n_splits: int = 5,
) -> dict:
    """
    Splits data into n_splits non-overlapping blocks.
    Finds optimal parameters for each block independently.
    Reports which parameters are consistently optimal (stability score).
    High stability → low overfitting risk → safe to use live.
    """
    n = len(ohlcv)
    block_size = n // n_splits
    if block_size < 100:
        return {"error": f"數據不足（需至少 {n_splits * 100} 天，建議 2-3 年）"}

    fn   = _fn(strategy)
    grid = _grid(strategy)
    blocks: list[dict] = []

    for k in range(n_splits):
        block = ohlcv[k * block_size : (k + 1) * block_size]
        best_score  = -999.0
        best_params = {}
        best_r      = {}

        for params in grid:
            try:
                r = fn(block, **params)
                s = _score(r)
                if s > best_score:
                    best_score  = s
                    best_params = params
                    best_r      = r
            except Exception:
                continue

        blocks.append({
            "period":    f"{block[0]['date']} → {block[-1]['date']}",
            "params":    best_params,
            "win_rate":  round(best_r.get("win_rate", 0), 1),
            "trades":    best_r.get("num_trades", 0),
            "sortino":   best_r.get("sortino", 0),
            "bars":      len(block),
        })

    # ── Per-parameter stability (% of blocks agreeing on a value) ────────
    param_keys = ["sm_threshold", "bp_threshold", "cr_threshold",
                  "rs_threshold", "rsi_lo", "max_5d_gain"]
    stability: dict[str, dict] = {}

    for pk in param_keys:
        counts: dict = {}
        for b in blocks:
            v = b["params"].get(pk)
            if v is not None:
                counts[v] = counts.get(v, 0) + 1
        if counts:
            best_val = max(counts, key=counts.get)
            stability[pk] = {
                "recommended":  best_val,
                "agreement_pct": round(counts[best_val] / n_splits * 100, 0),
                "all_values":   counts,
            }

    # ── Overall stability score ───────────────────────────────────────────
    agrees = [v["agreement_pct"] for v in stability.values()]
    overall = round(sum(agrees) / len(agrees), 0) if agrees else 0

    # Recommended stable parameter set
    rec = {pk: stability[pk]["recommended"] for pk in param_keys if pk in stability}

    verdict = (
        "✅ 參數高度穩定（過度擬合風險低）" if overall >= 70 else
        "⚠️  參數中等穩定——建議加大數據量" if overall >= 50 else
        "❌ 參數不穩定——過度擬合風險高"
    )

    return {
        "blocks":            blocks,
        "stability":         stability,
        "overall_stability": overall,
        "recommended":       rec,
        "verdict":           verdict,
    }
