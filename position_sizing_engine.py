"""
Position Sizing Engine — Phase 12B
Computes position size using Fixed-Risk, calibrated fractional Kelly, and ATR.

Public API
----------
run_position_sizing(payload: dict) -> dict

Methods:
  fixed_risk    — Risk a fixed % of account per trade (classic 1-2% rule)
  fractional_kelly — disabled until independent calibration gates pass
  vol_adjusted  — ATR-based: wider volatility → smaller position

All methods are computed and the most conservative result is used.
"""
from __future__ import annotations

import math

# ── Constants ─────────────────────────────────────────────────────────────────

_LEVEL_MAP = [
    (0.10, "AGGRESSIVE"),  # ≥ 10% of portfolio
    (0.05, "NORMAL"),      # 5–10%
    (0.03, "SMALL"),       # 3–5%
    (0.01, "TINY"),        # 1–3%
    (0.0,  "NO_TRADE"),    # < 1%
]

_DECISION_ALLOWED = {
    "STRONG_BUY", "BUY",
}

_REGIME_MULT = {
    "RISK_ON":    1.00,
    "NEUTRAL":    0.60,
    "RISK_OFF":   0.00,
    "CRASH_RISK": 0.00,
}

_DEFAULT_WIN_RATE    = 0.55
_DEFAULT_RR_RATIO    = 2.0
_MAX_POSITION_PCT    = 0.15   # hard cap after calibration
_UNCALIBRATED_MAX_POSITION_PCT = 0.03
_ATR_STOP_MULT       = 2.0    # stop = entry − ATR × 2.0
_ATR_PERIOD          = 14
_RISK_PCT_DEFAULT    = 1.0    # risk 1% of account per trade


# ── Public API ────────────────────────────────────────────────────────────────

