"""
Rotation Decision Engine — Phase 7 (public-facing entry point)

Delegates all computation to rotation_engine.py.
This module owns the decision *contract*:
  KEEP | WATCH | TRIM | ROTATE_PARTIAL | ROTATE_FULL | STOP_LOSS

Public API
----------
make_rotation_decision(symbol, ce_score, ce_level, drag_score, drag_level,
                       alternatives, momentum_score, chase_risk_score,
                       sell_signal, stop_loss_breached) -> dict
calc_drag_score(...)        -> dict
find_alternative_candidates(...) -> list[dict]
analyze_portfolio(...)      -> dict   ← main entry point for app.py

All outputs carry: disclaimer = "此為決策輔助，不代表自動下單。"
"""
from rotation_engine import (          # re-export everything
    ROTATION_LABEL,
    ROTATION_COLOR,
    DRAG_LEVEL,
    calc_drag_score,
    find_alternative_candidates,
    make_rotation_decision,
    analyze_portfolio,
)

__all__ = [
    "ROTATION_LABEL",
    "ROTATION_COLOR",
    "DRAG_LEVEL",
    "calc_drag_score",
    "find_alternative_candidates",
    "make_rotation_decision",
    "analyze_portfolio",
]
