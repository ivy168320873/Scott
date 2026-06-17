"""Risk manager.

Gatekeeper between a generated signal and a paper trade. Enforces:
  - max risk per single trade (USD)
  - max open exposure per market (USD)
  - max daily realized loss (USD)  -> trips a daily kill-switch
  - max holding time (seconds)     -> enforced by the paper trader on exits

The LLM has no influence over sizing here: order size is a fixed configured
amount, capped by these limits. Returns (allowed, size_usd, reason).
"""
from __future__ import annotations

import logging
from typing import Tuple

from .. import repository as repo
from ..config import get_settings

log = logging.getLogger(__name__)


class RiskManager:
    def __init__(self) -> None:
        s = get_settings()
        self.max_risk_per_trade = s.max_risk_per_trade_usd
        self.max_market_exposure = s.max_market_exposure_usd
        self.max_daily_loss = s.max_daily_loss_usd
        self.order_size = s.paper_order_size_usd
        self.max_holding_seconds = s.max_holding_seconds

    async def check(self, condition_id: str) -> Tuple[bool, float, str]:
        # 1) Daily loss kill-switch
        daily = await repo.daily_realized_pnl()
        if daily <= -abs(self.max_daily_loss):
            return False, 0.0, f"daily loss limit hit ({daily:.2f} <= -{self.max_daily_loss})"

        # 2) Per-trade size, capped by per-trade risk limit
        size = min(self.order_size, self.max_risk_per_trade)
        if size <= 0:
            return False, 0.0, "configured order size is zero"

        # 3) Per-market exposure
        exposure = await repo.market_exposure(condition_id)
        if exposure + size > self.max_market_exposure:
            remaining = self.max_market_exposure - exposure
            if remaining <= 0:
                return False, 0.0, f"market exposure cap reached ({exposure:.0f}/{self.max_market_exposure:.0f})"
            size = min(size, remaining)  # partial fill down to the cap

        return True, size, "ok"