def run_position_sizing(payload: dict) -> dict:
    """
    Input keys:
      Required:
        decision          : str  — STRONG_BUY | BUY | WATCH | HOLD | TRIM | SELL | AVOID
        top_tier_score    : int  — 0-100
        market_regime     : str  — RISK_ON | NEUTRAL | RISK_OFF | CRASH_RISK
        risk_budget_mult  : float — 0.0-1.5 from market_regime_engine
        chase_risk_score  : int  — 0-100 from risk_engine
        ohlcv             : dict — normalized {closes, highs, lows, volumes}

      Optional (enable dollar/share output):
        account_size      : float  — total portfolio value USD (default 0 = skip)
        entry_price       : float  — target entry price (default = last close)
        stop_loss_price   : float  — override stop (default = ATR-based)
        risk_per_trade_pct: float  — max % of account to risk (default 1.0)
        max_position_pct  : float  — hard cap on position size % (default 15.0)
        win_rate_estimate : float  — 0-1 (default 0.55)
        reward_risk_ratio : float  — r/r multiple (default 2.0)
        kelly_enabled     : bool   — must be explicitly true after calibration
        kelly_multiplier  : float  — default quarter-Kelly (0.25)

    Returns:
      position_size_level : "NO_TRADE" | "TINY" | "SMALL" | "NORMAL" | "AGGRESSIVE"
      sizing_method       : str
      recommended_dollars : float | None
      recommended_shares  : int | None
      max_loss_dollars    : float | None
      position_pct_of_portfolio: float | None
      kelly_fraction      : float
      half_kelly          : float
      atr_14              : float | None
      stop_loss_price     : float | None
      risk_per_share      : float | None
      regime_adjustment   : float
      chase_adjustment    : float
      final_multiplier    : float
      rationale           : list[str]
      warnings            : list[str]
    """
    try:
        decision      = str(payload.get("decision", "WATCH") or "WATCH")
        score         = float(payload.get("top_tier_score", 50) or 50)
        regime        = str(payload.get("market_regime", "NEUTRAL") or "NEUTRAL")
        budget_mult   = float(payload.get("risk_budget_mult", 1.0) or 1.0)
        chase_score   = float(payload.get("chase_risk_score", 50) or 50)
        ohlcv         = payload.get("ohlcv") or {}

        account_size  = float(payload.get("account_size", 0) or 0)
        risk_pct      = float(payload.get("risk_per_trade_pct", _RISK_PCT_DEFAULT) or _RISK_PCT_DEFAULT)
        max_pos_pct   = float(payload.get("max_position_pct", _MAX_POSITION_PCT * 100) or _MAX_POSITION_PCT * 100) / 100
        win_rate      = float(payload.get("win_rate_estimate", _DEFAULT_WIN_RATE) or _DEFAULT_WIN_RATE)
        rr_ratio      = float(payload.get("reward_risk_ratio", _DEFAULT_RR_RATIO) or _DEFAULT_RR_RATIO)
        kelly_enabled = payload.get("kelly_enabled") is True
        kelly_mult    = float(payload.get("kelly_multiplier", 0.25) or 0.25)

        # Clamp inputs
        win_rate    = max(0.01, min(0.99, win_rate))
        rr_ratio    = max(0.1, min(10.0, rr_ratio))
        max_pos_pct = max(0.01, min(0.50, max_pos_pct))
        risk_pct    = max(0.1, min(5.0, risk_pct))
        kelly_mult  = max(0.10, min(0.50, kelly_mult))

        rationale: list[str] = []
        warnings:  list[str] = []

        if not kelly_enabled:
            max_pos_pct = min(max_pos_pct, _UNCALIBRATED_MAX_POSITION_PCT)
            warnings.append(
                "Kelly 已停用：尚未通過獨立樣本、可信區間與校準誤差門檻；單檔上限 3%"
            )

        # ── Gate: non-buy decisions ───────────────────────────────────────────
        if decision not in _DECISION_ALLOWED:
            return _no_trade(f"決策 {decision} 不需要建倉計算", rationale, warnings)

        # ── Gate: regime block ────────────────────────────────────────────────
        regime_adj = _REGIME_MULT.get(regime, 0.6) * budget_mult
        regime_adj = min(1.5, max(0.0, regime_adj))
        if regime_adj == 0.0:
            return _no_trade(f"市場環境 {regime}：禁止新倉", rationale, warnings)

        # ── ATR ───────────────────────────────────────────────────────────────
        closes  = ohlcv.get("closes", [])
        highs   = ohlcv.get("highs",  [])
        lows    = ohlcv.get("lows",   [])
        atr     = _calc_atr(closes, highs, lows, _ATR_PERIOD)

        entry   = float(payload.get("entry_price", 0) or 0)
        if not entry and closes:
            entry = closes[-1]
        entry = float(entry) if entry > 0 else None

        # ── Stop loss ─────────────────────────────────────────────────────────
        stop_override = float(payload.get("stop_loss_price", 0) or 0)
        if stop_override > 0 and entry and stop_override < entry:
            stop_price = stop_override
            rationale.append(f"使用自訂停損 ${stop_price:.2f}")
        elif atr and entry:
            stop_price = entry - atr * _ATR_STOP_MULT
            rationale.append(f"ATR({_ATR_PERIOD})={atr:.2f}，停損 = 入場 − ATR×{_ATR_STOP_MULT} = ${stop_price:.2f}")
        elif entry:
            stop_price = entry * (1 - risk_pct / 100 * 4)
            warnings.append("無法計算 ATR，使用固定 % 估算停損")
        else:
            stop_price = None
            warnings.append("未提供入場價，無法計算停損位")

        risk_per_share = (entry - stop_price) if (entry and stop_price and entry > stop_price) else None

        # ── Kelly criterion ───────────────────────────────────────────────────
        raw_kelly_f = _kelly(win_rate, rr_ratio)
        kelly_f = raw_kelly_f if kelly_enabled else 0.0
        fractional_k = kelly_f * kelly_mult
        half_k = kelly_f / 2
        if kelly_enabled:
            rationale.append(
                f"保守勝率下界 {win_rate:.1%}，Kelly f*={kelly_f:.1%} → "
                f"{kelly_mult:.0%}-Kelly={fractional_k:.1%}（R/R={rr_ratio:.1f}）"
            )
        else:
            rationale.append("未校準勝率不進入 Kelly 計算")

        # ── Chase risk adjustment ─────────────────────────────────────────────
        chase_adj = _chase_adjustment(chase_score)
        if chase_adj < 1.0:
            rationale.append(f"追高風險 {chase_score:.0f} → 倉位乘數 {chase_adj:.0%}")

        # ── Score bonus ───────────────────────────────────────────────────────
        score_adj = 1.0
        if score >= 85:
            score_adj = 1.2
            rationale.append(f"頂級分數 {score} → 倉位加成 +20%")
        elif score >= 70:
            score_adj = 1.0
        elif score < 55:
            score_adj = 0.7
            rationale.append(f"分數偏低 {score} → 倉位減少 30%")

        # ── Final multiplier ──────────────────────────────────────────────────
        final_mult = regime_adj * chase_adj * score_adj
        final_mult = round(min(1.5, max(0.0, final_mult)), 3)
        rationale.append(
            f"最終乘數 = 環境{regime_adj:.2f} × 追高{chase_adj:.2f} × 分數{score_adj:.2f} = {final_mult:.2f}"
        )

        # ── Compute position sizes (fraction of portfolio) ────────────────────
        sizing_results: dict[str, float] = {}

        # Method 1: Fixed-risk
        if account_size > 0 and risk_per_share and entry:
            risk_dollars         = account_size * (risk_pct / 100) * final_mult
            fixed_risk_shares    = risk_dollars / risk_per_share
            fixed_risk_dollars   = fixed_risk_shares * entry
            fixed_risk_pct       = fixed_risk_dollars / account_size
            sizing_results["fixed_risk"] = min(fixed_risk_pct, max_pos_pct)
        elif entry and risk_per_share:
            # Relative-only mode (no account size)
            # Account-free mode leaves fixed-risk unavailable.
            sizing_results["fixed_risk"] = None

        # Method 2: fractional Kelly, only after explicit calibration approval.
        kelly_size_pct = min(fractional_k * final_mult, max_pos_pct)
        if kelly_enabled and kelly_size_pct > 0:
            sizing_results["fractional_kelly"] = kelly_size_pct

        # Method 3: Volatility-adjusted (ATR-based)
        # Target: position such that daily portfolio-vol contribution = 0.2%
        # i.e. position_pct * (ATR/entry) = 0.002  →  position_pct = 0.002 / vol_pct
        if atr and entry:
            vol_pct = atr / entry
            vol_adj_size = (0.002 / max(vol_pct, 0.002)) * final_mult
            sizing_results["vol_adjusted"] = min(vol_adj_size, max_pos_pct)

        # ── Choose: use fixed_risk primary; fall back to half_kelly; vol_adjusted caps ──
        # All three valid → final = min(fixed_risk or kelly, vol_adjusted) to respect volatility
        valid_sizes = {k: v for k, v in sizing_results.items() if v is not None and v > 0}
        if not valid_sizes:
            chosen_pct = 0.0
            method = "risk_only_unavailable"
        elif len(valid_sizes) == 1:
            method    = next(iter(valid_sizes))
            chosen_pct = valid_sizes[method]
        else:
            # Primary: prefer fixed-risk, then calibrated Kelly; volatility is a cap.
            primary_methods = {k: v for k, v in valid_sizes.items() if k != "vol_adjusted"}
            primary_pct = min(primary_methods.values()) if primary_methods else kelly_size_pct
            vol_adj_pct = valid_sizes.get("vol_adjusted", primary_pct)
            # vol_adjusted caps only when meaningfully smaller (> 30% difference)
            if vol_adj_pct < primary_pct * 0.7:
                chosen_pct = vol_adj_pct
                method = "vol_adjusted"
            else:
                # Pick smallest primary method
                method = min(primary_methods, key=lambda k: primary_methods[k]) if primary_methods else "vol_adjusted"
                chosen_pct = primary_methods.get(method, kelly_size_pct)

        chosen_pct = max(0.0, min(max_pos_pct, chosen_pct))

        # ── Dollar / share output ─────────────────────────────────────────────
        rec_dollars = None
        rec_shares  = None
        max_loss    = None
        pos_pct_out = None

        if account_size > 0 and chosen_pct > 0:
            rec_dollars = round(account_size * chosen_pct, 2)
            if entry and entry > 0:
                rec_shares  = max(1, int(rec_dollars / entry))
                rec_dollars = round(rec_shares * entry, 2)  # align to whole shares
            if risk_per_share and rec_shares:
                max_loss = round(rec_shares * risk_per_share, 2)
            pos_pct_out = round(chosen_pct * 100, 2)

        # ── Level label ───────────────────────────────────────────────────────
        level = _pct_to_level(chosen_pct)

        # Sanity: BUY decision max NORMAL (AGGRESSIVE reserved for STRONG_BUY only)
        if decision == "BUY" and level == "AGGRESSIVE":
            level = "NORMAL"
            warnings.append("BUY 信號最高建議 NORMAL 倉位（AGGRESSIVE 需 STRONG_BUY 才適用）")

        # Sanity: if decision=BUY and score<60, at most SMALL
        if decision == "BUY" and score < 60 and level == "NORMAL":
            level = "SMALL"
            warnings.append(f"分數 {score:.0f} < 60，NORMAL 降級為 SMALL")

        # Sanity: if decision=STRONG_BUY with high chase risk, at most NORMAL
        if decision == "STRONG_BUY" and chase_score > 60 and level == "AGGRESSIVE":
            level = "NORMAL"
            warnings.append(f"STRONG_BUY 但追高風險 {chase_score:.0f} > 60，AGGRESSIVE 降級為 NORMAL")

        if level == "NO_TRADE":
            rationale.append("計算結果倉位過小，建議不操作")

        return {
            "ok":                      True,
            "engine":                  "position_sizing",
            # ── Core output ──────────────────────────────────────────────────
            "position_size_level":     level,
            "sizing_method":           method,
            # ── Dollar/share details (None if no account_size) ────────────────
            "recommended_dollars":     rec_dollars,
            "recommended_shares":      rec_shares,
            "max_loss_dollars":        max_loss,
            "position_pct_of_portfolio": pos_pct_out,
            # ── Kelly ─────────────────────────────────────────────────────────
            "kelly_fraction":          round(kelly_f, 4),
            "half_kelly":              round(half_k, 4),
            "fractional_kelly":        round(fractional_k, 4),
            "raw_kelly_fraction":      round(raw_kelly_f, 4),
            "kelly_enabled":           kelly_enabled,
            "kelly_multiplier":        kelly_mult,
            "calibration_status":      "QUALIFIED" if kelly_enabled else "INSUFFICIENT",
            "win_rate_estimate":       win_rate,
            "reward_risk_ratio":       rr_ratio,
            # ── Risk / stop ───────────────────────────────────────────────────
            "entry_price":             round(entry, 4)       if entry      else None,
            "stop_loss_price":         round(stop_price, 4)  if stop_price else None,
            "risk_per_share":          round(risk_per_share, 4) if risk_per_share else None,
            "atr_14":                  round(atr, 4)         if atr        else None,
            # ── Adjustments ───────────────────────────────────────────────────
            "regime_adjustment":       round(regime_adj, 3),
            "chase_adjustment":        round(chase_adj, 3),
            "score_adjustment":        round(score_adj, 3),
            "final_multiplier":        final_mult,
            # ── Sizing method breakdown ───────────────────────────────────────
            "sizing_breakdown":        {
                k: round(v * 100, 2) if v else None
                for k, v in sizing_results.items()
            },
            # ── Narrative ─────────────────────────────────────────────────────
            "rationale":  rationale,
            "warnings":   warnings,
        }

    except Exception as e:
        import traceback
        traceback.print_exc()
        return {"ok": False, "error": str(e)}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _kelly(win_rate: float, rr: float) -> float:
    """Kelly criterion: f* = (p*b - q) / b, capped at 0.25."""
    q = 1.0 - win_rate
    f = (win_rate * rr - q) / rr
    return max(0.0, min(0.25, f))


