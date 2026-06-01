"""
Decision Engine — Integration layer.
Normalises raw OHLCV data into a common format and delegates to the
individual scoring engines.  Import this module (not the individual
engines) from app.py.
"""
from __future__ import annotations

from risk_engine      import calc_chase_risk
from sell_engine      import calc_sell_decision
from portfolio_engine import calc_capital_efficiency
from sector_engine    import calc_sector_leadership

__all__ = [
    "normalize_yahoo",
    "normalize_list",
    "run_chase_risk",
    "run_sell_decision",
    "run_capital_efficiency",
    "run_sector_leadership",
]


# ── OHLCV normalization helpers ───────────────────────────────────────────────

def normalize_yahoo(yahoo_data: dict) -> dict | None:
    """
    Convert Yahoo-format dict (from _fetch_ohlcv_twse / _fetch_ohlcv_finnhub /
    _fetch_ohlcv_alpha_vantage) to a normalised OHLCV dict.
    Returns None on failure.
    """
    try:
        res = yahoo_data["chart"]["result"][0]
        q   = res["indicators"]["quote"][0]
        ts  = res.get("timestamp", [])

        raw = zip(
            ts or [None] * len(q.get("close", [])),
            q.get("open",   []),
            q.get("high",   []),
            q.get("low",    []),
            q.get("close",  []),
            q.get("volume", []),
        )
        rows = [(t, o, h, l, c, v) for t, o, h, l, c, v in raw
                if c and c > 0 and o and h and l and v is not None]
        if not rows:
            return None
        ts_, o_, h_, l_, c_, v_ = zip(*rows)
        return {
            "closes":     list(c_),
            "opens":      list(o_),
            "highs":      list(h_),
            "lows":       list(l_),
            "volumes":    list(v_),
            "timestamps": list(ts_),
        }
    except (KeyError, IndexError, TypeError):
        return None


def normalize_list(rows: list) -> dict | None:
    """
    Convert list-of-dicts format [{date, open, high, low, close, volume}, ...]
    to normalised dict.  Rows with close ≤ 0 are skipped.
    """
    if not rows:
        return None
    valid = [r for r in rows if r.get("close", 0) > 0]
    if not valid:
        return None
    return {
        "closes":     [r["close"]  for r in valid],
        "opens":      [r.get("open",  r["close"]) for r in valid],
        "highs":      [r.get("high",  r["close"]) for r in valid],
        "lows":       [r.get("low",   r["close"]) for r in valid],
        "volumes":    [r.get("volume", 0)          for r in valid],
        "timestamps": [],
    }


# ── Public wrappers (called from app.py routes) ───────────────────────────────

def run_chase_risk(ohlcv_norm: dict) -> dict:
    return calc_chase_risk(ohlcv_norm)


def run_sell_decision(ohlcv_norm: dict, cost: float, holding_days: int = 0,
                      stop_pct: float = 8.0, trail_pct: float = 15.0,
                      profit_target_pct: float = 20.0) -> dict:
    return calc_sell_decision(
        ohlcv_norm, cost=cost, holding_days=holding_days,
        stop_pct=stop_pct, trail_pct=trail_pct,
        profit_target_pct=profit_target_pct,
    )


def run_capital_efficiency(holding: dict, ohlcv_norm: dict,
                           benchmark_return: float | None = None) -> dict:
    return calc_capital_efficiency(holding, ohlcv_norm,
                                   benchmark_return=benchmark_return)


def run_sector_leadership(sector_name: str,
                          stocks_ohlcv: dict) -> dict:
    return calc_sector_leadership(sector_name, stocks_ohlcv)
