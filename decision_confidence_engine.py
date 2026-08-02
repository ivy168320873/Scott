"""Explainable confidence card for a top-tier stock decision.

The score measures evidence completeness and consistency.  It is deliberately
not presented as a probability of profit.  The engine is pure and deterministic
when ``now`` is supplied, which keeps the decision layer easy to test.
"""
from __future__ import annotations

import math
from datetime import date, datetime, timezone


_BUY_DECISIONS = {"STRONG_BUY", "BUY"}
_DEFENSIVE_DECISIONS = {"TRIM", "SELL", "AVOID"}


def build_confidence_card(
    *,
    symbol: str,
    decision: str,
    composite_score: float,
    data_quality: dict | None,
    market_regime: dict | None,
    chase_risk: dict | None,
    position_sizing: dict | None = None,
    signal_calibration: dict | None = None,
    kill_signal: dict | None = None,
    blockers: list[str] | None = None,
    risk_controls: list[str] | None = None,
    now: datetime | None = None,
) -> dict:
    """Return a mobile-friendly, explainable reliability assessment."""
    now = _as_utc(now or datetime.now(timezone.utc))
    symbol = str(symbol or "").upper().strip()
    decision = str(decision or "WATCH").upper().strip()
    composite = _clamp(composite_score)
    dq = data_quality or {}
    mr = market_regime or {}
    cr = chase_risk or {}
    pos = position_sizing or {}
    calibration = signal_calibration or {}
    kill = kill_signal or {}

    data_score = _clamp(dq.get("data_quality_score"), default=0)
    age_days, freshness_score, freshness_status = _freshness(dq.get("last_date"), now)
    support_score = _decision_support(decision, composite)
    market_score = _market_agreement(decision, str(mr.get("market_regime") or "NEUTRAL"))
    risk_score = _risk_agreement(decision, cr, pos, kill)

    sample_size = _int(calibration.get("sample_size"), 0)
    evaluated_size = _int(calibration.get("evaluated_size"), 0)
    historical_score = _clamp(calibration.get("confidence_score"), default=50)

    source = str(dq.get("source") or "unknown")
    bar_count = _int(dq.get("bar_count"), 0)
    regime = str(mr.get("market_regime") or "NEUTRAL")
    chase_score = _clamp(cr.get("score"), default=50)

    evidence = [
        _evidence(
            "data_quality", "資料品質", data_score, 25,
            f"{dq.get('data_status', 'UNKNOWN')} · {source} · {bar_count} 根K線",
        ),
        _evidence(
            "freshness", "資料時效", freshness_score, 15,
            _freshness_note(dq.get("last_date"), age_days, freshness_status),
        ),
        _evidence(
            "decision_support", "決策訊號一致性", support_score, 20,
            f"{decision} 與綜合分 {round(composite)} 的一致程度",
        ),
        _evidence(
            "market_agreement", "市場環境配合", market_score, 15,
            f"{regime} 對 {decision} 決策的支持程度",
        ),
        _evidence(
            "risk_agreement", "風險控制一致性", risk_score, 15,
            f"追高風險 {round(chase_score)} · 倉位 {pos.get('position_size_level', '未提供')}",
        ),
        _evidence(
            "historical_calibration", "歷史校準", historical_score, 10,
            _history_note(calibration, sample_size, evaluated_size),
        ),
    ]

    raw_score = round(sum(item["score"] * item["weight"] / 100 for item in evidence))
    active_caps: list[tuple[int, str]] = []
    status = str(dq.get("data_status") or "BAD").upper()
    is_demo = bool(dq.get("is_demo", False))

    if is_demo:
        active_caps.append((25, "Demo 合成資料：可信度最高 25"))
    if status == "BAD":
        active_caps.append((30, "資料品質 BAD：可信度最高 30"))
    if bar_count < 20:
        active_caps.append((30, f"K線僅 {bar_count} 根：可信度最高 30"))
    if age_days is None:
        active_caps.append((55, "缺少最後更新日期：可信度最高 55"))
    elif age_days > 7:
        active_caps.append((45, f"資料已 {age_days:.0f} 天未更新：可信度最高 45"))
    if evaluated_size == 0:
        active_caps.append((75, "尚無完成 5 日驗證的歷史樣本：可信度最高 75"))
    elif evaluated_size < 5:
        active_caps.append((80, f"完成驗證樣本僅 {evaluated_size} 筆：可信度最高 80"))
    elif evaluated_size < 20:
        active_caps.append((90, f"完成驗證樣本 {evaluated_size} 筆：可信度最高 90"))
    if decision in _BUY_DECISIONS and blockers:
        active_caps.append((45, "買進決策仍有阻擋條件：可信度最高 45"))
    if decision in _BUY_DECISIONS and bool(kill.get("triggered")):
        active_caps.append((40, "買進決策與 Kill Signal 衝突：可信度最高 40"))
    if calibration.get("can_use") is False:
        active_caps.append((40, "歷史校準已停用此訊號：可信度最高 40"))

    score_cap = min((cap for cap, _ in active_caps), default=100)
    final_score = min(raw_score, score_cap)
    grade, level, label, color = _rating(final_score)

    normalized_blockers = _unique_text(blockers or [])
    if status == "BAD":
        normalized_blockers.extend(
            item for item in _unique_text(dq.get("warnings") or [])
            if item not in normalized_blockers
        )

    invalidation = _invalidation_conditions(
        decision=decision,
        composite=composite,
        dq=dq,
        mr=mr,
        cr=cr,
        pos=pos,
        kill=kill,
        age_days=age_days,
    )
    strongest = sorted(evidence, key=lambda item: item["score"], reverse=True)[:2]
    weakest = sorted(evidence, key=lambda item: item["score"])[:2]

    return {
        "schema_version": "1.0",
        "symbol": symbol,
        "decision": decision,
        "confidence_score": final_score,
        "raw_score": raw_score,
        "score_cap": score_cap,
        "grade": grade,
        "level": level,
        "label": label,
        "color": color,
        "score_meaning": "決策證據的完整度與一致性，非勝率、非獲利機率",
        "generated_at": now.isoformat(),
        "evidence": evidence,
        "strongest_evidence": [item["label"] for item in strongest],
        "weak_points": [item["label"] for item in weakest if item["score"] < 70],
        "data_freshness": {
            "source": source,
            "last_date": dq.get("last_date"),
            "age_days": round(age_days, 1) if age_days is not None else None,
            "status": freshness_status,
            "bar_count": bar_count,
        },
        "historical_calibration": {
            "sample_size": sample_size,
            "evaluated_size": evaluated_size,
            "win_rate_1d": calibration.get("win_rate_1d"),
            "win_rate_3d": calibration.get("win_rate_3d"),
            "win_rate_5d": calibration.get("win_rate_5d"),
            "avg_return_5d": calibration.get("avg_return_5d"),
            "avg_relative_return_5d": calibration.get("avg_relative_return_5d"),
            "false_signal_rate": calibration.get("false_signal_rate"),
            "stop_loss_rate": calibration.get("stop_loss_rate"),
            "recommendation": calibration.get("recommendation", "WATCH"),
        },
        "caps_applied": [reason for _, reason in active_caps],
        "blockers": normalized_blockers,
        "risk_controls": _unique_text(risk_controls or []),
        "invalidation_conditions": invalidation,
        "disclaimer": "此分數是決策輔助資訊，不構成投資建議，也不會自動下單。",
    }


