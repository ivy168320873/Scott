"""Detailed cost model regression tests."""

import trade_cost as tc


def _bars():
    return [
        {"date": "d0", "open": 100.0, "high": 106.0, "low": 99.0, "close": 105.0, "volume": 2_000_000},
        {"date": "d1", "open": 110.0, "high": 116.0, "low": 109.0, "close": 115.0, "volume": 2_000_000},
        {"date": "d2", "open": 120.0, "high": 126.0, "low": 119.0, "close": 125.0, "volume": 2_000_000},
    ]


def test_next_open_mode_uses_signal_index_once():
    trade = {
        "entry_date": "d1",
        "exit_date": "d2",
        "entry_price": 110.0,
        "exit_price": 120.0,
        "pnl_pct": 9.09,
        "entry_idx": 1,
        "exit_idx": 2,
        "entry_signal_idx": 0,
        "exit_signal_idx": 1,
    }
    params = {
        **tc.default_params("AAPL"),
        "slippage_pct": 0.0,
        "use_liquidity_filter": False,
    }
    result = tc.apply_costs([trade], _bars(), params)
    enhanced = result["trades"][0]
    assert enhanced["entry_price_actual"] == 110.0
    assert enhanced["exit_price_actual"] == 120.0


def test_slippage_summary_is_in_dollars_not_per_share():
    trade = {
        "entry_price": 110.0,
        "exit_price": 120.0,
        "pnl_pct": 9.09,
        "entry_idx": 1,
        "exit_idx": 2,
        "price_locked": True,
    }
    params = {
        **tc.default_params("AAPL"),
        "slippage_pct": 0.001,
        "use_liquidity_filter": False,
    }
    result = tc.apply_costs([trade], _bars(), params)
    assert result["cost_summary"]["total_slippage"] > 100
    assert result["trades"][0]["slippage_buy_per_share"] == 0.11
