"""Trading safety-gate tests; no brokerage connection is made."""

import trader


def test_alpaca_live_flag_alone_cannot_enable_live(monkeypatch):
    monkeypatch.setattr(trader, "_ALPACA_AVAILABLE", False)
    monkeypatch.setenv("ALPACA_PAPER", "false")
    monkeypatch.delenv("ENABLE_LIVE_TRADING", raising=False)
    engine = trader.TradeEngine()
    assert engine.is_paper is True
    assert engine.live_trading_enabled is False
    assert engine.simulation is True


def test_order_shape_is_validated_before_simulation(monkeypatch):
    monkeypatch.setattr(trader, "_ALPACA_AVAILABLE", False)
    engine = trader.TradeEngine()
    result = engine.submit_order("AAPL", 1, 100.0, 105.0, 110.0)
    assert result["ok"] is False
    assert result["blocked"] is True
