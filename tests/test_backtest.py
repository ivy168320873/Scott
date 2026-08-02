"""Backtest engine tests, including execution-timing regression coverage."""

import backtest as bt
from conftest import make_ohlcv


def test_sma_length_and_warmup():
    closes = [float(i) for i in range(1, 11)]
    sma = bt._sma(closes, 3)
    assert len(sma) == len(closes)
    assert sma[:2] == [None, None]
    assert sma[2] == 2.0


def test_rsi_is_bounded():
    closes = [100 + (i % 5) - 2 for i in range(60)]
    values = [value for value in bt._rsi(closes) if value is not None]
    assert values
    assert all(0 <= value <= 100 for value in values)


def test_core_strategies_return_expected_statistics():
    expected = {
        "total_return", "annual_return", "win_rate", "num_trades",
        "profit_factor", "max_drawdown",
    }
    for strategy in bt.STRATEGIES:
        result = bt.run(make_ohlcv(220), strategy, {})
        assert expected.issubset(result)
        assert isinstance(result["num_trades"], int)


def test_standard_signals_fill_at_next_open():
    result = bt._simulate(
        opens=[100.0, 111.0, 121.0],
        closes=[105.0, 115.0, 125.0],
        dates=["d0", "d1", "d2"],
        buy_sig=[True, False, False],
        sell_sig=[False, True, False],
    )
    trade = result["trades"][0]
    assert trade["entry_date"] == "d1"
    assert trade["entry_price"] == 111.0
    assert trade["exit_date"] == "d2"
    assert trade["exit_price"] == 121.0


def test_partial_exit_uses_conservative_intrabar_order():
    result = bt._simulate_partial(
        opens=[100.0, 100.0],
        closes=[100.0, 105.0],
        highs=[101.0, 120.0],
        lows=[99.0, 80.0],
        dates=["signal", "fill"],
        entries=[(0, 100.0, 90.0, 110.0, 120.0)],
    )
    assert result["trades"]
    assert {trade["exit_price"] for trade in result["trades"]} == {90.0}
    assert all(not trade["win"] for trade in result["trades"])


def test_optimizer_never_ranks_by_out_of_sample(monkeypatch):
    def fake_strategy(data, **params):
        is_test = str(data[0]["date"]).startswith("test")
        train_score = float(params["sm_threshold"])
        score = 100.0 - train_score if is_test else train_score
        return {
            "num_trades": 3,
            "win_rate": score,
            "total_return": score,
            "sharpe": score,
            "sortino": score,
            "calmar": score,
            "profit_factor": score,
            "expectancy": score,
        }

    monkeypatch.setitem(bt.STRATEGIES, "selection_test", fake_strategy)
    rows = make_ohlcv(220)
    for i, row in enumerate(rows):
        row["date"] = ("train" if i < 154 else "test") + f"-{i:03d}"

    result = bt.optimize_parameters(rows, "selection_test", "win_rate")
    assert result["selection_basis"] == "in_sample_only"
    assert result["top_params"]
    assert all(item["params"]["sm_threshold"] == 70 for item in result["top_params"])