def _evidence(id_: str, label: str, score: float, weight: int, note: str) -> dict:
    return {
        "id": id_,
        "label": label,
        "score": round(_clamp(score)),
        "weight": weight,
        "note": str(note or ""),
    }


def _decision_support(decision: str, composite: float) -> float:
    if decision in _BUY_DECISIONS:
        return composite
    if decision in _DEFENSIVE_DECISIONS:
        return 100 - composite
    return 100 - abs(composite - 50) * 2


def _market_agreement(decision: str, regime: str) -> float:
    regime = regime.upper()
    if decision in _BUY_DECISIONS:
        return {"RISK_ON": 100, "NEUTRAL": 65, "RISK_OFF": 10, "CRASH_RISK": 0}.get(regime, 45)
    if decision in _DEFENSIVE_DECISIONS:
        return {"RISK_ON": 45, "NEUTRAL": 65, "RISK_OFF": 90, "CRASH_RISK": 100}.get(regime, 55)
    return {"RISK_ON": 70, "NEUTRAL": 100, "RISK_OFF": 85, "CRASH_RISK": 75}.get(regime, 70)


def _risk_agreement(decision: str, chase_risk: dict, position: dict, kill: dict) -> float:
    chase = _clamp(chase_risk.get("score"), default=50)
    position_level = str(position.get("position_size_level") or "NO_TRADE")
    position_support = {
        "AGGRESSIVE": 100, "NORMAL": 90, "SMALL": 75,
        "TINY": 60, "NO_TRADE": 20,
    }.get(position_level, 50)
    if decision in _BUY_DECISIONS:
        return (100 - chase) * 0.7 + position_support * 0.3
    if decision in _DEFENSIVE_DECISIONS:
        kill_support = 100 if kill.get("triggered") else 55
        no_trade_support = 100 if position_level == "NO_TRADE" else 55
        return chase * 0.5 + kill_support * 0.3 + no_trade_support * 0.2
    centered_chase = max(45, 100 - abs(chase - 50) * 1.1)
    no_trade_support = 90 if position_level == "NO_TRADE" else 65
    return centered_chase * 0.75 + no_trade_support * 0.25


