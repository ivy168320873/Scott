"""Scott Evolution v2 domain package.

The package keeps the new decision workflow out of the legacy Flask module:
confidence scorecards, personal risk gates, durable notifications, evidence
guards, and the persistent paper-trading journal live behind small APIs here.
"""

from .risk_brain import assess_trade, get_profile, save_profile
from .scorecard import build_scorecard

__all__ = ["assess_trade", "build_scorecard", "get_profile", "save_profile"]
