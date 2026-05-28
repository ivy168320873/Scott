"""
Risk Management Engine
Enforces position sizing, daily-loss kill-switch, and per-trade risk limits.
All methods are pure (no side effects) — the caller decides whether to execute.
"""
from __future__ import annotations
import math

# ── Default risk parameters (overridable via env / API) ───────────────────────
DEFAULT_RISK_PER_TRADE  = 0.02   # max 2% of portfolio per trade
DEFAULT_MAX_POS_PCT     = 0.20   # max 20% of portfolio in one position
DEFAULT_MAX_DAILY_LOSS  = 0.05   # kill-switch at 5% daily drawdown
DEFAULT_MAX_OPEN_TRADES = 10     # never hold more than 10 positions


class RiskManager:
    def __init__(
        self,
        risk_per_trade: float  = DEFAULT_RISK_PER_TRADE,
        max_pos_pct:    float  = DEFAULT_MAX_POS_PCT,
        max_daily_loss: float  = DEFAULT_MAX_DAILY_LOSS,
        max_open_trades: int   = DEFAULT_MAX_OPEN_TRADES,
    ):
        self.risk_per_trade  = risk_per_trade
        self.max_pos_pct     = max_pos_pct
        self.max_daily_loss  = max_daily_loss
        self.max_open_trades = max_open_trades

    # ── Core position-size calculation ────────────────────────────────────────

    def calc_shares(
        self,
        portfolio_value: float,
        entry: float,
        stop: float,
    ) -> tuple[int, dict]:
        """
        Fixed-fractional position sizing using ATR stop.
        Returns (shares, details_dict).
        """
        if entry <= 0 or stop <= 0 or entry <= stop:
            return 0, {"error": "無效的進場/停損價格"}

        risk_dollar   = portfolio_value * self.risk_per_trade
        risk_per_share = entry - stop
        if risk_per_share <= 0:
            return 0, {"error": "停損必須低於進場價"}

        shares_by_risk  = math.floor(risk_dollar / risk_per_share)
        max_shares      = math.floor(portfolio_value * self.max_pos_pct / entry)
        shares          = min(shares_by_risk, max_shares)

        if shares <= 0:
            return 0, {"error": "資金不足或倉位過小"}

        invest_amount = shares * entry
        actual_risk   = shares * risk_per_share
        portfolio_pct = invest_amount / portfolio_value * 100

        return shares, {
            "shares":        shares,
            "invest_amount": round(invest_amount, 2),
            "risk_dollar":   round(actual_risk, 2),
            "risk_pct":      round(actual_risk / portfolio_value * 100, 2),
            "portfolio_pct": round(portfolio_pct, 2),
            "capped":        shares < shares_by_risk,
        }

    # ── Kill-switch ───────────────────────────────────────────────────────────

    def check_kill_switch(
        self,
        portfolio_value: float,
        start_of_day_value: float,
        open_positions: int = 0,
    ) -> tuple[bool, str]:
        """
        Returns (trading_allowed, reason).
        Blocks if daily loss exceeds threshold or too many open positions.
        """
        if start_of_day_value > 0:
            daily_loss = (start_of_day_value - portfolio_value) / start_of_day_value
            if daily_loss >= self.max_daily_loss:
                pct = daily_loss * 100
                return False, f"⛔ 今日已虧損 {pct:.1f}%，超過 {self.max_daily_loss*100:.0f}% 停機線，停止交易"

        if open_positions >= self.max_open_trades:
            return False, f"⛔ 已持有 {open_positions} 個部位，達到上限 {self.max_open_trades}"

        return True, "✅ 風控通過"

    # ── Order pre-check ───────────────────────────────────────────────────────

    def validate_order(
        self,
        portfolio_value: float,
        start_of_day_value: float,
        open_positions: int,
        entry: float,
        stop: float,
    ) -> tuple[bool, int, dict]:
        """
        Returns (approved, shares, details).
        """
        allowed, reason = self.check_kill_switch(
            portfolio_value, start_of_day_value, open_positions
        )
        if not allowed:
            return False, 0, {"reason": reason}

        shares, details = self.calc_shares(portfolio_value, entry, stop)
        if shares <= 0:
            return False, 0, {**details, "reason": details.get("error", "倉位計算失敗")}

        return True, shares, {**details, "reason": "✅ 風控通過，可以下單"}

    # ── Kelly Criterion (參考用) ───────────────────────────────────────────────

    @staticmethod
    def kelly_fraction(win_rate: float, avg_win_pct: float, avg_loss_pct: float) -> float:
        """
        Full Kelly fraction (cap at 0.25 for safety).
        win_rate: 0–1
        avg_win/loss: percentages as decimals (e.g. 0.08 = 8%)
        """
        if avg_loss_pct <= 0:
            return 0.0
        b = avg_win_pct / avg_loss_pct
        k = (b * win_rate - (1 - win_rate)) / b
        return round(min(max(k, 0), 0.25), 4)


# ── Singleton ─────────────────────────────────────────────────────────────────
_default_rm = RiskManager()

def get_risk_manager() -> RiskManager:
    return _default_rm
