"""Persistent personal risk profile and deterministic pre-trade gate."""

from __future__ import annotations

import math
import os
import re
from copy import deepcopy

from .storage import connect, dumps, loads, utc_now


PRESETS: dict[str, dict] = {
    "conservative": {
        "max_position_pct": 5.0,
        "max_sector_pct": 20.0,
        "max_total_exposure_pct": 55.0,
        "risk_per_trade_pct": 0.5,
        "max_daily_loss_pct": 1.5,
        "max_drawdown_pct": 8.0,
        "min_signal_confidence": 72.0,
        "min_reward_risk": 2.0,
        "allow_margin": False,
    },
    "balanced": {
        "max_position_pct": 10.0,
        "max_sector_pct": 30.0,
        "max_total_exposure_pct": 75.0,
        "risk_per_trade_pct": 1.0,
        "max_daily_loss_pct": 2.5,
        "max_drawdown_pct": 12.0,
        "min_signal_confidence": 65.0,
        "min_reward_risk": 1.8,
        "allow_margin": False,
    },
    "aggressive": {
        "max_position_pct": 15.0,
        "max_sector_pct": 40.0,
        "max_total_exposure_pct": 95.0,
        "risk_per_trade_pct": 1.5,
        "max_daily_loss_pct": 4.0,
        "max_drawdown_pct": 18.0,
        "min_signal_confidence": 58.0,
        "min_reward_risk": 1.5,
        "allow_margin": False,
    },
}

_NUMERIC_LIMITS = {
    "account_size": (0.0, 1_000_000_000_000.0),
    "max_position_pct": (0.1, 50.0),
    "max_sector_pct": (1.0, 100.0),
    "max_total_exposure_pct": (1.0, 100.0),
    "risk_per_trade_pct": (0.1, 5.0),
    "max_daily_loss_pct": (0.5, 20.0),
    "max_drawdown_pct": (1.0, 50.0),
    "min_signal_confidence": (0.0, 100.0),
    "min_reward_risk": (0.5, 10.0),
}
_SYMBOL_RE = re.compile(r"[A-Z0-9.\-]{1,20}")


def _init_db(db_path: str | None = None) -> None:
    with connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS personal_risk_profile (
                profile_id TEXT PRIMARY KEY,
                profile_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )


def default_profile(preset: str | None = None) -> dict:
    selected = (preset or os.environ.get("DEFAULT_RISK_PRESET", "balanced")).lower()
    if selected not in PRESETS:
        selected = "balanced"
    result = deepcopy(PRESETS[selected])
    result.update(
        {
            "profile_id": "default",
            "preset": selected,
            "account_size": 0.0,
            "blocked_symbols": [],
            "notes": "",
            "updated_at": None,
        }
    )
    return result


def _bounded_number(key: str, value) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{key} 必須是數字") from exc
    lo, hi = _NUMERIC_LIMITS[key]
    if not math.isfinite(number) or not lo <= number <= hi:
        raise ValueError(f"{key} 必須介於 {lo:g} 與 {hi:g} 之間")
    return round(number, 4)


def _normalise_profile(profile: dict) -> dict:
    if not isinstance(profile, dict):
        raise ValueError("risk profile 必須是 JSON object")
    preset = str(profile.get("preset") or "balanced").lower()
    if preset not in PRESETS and preset != "custom":
        raise ValueError("preset 必須是 conservative、balanced、aggressive 或 custom")

    result = default_profile(preset if preset in PRESETS else "balanced")
    result["preset"] = preset
    for key in _NUMERIC_LIMITS:
        if key in profile:
            result[key] = _bounded_number(key, profile[key])

    allow_margin = profile.get("allow_margin", result["allow_margin"])
    if not isinstance(allow_margin, bool):
        raise ValueError("allow_margin 必須是 boolean")
    result["allow_margin"] = allow_margin

    blocked = profile.get("blocked_symbols", [])
    if not isinstance(blocked, list) or len(blocked) > 100:
        raise ValueError("blocked_symbols 必須是最多 100 筆的陣列")
    normalised_symbols: list[str] = []
    for value in blocked:
        symbol = str(value or "").upper().strip()
        if not _SYMBOL_RE.fullmatch(symbol):
            raise ValueError(f"無效股票代號：{symbol[:30]}")
        if symbol not in normalised_symbols:
            normalised_symbols.append(symbol)
    result["blocked_symbols"] = normalised_symbols
    result["notes"] = str(profile.get("notes") or "").strip()[:500]
    result["profile_id"] = "default"
    result["updated_at"] = profile.get("updated_at")

    if result["max_position_pct"] > result["max_sector_pct"]:
        raise ValueError("max_position_pct 不可高於 max_sector_pct")
    if result["max_sector_pct"] > result["max_total_exposure_pct"]:
        raise ValueError("max_sector_pct 不可高於 max_total_exposure_pct")
    return result


