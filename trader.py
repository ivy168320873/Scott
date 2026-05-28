"""
Alpaca Trading Engine
Handles paper/live order execution via the Alpaca REST API.
Requires environment variables:
    ALPACA_API_KEY    — Alpaca key ID
    ALPACA_SECRET_KEY — Alpaca secret key
    ALPACA_PAPER      — "true" (default) or "false" for live trading

When API keys are absent the engine runs in SIMULATION mode:
all orders are accepted but nothing is sent to Alpaca.
"""
from __future__ import annotations
import os
import time
import logging
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# ── Try to import alpaca-py ───────────────────────────────────────────────────
try:
    from alpaca.trading.client import TradingClient
    from alpaca.trading.requests import (
        MarketOrderRequest, LimitOrderRequest,
        TakeProfitRequest, StopLossRequest,
        GetOrdersRequest,
    )
    from alpaca.trading.enums import (
        OrderSide, TimeInForce, OrderClass,
        QueryOrderStatus,
    )
    _ALPACA_AVAILABLE = True
except ImportError:
    _ALPACA_AVAILABLE = False
    logger.warning("alpaca-py not installed — running in SIMULATION mode. pip install alpaca-py")


# ── Simulated order store (used when Alpaca is unavailable) ──────────────────
_sim_orders: list[dict] = []
_sim_positions: dict[str, dict] = {}
_sim_equity = 100_000.0


