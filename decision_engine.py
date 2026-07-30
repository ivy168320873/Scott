"""
Decision Engine — Integration layer.
Normalises raw OHLCV data into a common format and delegates to the
individual scoring engines.  Import this module (not the individual
engines) from app.py.
"""
from __future__ import annotations

from ohlcv_utils      import sanitize_ohlcv, to_finite_float
from risk_engine      import calc_chase_risk
from sell_engine      import calc_sell_decision
from portfolio_engine import calc_capital_efficiency
from sector_engine    import calc_sector_leadership

__all__ = [
    "normalize_yahoo",
    "normalize_list",
    "sanitize_ohlcv",
    "run_chase_risk",
    "run_sell_decision",
    "run_capital_efficiency",
    "run_sector_leadership",
]


# ── OHLCV normalization helpers ───────────────────────────────────────────────

# 數值轉換與 OHLCV 清洗的單一實作在 ohlcv_utils，各引擎共用同一份語意。
_num = to_finite_float


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

        timestamps: list = []
        closes:  list[float] = []
        opens:   list[float] = []
        highs:   list[float] = []
        lows:    list[float] = []
        volumes: list[float] = []

        # 逐根轉換：單一髒值只丟掉那一根 K 棒，不讓整支股票變成「無資料」。
        for t, o, h, low, c, v in raw:
            close = _num(c)
            if close is None or close <= 0:
                continue
            open_ = _num(o)
            high  = _num(h)
            low_  = _num(low)
            volume = _num(v)
            timestamps.append(t)
            closes.append(close)
            opens.append(open_ if open_ is not None else close)
            highs.append(high if high is not None else close)
            lows.append(low_ if low_ is not None else close)
            volumes.append(volume if volume is not None else 0.0)

        if not closes:
            return None
        return {
            "closes":     closes,
            "opens":      opens,
            "highs":      highs,
            "lows":       lows,
            "volumes":    volumes,
            "timestamps": timestamps,
            # 缺量被補成 0，需另外標示，否則下游無法區分「沒有量」與「量為 0」
            "has_volume": any(v > 0 for v in volumes),
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

    closes:  list[float] = []
    opens:   list[float] = []
    highs:   list[float] = []
    lows:    list[float] = []
    volumes: list[float] = []

    for row in rows:
        if not isinstance(row, dict):
            continue                      # 跳過格式錯誤的列，不讓整批資料失敗
        close = _num(row.get("close"))
        if close is None or close <= 0:
            continue                      # 缺值／非數值／非正數一律略過

        def _or_close(key: str, _row=row, _close=close) -> float:
            v = _num(_row.get(key))
            return v if v is not None else _close

        closes.append(close)
        opens.append(_or_close("open"))
        highs.append(_or_close("high"))
        lows.append(_or_close("low"))
        volume = _num(row.get("volume"))
        volumes.append(volume if volume is not None else 0.0)

    if not closes:
        return None
    return {
        "closes":     closes,
        "opens":      opens,
        "highs":      highs,
        "lows":       lows,
        "volumes":    volumes,
        "timestamps": [],
        # 缺量被補成 0，需另外標示，否則下游無法區分「沒有量」與「量為 0」
        "has_volume": any(v > 0 for v in volumes),
    }


# ── Public wrappers (called from app.py routes) ───────────────────────────────
#
# 各引擎入口已自行呼叫 sanitize_ohlcv（見 ohlcv_utils），因此這些包裝層
# 不再重複清洗；保留 sanitize_ohlcv 的 re-export 供既有呼叫端使用。

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
    if not isinstance(stocks_ohlcv, dict):
        stocks_ohlcv = {}
    return calc_sector_leadership(sector_name, stocks_ohlcv)