def get_profile(db_path: str | None = None) -> dict:
    _init_db(db_path)
    with connect(db_path) as conn:
        row = conn.execute(
            "SELECT profile_json, updated_at FROM personal_risk_profile WHERE profile_id='default'"
        ).fetchone()
    if not row:
        return default_profile()
    stored = loads(row["profile_json"], {})
    try:
        result = _normalise_profile(stored)
    except ValueError:
        return default_profile()
    result["updated_at"] = row["updated_at"]
    return result


def save_profile(patch: dict, db_path: str | None = None) -> dict:
    if not isinstance(patch, dict):
        raise ValueError("risk profile 必須是 JSON object")
    current = get_profile(db_path)
    requested_preset = str(patch.get("preset") or current["preset"]).lower()
    if requested_preset != current["preset"] and requested_preset in PRESETS:
        merged = default_profile(requested_preset)
    else:
        merged = dict(current)
    merged.update(patch)
    profile = _normalise_profile(merged)
    profile["updated_at"] = utc_now()
    _init_db(db_path)
    with connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO personal_risk_profile(profile_id, profile_json, updated_at)
            VALUES('default', ?, ?)
            ON CONFLICT(profile_id) DO UPDATE SET
                profile_json=excluded.profile_json,
                updated_at=excluded.updated_at
            """,
            (dumps(profile), profile["updated_at"]),
        )
    return profile


def assess_trade(
    proposal: dict,
    profile: dict | None = None,
    portfolio: dict | None = None,
) -> dict:
    """Apply the saved constraints to one proposed long trade.

    This function never submits an order.  It returns a deterministic PASS,
    REDUCE, or BLOCK decision plus the maximum safe whole-share quantity.
    """
    if not isinstance(proposal, dict):
        raise ValueError("proposal 必須是 JSON object")
    profile = _normalise_profile(profile or default_profile())
    portfolio = portfolio if isinstance(portfolio, dict) else {}
    symbol = str(proposal.get("symbol") or "").upper().strip()
    decision = str(proposal.get("decision") or "WATCH").upper().strip()
    reasons: list[str] = []
    warnings: list[str] = []
    blockers: list[str] = []

    if not _SYMBOL_RE.fullmatch(symbol):
        blockers.append("股票代號無效")
    if symbol in profile["blocked_symbols"]:
        blockers.append(f"{symbol} 位於個人禁止清單")
    if bool(proposal.get("is_demo", False)):
        blockers.append("資料為 Demo / fallback，不允許建立交易")
    if str(proposal.get("data_quality_status") or "OK").upper() == "BAD":
        blockers.append("資料品質 BAD")
    if decision not in {"BUY", "STRONG_BUY"}:
        blockers.append(f"決策為 {decision}，不是可建倉訊號")

    try:
        confidence = float(proposal.get("confidence_score", 0) or 0)
    except (TypeError, ValueError, OverflowError):
        confidence = 0.0
    if not math.isfinite(confidence) or confidence < profile["min_signal_confidence"]:
        blockers.append(
            f"可信度 {max(0.0, confidence):.1f} 低於個人門檻 {profile['min_signal_confidence']:.1f}"
        )

    def finite_value(source: dict, key: str, default: float = 0.0) -> float:
        try:
            value = float(source.get(key, default) or default)
            return value if math.isfinite(value) else default
        except (TypeError, ValueError, OverflowError):
            return default

    entry = finite_value(proposal, "entry_price")
    stop = finite_value(proposal, "stop_price")
    target = finite_value(proposal, "target_price")
    if not (entry > 0 and 0 < stop < entry):
        blockers.append("進場價與停損價必須符合 0 < stop < entry")
    reward_risk = (target - entry) / (entry - stop) if target > entry > stop > 0 else 0.0
    if reward_risk < profile["min_reward_risk"]:
        blockers.append(
            f"報酬風險比 {reward_risk:.2f} 低於門檻 {profile['min_reward_risk']:.2f}"
        )

    account_value = finite_value(portfolio, "account_value", profile["account_size"])
    if account_value <= 0:
        account_value = profile["account_size"]
    if account_value <= 0:
        blockers.append("請先設定帳戶資金，才能計算個人風險額度")
    daily_pnl_pct = finite_value(portfolio, "daily_pnl_pct")
    drawdown_pct = max(0.0, finite_value(portfolio, "drawdown_pct"))
    total_exposure_pct = max(0.0, finite_value(portfolio, "total_exposure_pct"))
    sector_exposure_pct = max(0.0, finite_value(portfolio, "sector_exposure_pct"))

    if daily_pnl_pct <= -profile["max_daily_loss_pct"]:
        blockers.append(
            f"今日損失 {daily_pnl_pct:.2f}% 已達停機線 -{profile['max_daily_loss_pct']:.2f}%"
        )
    if drawdown_pct >= profile["max_drawdown_pct"]:
        blockers.append(
            f"組合回撤 {drawdown_pct:.2f}% 已達上限 {profile['max_drawdown_pct']:.2f}%"
        )
    remaining_exposure = max(0.0, profile["max_total_exposure_pct"] - total_exposure_pct)
    remaining_sector = max(0.0, profile["max_sector_pct"] - sector_exposure_pct)
    if remaining_exposure <= 0:
        blockers.append("總曝險已達個人上限")
    if remaining_sector <= 0:
        blockers.append("產業曝險已達個人上限")

    max_position_pct = min(
        profile["max_position_pct"], remaining_exposure, remaining_sector
    )
    requested_pct = finite_value(proposal, "requested_position_pct", max_position_pct)
    recommended_pct = max(0.0, min(requested_pct or max_position_pct, max_position_pct))
    status = "BLOCK" if blockers else "PASS"
    if not blockers and requested_pct > max_position_pct:
        status = "REDUCE"
        warnings.append(
            f"要求倉位 {requested_pct:.2f}% 已縮減為 {max_position_pct:.2f}%"
        )

    max_notional = account_value * recommended_pct / 100 if account_value > 0 else None
    risk_budget = account_value * profile["risk_per_trade_pct"] / 100 if account_value > 0 else None
    risk_per_share = entry - stop if entry > stop > 0 else None
    shares_by_position = math.floor(max_notional / entry) if max_notional and entry > 0 else None
    shares_by_risk = math.floor(risk_budget / risk_per_share) if risk_budget and risk_per_share else None
    candidates = [value for value in (shares_by_position, shares_by_risk) if value is not None]
    max_shares = min(candidates) if candidates else None
    if max_shares is not None and max_shares <= 0 and not blockers:
        blockers.append("依風險預算計算後不足 1 股")
        status = "BLOCK"

    if not blockers:
        reasons.append(
            f"可信度 {confidence:.1f} 通過門檻，最大倉位 {recommended_pct:.2f}%"
        )
        reasons.append(
            f"報酬風險比 {reward_risk:.2f}，每筆風險上限 {profile['risk_per_trade_pct']:.2f}%"
        )

    return {
        "allowed": not blockers,
        "status": status,
        "symbol": symbol,
        "decision": decision,
        "confidence_score": round(max(0.0, min(100.0, confidence)), 2),
        "max_position_pct": round(max_position_pct, 2),
        "recommended_position_pct": round(recommended_pct, 2),
        "max_notional": round(max_notional, 2) if max_notional is not None else None,
        "max_shares": max_shares,
        "risk_budget": round(risk_budget, 2) if risk_budget is not None else None,
        "risk_per_share": round(risk_per_share, 4) if risk_per_share else None,
        "reward_risk_ratio": round(reward_risk, 3),
        "blockers": blockers,
        "warnings": warnings,
        "reasons": reasons,
        "profile": profile,
        "checked_at": utc_now(),
        "disclaimer": "此結果是風險限制器，不是獲利保證或自動下單授權。",
    }
