"""Per-decision confidence scorecard with sample-size aware calibration."""

from __future__ import annotations

import hashlib
import math
from datetime import datetime, timezone


_REQUIRED_EVIDENCE = {"price", "trend", "volume", "risk", "market"}
_DECISION_BASE = {
    "STRONG_BUY": 90.0,
    "BUY": 76.0,
    "WATCH": 55.0,
    "HOLD": 52.0,
    "TRIM": 42.0,
    "SELL": 30.0,
    "AVOID": 20.0,
}


def _number(value, default: float) -> float:
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except (TypeError, ValueError, OverflowError):
        return default


def _clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, value))


def _wilson_lower_bound(wins: int, total: int, z: float = 1.96) -> float | None:
    if total <= 0:
        return None
    p = wins / total
    denominator = 1 + z * z / total
    centre = p + z * z / (2 * total)
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * total)) / total)
    return max(0.0, (centre - margin) / denominator) * 100


def _normalise_evidence(items) -> tuple[list[dict], set[str]]:
    clean: list[dict] = []
    categories: set[str] = set()
    if not isinstance(items, list):
        return clean, categories
    for raw in items[:30]:
        if not isinstance(raw, dict):
            continue
        category = str(raw.get("category") or "other").lower().strip()[:30]
        source = str(raw.get("source") or "unknown").strip()[:100]
        statement = str(raw.get("statement") or "").strip()[:500]
        if not statement:
            continue
        item = {
            "category": category,
            "source": source,
            "statement": statement,
            "observed_at": str(raw.get("observed_at") or "")[:64] or None,
            "is_demo": bool(raw.get("is_demo", False)),
        }
        if "value" in raw:
            item["value"] = raw["value"]
        clean.append(item)
        categories.add(category)
    return clean, categories


def _regime_alignment(decision: str, regime: str) -> float:
    regime = regime.upper()
    if regime == "CRASH_RISK":
        return 10.0 if decision in {"BUY", "STRONG_BUY"} else 90.0
    if regime == "RISK_OFF":
        return 25.0 if decision in {"BUY", "STRONG_BUY"} else 80.0
    if regime == "RISK_ON":
        return 90.0 if decision in {"BUY", "STRONG_BUY", "HOLD"} else 55.0
    return 65.0