class TradeEngine:
    """
    Unified interface for Alpaca paper / live / simulation trading.
    """

    def __init__(self):
        self.api_key    = os.environ.get("ALPACA_API_KEY", "").strip()
        self.api_secret = os.environ.get("ALPACA_SECRET_KEY", "").strip()
        self.is_paper   = os.environ.get("ALPACA_PAPER", "true").lower() != "false"
        self.client: Optional[object] = None
        self.simulation = True

        if _ALPACA_AVAILABLE and self.api_key and self.api_secret:
            try:
                self.client = TradingClient(
                    self.api_key, self.api_secret,
                    paper=self.is_paper,
                )
                acct = self.client.get_account()
                self.simulation = False
                mode = "📄 模擬帳戶" if self.is_paper else "💰 真實帳戶"
                logger.info("Alpaca connected — %s  equity=$%s", mode, acct.equity)
            except Exception as e:
                logger.warning("Alpaca connection failed: %s — using SIMULATION mode", e)
        else:
            logger.info("Alpaca keys not set — SIMULATION mode")

    # ── Account info ──────────────────────────────────────────────────────────

    def get_account(self) -> dict:
        if not self.simulation and self.client:
            try:
                a = self.client.get_account()
                return {
                    "equity":            float(a.equity),
                    "cash":              float(a.cash),
                    "buying_power":      float(a.buying_power),
                    "daytrade_count":    int(a.daytrade_count),
                    "portfolio_value":   float(a.portfolio_value),
                    "status":            a.status,
                    "is_paper":          self.is_paper,
                    "simulation":        False,
                }
            except Exception as e:
                logger.error("get_account error: %s", e)

        # Simulation
        return {
            "equity":         _sim_equity,
            "cash":           _sim_equity - sum(
                                  p["qty"] * p["avg_entry"] for p in _sim_positions.values()),
            "buying_power":   _sim_equity * 2,
            "daytrade_count": 0,
            "portfolio_value": _sim_equity,
            "status":         "ACTIVE",
            "is_paper":       True,
            "simulation":     True,
        }

    # ── Positions ─────────────────────────────────────────────────────────────

    def get_positions(self) -> list[dict]:
        if not self.simulation and self.client:
            try:
                positions = self.client.get_all_positions()
                return [
                    {
                        "symbol":     p.symbol,
                        "qty":        float(p.qty),
                        "avg_entry":  float(p.avg_entry_price),
                        "current_price": float(p.current_price or 0),
                        "market_value":  float(p.market_value or 0),
                        "unrealized_pnl": float(p.unrealized_pl or 0),
                        "unrealized_pnl_pct": float(p.unrealized_plpc or 0) * 100,
                        "side":       p.side,
                    }
                    for p in positions
                ]
            except Exception as e:
                logger.error("get_positions error: %s", e)

        # Simulation
        return [
            {
                "symbol":     sym,
                "qty":        p["qty"],
                "avg_entry":  p["avg_entry"],
                "current_price": p.get("current_price", p["avg_entry"]),
                "market_value":  p["qty"] * p.get("current_price", p["avg_entry"]),
                "unrealized_pnl": p["qty"] * (p.get("current_price", p["avg_entry"]) - p["avg_entry"]),
                "unrealized_pnl_pct": (p.get("current_price", p["avg_entry"]) / p["avg_entry"] - 1) * 100,
                "side": "long",
            }
            for sym, p in _sim_positions.items()
        ]

    # ── Orders ────────────────────────────────────────────────────────────────

    def get_recent_orders(self, limit: int = 20) -> list[dict]:
        if not self.simulation and self.client:
            try:
                req = GetOrdersRequest(
                    status=QueryOrderStatus.ALL,
                    limit=limit,
                )
                orders = self.client.get_orders(filter=req)
                return [
                    {
                        "id":          str(o.id),
                        "symbol":      o.symbol,
                        "side":        str(o.side),
                        "qty":         float(o.qty or 0),
                        "filled_qty":  float(o.filled_qty or 0),
                        "order_type":  str(o.order_type),
                        "status":      str(o.status),
                        "filled_avg_price": float(o.filled_avg_price or 0),
                        "submitted_at": str(o.submitted_at or ""),
                        "filled_at":    str(o.filled_at or ""),
                    }
                    for o in orders
                ]
            except Exception as e:
                logger.error("get_orders error: %s", e)

        return list(reversed(_sim_orders[-limit:]))

    # ── Submit bracket order (entry + stop + target) ──────────────────────────

    def submit_order(
        self,
        symbol:      str,
        shares:      int,
        entry:       float,
        stop_price:  float,
        take_profit: float,
        note:        str = "",
    ) -> dict:
        """
        Submit a bracket order:
          - Limit buy at `entry`
          - Stop-loss at `stop_price`
          - Take-profit at `take_profit`
        Returns order dict with status.
        """
        symbol = symbol.upper().replace(".TW", "")  # Alpaca uses plain symbols

        if self.simulation:
            return self._sim_submit(symbol, shares, entry, stop_price, take_profit, note)

        try:
            req = LimitOrderRequest(
                symbol         = symbol,
                qty            = shares,
                side           = OrderSide.BUY,
                time_in_force  = TimeInForce.DAY,
                limit_price    = round(entry, 2),
                order_class    = OrderClass.BRACKET,
                stop_loss      = StopLossRequest(stop_price=round(stop_price, 2)),
                take_profit    = TakeProfitRequest(limit_price=round(take_profit, 2)),
            )
            order = self.client.submit_order(req)
            logger.info("Order submitted: %s %s x%d @ %s", symbol, "BUY", shares, entry)
            return {
                "ok":       True,
                "order_id": str(order.id),
                "symbol":   symbol,
                "shares":   shares,
                "entry":    entry,
                "stop":     stop_price,
                "target":   take_profit,
                "status":   str(order.status),
                "note":     note,
                "ts":       datetime.now(timezone.utc).isoformat(),
                "simulation": False,
            }
        except Exception as e:
            logger.error("submit_order error: %s", e)
            return {"ok": False, "error": str(e), "symbol": symbol}

    def _sim_submit(self, symbol, shares, entry, stop, target, note):
        global _sim_positions
        oid = f"SIM-{int(time.time()*1000)}"
        order = {
            "ok":       True,
            "order_id": oid,
            "symbol":   symbol,
            "shares":   shares,
            "entry":    entry,
            "stop":     stop,
            "target":   target,
            "status":   "filled",
            "note":     note,
            "ts":       datetime.now(timezone.utc).isoformat(),
            "simulation": True,
        }
        _sim_orders.append(order)
        _sim_positions[symbol] = {
            "qty":           shares,
            "avg_entry":     entry,
            "stop":          stop,
            "target":        target,
            "current_price": entry,
        }
        logger.info("[SIM] Order: BUY %s x%d @ %.2f  stop=%.2f  tp=%.2f", symbol, shares, entry, stop, target)
        return order

    # ── Close position ────────────────────────────────────────────────────────

    def close_position(self, symbol: str) -> dict:
        symbol = symbol.upper().replace(".TW", "")
        if self.simulation:
            if symbol in _sim_positions:
                del _sim_positions[symbol]
                return {"ok": True, "symbol": symbol, "simulation": True}
            return {"ok": False, "error": "Position not found"}

        try:
            self.client.close_position(symbol)
            return {"ok": True, "symbol": symbol}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    # ── Status summary ────────────────────────────────────────────────────────

    def status(self) -> dict:
        acct = self.get_account()
        return {
            "connected":  not self.simulation,
            "simulation": self.simulation,
            "is_paper":   self.is_paper,
            "mode":       "模擬" if self.simulation else ("📄 Alpaca 模擬帳戶" if self.is_paper else "💰 Alpaca 真實帳戶"),
            "equity":     acct.get("equity", 0),
            "cash":       acct.get("cash", 0),
        }


# ── Singleton ─────────────────────────────────────────────────────────────────
_engine: Optional[TradeEngine] = None

def get_engine() -> TradeEngine:
    global _engine
    if _engine is None:
        _engine = TradeEngine()
    return _engine
