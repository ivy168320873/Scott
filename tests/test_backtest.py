"""backtest 引擎與技術指標的單元測試（純計算，不需網路）。"""

import backtest as bt
from conftest import make_ohlcv


# ── 技術指標 ──────────────────────────────────────────────────────────────

def test_sma_length_and_warmup():
    closes = [float(i) for i in range(1, 11)]  # 1..10
    sma = bt._sma(closes, 3)
    assert len(sma) == len(closes)
    assert sma[:2] == [None, None]          # 前 period-1 個為 None
    assert sma[2] == (1 + 2 + 3) / 3        # 第一個有效值


def test_rsi_bounded_0_100():
    closes = [100 + (i % 5) - 2 for i in range(60)]
    rsi = bt._rsi(closes)
    vals = [v for v in rsi if v is not None]
    assert vals, "RSI 應有有效值"
    assert all(0 <= v <= 100 for v in vals)


def test_macd_returns_three_aligned_series():
    closes = [100 + i * 0.3 for i in range(80)]
    ml, sl, hist = bt._macd(closes)
    assert len(ml) == len(sl) == len(hist) == len(closes)


# ── 回測主流程 ────────────────────────────────────────────────────────────

_EXPECTED_KEYS = {
    "total_return", "annual_return", "win_rate",
    "num_trades", "profit_factor", "max_drawdown",
}


def test_run_rsi_returns_expected_keys():
    ohlcv = make_ohlcv(150)
    r = bt.run(ohlcv, "rsi", {})
    assert isinstance(r, dict)
    assert _EXPECTED_KEYS.issubset(r.keys())


def test_run_ma_cross_is_numeric():
    ohlcv = make_ohlcv(200)
    r = bt.run(ohlcv, "ma_cross", {})
    assert isinstance(r["total_return"], (int, float))
    assert isinstance(r["num_trades"], int)
    assert r["num_trades"] >= 0


def test_run_unknown_strategy_falls_back_gracefully():
    # backtest.run 對未知策略會退回預設策略，不應拋例外。
    ohlcv = make_ohlcv(120)
    r = bt.run(ohlcv, "does_not_exist", {})
    assert isinstance(r, dict)
    assert "total_return" in r