def _freshness(last_date, now: datetime) -> tuple[float | None, float, str]:
    parsed = _parse_date(last_date)
    if parsed is None:
        return None, 20, "UNKNOWN"
    age = max(0.0, (now.date() - parsed).days)
    if age <= 1:
        return age, 100, "FRESH"
    if age <= 3:
        return age, 90, "FRESH"
    if age <= 5:
        return age, 75, "AGING"
    if age <= 7:
        return age, 55, "AGING"
    if age <= 14:
        return age, 25, "STALE"
    return age, 5, "STALE"


def _freshness_note(last_date, age_days: float | None, status: str) -> str:
    if age_days is None:
        return "缺少最後更新日期"
    return f"最後交易日 {last_date} · {age_days:.0f} 天前 · {status}"


def _history_note(calibration: dict, sample_size: int, evaluated_size: int) -> str:
    if sample_size == 0:
        return "尚無歷史訊號；系統會從本次開始累積"
    win_rate = calibration.get("win_rate_5d")
    win_text = f" · 5日命中率 {win_rate}%" if win_rate is not None else ""
    return f"{sample_size} 筆訊號 · {evaluated_size} 筆完成5日驗證{win_text}"


def _invalidation_conditions(*, decision, composite, dq, mr, cr, pos, kill, age_days) -> list[str]:
    conditions: list[str] = []
    if age_days is None or age_days > 4:
        conditions.append("取得更新交易日資料後必須重新計算")
    conditions.append("資料品質分數低於 55 或資料來源切換為 Demo 時，本判斷失效")

    regime = str(mr.get("market_regime") or "NEUTRAL")
    chase = _clamp(cr.get("score"), default=50)
    stop = pos.get("stop_loss_price")
    if decision in _BUY_DECISIONS:
        stop_value = _positive_number(stop)
        if stop_value is not None:
            conditions.append(f"收盤跌破風控停損價 {stop_value:.2f} 時，買進判斷失效")
        conditions.append("市場轉為 RISK_OFF 或 CRASH_RISK 時，停止新增部位")
        if chase < 80:
            conditions.append("追高風險升至 80 以上時，買進判斷降級")
    elif decision in _DEFENSIVE_DECISIONS:
        conditions.append("市場恢復 RISK_ON 且綜合分回升至 70 以上時，重新評估防守決策")
        if kill.get("triggered"):
            conditions.append("Kill Signal 解除後，不沿用本次賣出／減倉判斷")
    else:
        conditions.append("綜合分升至 70 以上或降至 35 以下時，觀察判斷失效並重新分類")
        if regime == "RISK_ON" and composite >= 65:
            conditions.append("若追高風險降至 50 以下，可重新評估進場")
    return _unique_text(conditions)[:6]


def _rating(score: int) -> tuple[str, str, str, str]:
    if score >= 85:
        return "A", "HIGH", "高度可信", "#3fb950"
    if score >= 70:
        return "B", "GOOD", "可信", "#58a6ff"
    if score >= 55:
        return "C", "MEDIUM", "中等", "#e3b341"
    if score >= 40:
        return "D", "LOW", "偏低", "#f0883e"
    return "F", "BLOCKED", "證據不足", "#f85149"


def _parse_date(value) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()
    except (TypeError, ValueError):
        return None


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _clamp(value, default: float = 50) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    if not math.isfinite(number):
        number = default
    return max(0.0, min(100.0, number))


def _positive_number(value) -> float | None:
    try:
        number = float(value)
        if number > 0 and math.isfinite(number):
            return number
    except (TypeError, ValueError):
        pass
    return None


def _int(value, default: int = 0) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return default


def _unique_text(values) -> list[str]:
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in result:
            result.append(text)
    return result