def build_scorecard(signal: dict, historical: dict | None = None) -> dict:
    """Build a transparent 0–100 confidence score for one current signal.

    `historical` is normally returned by signal_confidence_engine.  A missing
    or tiny sample never receives an optimistic default: the card is marked
    provisional and its maximum grade is capped.
    """
    if not isinstance(signal, dict):
        raise ValueError("signal 必須是 JSON object")
    historical = historical if isinstance(historical, dict) else {}
    symbol = str(signal.get("symbol") or "").upper().strip()[:20]
    decision = str(signal.get("decision") or signal.get("action_code") or "WATCH").upper()
    data_quality = signal.get("data_quality") if isinstance(signal.get("data_quality"), dict) else {}
    data_status = str(
        signal.get("data_quality_status")
        or data_quality.get("data_status")
        or "UNKNOWN"
    ).upper()
    data_score = _clamp(
        _number(
            signal.get("data_quality_score", data_quality.get("data_quality_score")),
            45.0 if data_status == "UNKNOWN" else 65.0,
        )
    )
    analysis_score = _clamp(
        _number(
            signal.get("top_tier_score", signal.get("total_score")),
            _DECISION_BASE.get(decision, 50.0),
        )
    )
    regime = str(signal.get("market_regime") or "NEUTRAL").upper()
    chase_score = _clamp(
        _number(
            signal.get("chase_risk_score", (signal.get("chase_risk") or {}).get("score")),
            50.0,
        )
    )
    is_demo = bool(signal.get("is_demo", data_quality.get("is_demo", False)))

    evidence, evidence_categories = _normalise_evidence(signal.get("evidence"))
    missing = sorted(_REQUIRED_EVIDENCE - evidence_categories)
    coverage = len(_REQUIRED_EVIDENCE & evidence_categories) / len(_REQUIRED_EVIDENCE)
    evidence_score = coverage * 100.0
    if any(item["is_demo"] for item in evidence):
        is_demo = True

    sample_size = int(
        _number(
            historical.get(
                "outcome_sample_size",
                historical.get("evaluated_size", historical.get("sample_size")),
            ),
            0,
        )
    )
    win_rate = historical.get("paper_win_rate")
    if win_rate is None:
        win_rate = historical.get("win_rate_5d")
    win_rate_value = _number(win_rate, 50.0)
    wins = round(sample_size * win_rate_value / 100) if sample_size else 0
    wilson = _wilson_lower_bound(wins, sample_size)
    historical_raw = _clamp(_number(historical.get("confidence_score"), 40.0))
    sample_reliability = min(1.0, sample_size / 30) if sample_size else 0.0
    historical_score = historical_raw * sample_reliability + 40.0 * (1 - sample_reliability)

    components = {
        "analysis": round(analysis_score, 2),
        "data_quality": round(data_score, 2),
        "historical_calibration": round(historical_score, 2),
        "evidence_coverage": round(evidence_score, 2),
        "regime_alignment": round(_regime_alignment(decision, regime), 2),
    }
    score = (
        components["analysis"] * 0.35
        + components["data_quality"] * 0.20
        + components["historical_calibration"] * 0.20
        + components["evidence_coverage"] * 0.15
        + components["regime_alignment"] * 0.10
    )
    warnings: list[str] = []
    blockers: list[str] = []
    reasons: list[str] = [
        f"模型分析 {analysis_score:.0f}/100",
        f"五類必要證據覆蓋 {coverage:.0%}",
    ]

    if chase_score > 90:
        score -= 20
        warnings.append(f"追高風險 {chase_score:.0f}，可信度額外扣 20 分")
    elif chase_score > 75:
        score -= 10
        warnings.append(f"追高風險 {chase_score:.0f}，可信度額外扣 10 分")
    if sample_size < 5:
        warnings.append("已評估歷史樣本少於 5 筆，評分仍屬 provisional")
    elif sample_size < 20:
        warnings.append(f"歷史樣本僅 {sample_size} 筆，校準可信度有限")
    if missing:
        warnings.append("缺少證據類別：" + "、".join(missing))
    if data_status == "BAD":
        blockers.append("資料品質 BAD")
        score = min(score, 30)
    if is_demo:
        blockers.append("使用 Demo / fallback 資料")
        score = min(score, 25)
    if coverage < 0.6:
        blockers.append("必要證據覆蓋不足 60%")
        score = min(score, 45)

    score = round(_clamp(score), 1)
    if score >= 82:
        grade = "A"
    elif score >= 68:
        grade = "B"
    elif score >= 52:
        grade = "C"
    else:
        grade = "D"
    if sample_size < 5 and grade in {"A", "B"}:
        grade = "C"

    if score >= 82:
        action_cap = decision
    elif score >= 68:
        action_cap = "BUY" if decision == "STRONG_BUY" else decision
    elif score >= 52:
        action_cap = "WATCH"
    else:
        action_cap = "AVOID"
    actionable = (
        not blockers
        and decision in {"BUY", "STRONG_BUY"}
        and score >= 68
        and action_cap in {"BUY", "STRONG_BUY"}
    )

    created_at = datetime.now(timezone.utc).isoformat()
    card_key = f"{symbol}:{decision}:{created_at}"
    return {
        "scorecard_id": hashlib.sha256(card_key.encode()).hexdigest()[:20],
        "symbol": symbol,
        "decision": decision,
        "confidence_score": score,
        "confidence_grade": grade,
        "actionable": actionable,
        "action_cap": action_cap,
        "provisional": sample_size < 20,
        "components": components,
        "historical": {
            "sample_size": sample_size,
            "win_rate_5d": win_rate if win_rate is not None else None,
            "wilson_lower_bound_95": round(wilson, 2) if wilson is not None else None,
            "recommendation": historical.get("recommendation", "WATCH"),
        },
        "evidence_coverage": round(coverage, 3),
        "missing_evidence": missing,
        "evidence": evidence,
        "reasons": reasons,
        "warnings": warnings,
        "blockers": blockers,
        "created_at": created_at,
        "disclaimer": "可信度是資料完整度與歷史校準分數，不代表上漲機率或獲利保證。",
    }