def _chase_adjustment(chase_score: float) -> float:
    """Return position multiplier based on chase risk score."""
    if chase_score <= 30:  return 1.00
    if chase_score <= 50:  return 0.90
    if chase_score <= 65:  return 0.75
    if chase_score <= 80:  return 0.55
    if chase_score <= 90:  return 0.35
    return 0.20


def _pct_to_level(pct: float) -> str:
    for threshold, label in _LEVEL_MAP:
        if pct >= threshold:
            return label
    return "NO_TRADE"


def _calc_atr(closes: list, highs: list, lows: list, period: int) -> float | None:
    n = min(len(closes), len(highs), len(lows))
    if n < period + 1:
        return None
    closes = closes[-n:]
    highs  = highs[-n:]
    lows   = lows[-n:]

    trs = []
    for i in range(1, n):
        h, l, pc = highs[i], lows[i], closes[i - 1]
        if not (h and l and pc and h > 0 and l > 0 and pc > 0):
            continue
        tr = max(h - l, abs(h - pc), abs(l - pc))
        trs.append(tr)

    if len(trs) < period:
        return None

    return sum(trs[-period:]) / period


def _no_trade(reason: str, rationale: list, warnings: list) -> dict:
    rationale.append(reason)
    return {
        "ok":                      True,
        "engine":                  "position_sizing",
        "position_size_level":     "NO_TRADE",
        "sizing_method":           "blocked",
        "recommended_dollars":     None,
        "recommended_shares":      None,
        "max_loss_dollars":        None,
        "position_pct_of_portfolio": None,
        "kelly_fraction":          0.0,
        "half_kelly":              0.0,
        "fractional_kelly":        0.0,
        "raw_kelly_fraction":      0.0,
        "kelly_enabled":           False,
        "kelly_multiplier":        0.25,
        "calibration_status":      "BLOCKED",
        "win_rate_estimate":       _DEFAULT_WIN_RATE,
        "reward_risk_ratio":       _DEFAULT_RR_RATIO,
        "entry_price":             None,
        "stop_loss_price":         None,
        "risk_per_share":          None,
        "atr_14":                  None,
        "regime_adjustment":       0.0,
        "chase_adjustment":        1.0,
        "score_adjustment":        1.0,
        "final_multiplier":        0.0,
        "sizing_breakdown":        {},
        "rationale":               rationale,
        "warnings":                warnings,
    }
