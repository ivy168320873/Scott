"""Application service orchestrating decision, scorecard, risk and paper loop."""

from __future__ import annotations

import re

import signal_confidence_engine as signal_history
import top_tier_decision_engine as top_tier

from . import paper_trading
from .evidence import build_decision_evidence
from .risk_brain import assess_trade, get_profile
from .scorecard import build_scorecard


_SYMBOL_RE = re.compile(r"[A-Z0-9.\-]{1,20}")


def _symbol(value) -> str:
    result = str(value or "").upper().strip()
    if not _SYMBOL_RE.fullmatch(result):
        raise ValueError("股票代號無效")
    return result


def _proposal_from_decision(decision: dict, scorecard: dict, profile: dict) -> dict:
    sizing = decision.get("position_sizing") if isinstance(decision.get("position_sizing"), dict) else {}
    ohlcv = decision.get("_ohlcv") if isinstance(decision.get("_ohlcv"), dict) else {}
    closes = ohlcv.get("closes", [])
    entry = sizing.get("entry_price") or (closes[-1] if closes else None)
    if not entry:
        raise ValueError("無法取得進場參考價")
    entry = float(entry)
    stop = sizing.get("stop_loss_price") or entry * 0.92
    stop = min(float(stop), entry * 0.999)
    risk_per_share = max(entry - stop, entry * 0.005)
    target = entry + risk_per_share * max(float(profile["min_reward_risk"]), 2.0)
    requested_pct = sizing.get("position_pct_of_portfolio")
    if requested_pct is None:
        requested_pct = {
            "TINY": 2.0,
            "SMALL": 5.0,
            "NORMAL": 10.0,
            "AGGRESSIVE": 15.0,
        }.get(decision.get("position_size_level"), profile["max_position_pct"])
    quality = decision.get("data_quality") or {}
    return {
        "symbol": decision.get("symbol"),
        "decision": decision.get("decision"),
        "confidence_score": scorecard["confidence_score"],
        "is_demo": bool(quality.get("is_demo", False)),
        "data_quality_status": quality.get("data_status", "UNKNOWN"),
        "entry_price": round(entry, 4),
        "stop_price": round(stop, 4),
        "target_price": round(target, 4),
        "requested_position_pct": float(requested_pct or 0),
        "sector": decision.get("sector_name"),
    }


def evaluate(
    symbol: str,
    ohlcv_fn,
    *,
    portfolio: dict | None = None,
    cost: float = 0.0,
    holding_days: int = 0,
    db_path: str | None = None,
) -> dict:
    sym = _symbol(symbol)
    if not callable(ohlcv_fn):
        raise ValueError("ohlcv_fn 必須可呼叫")
    ohlcv = ohlcv_fn(sym)
    if not isinstance(ohlcv, dict) or not ohlcv.get("closes"):
        raise ValueError(f"無法取得 {sym} 的有效行情")
    decision = top_tier.run_top_tier_decision(
        sym,
        lambda requested: ohlcv if requested.upper() == sym else ohlcv_fn(requested),
        cost=float(cost or 0),
        holding_days=max(0, int(holding_days or 0)),
    )
    decision["_ohlcv"] = ohlcv
    evidence = build_decision_evidence(sym, ohlcv, decision)
    history = signal_history.get_confidence_stats(decision.get("decision", "WATCH"))
    scorecard_input = {
        **decision,
        "is_demo": bool(ohlcv.get("is_demo", False)),
        "evidence": evidence,
    }
    scorecard = build_scorecard(scorecard_input, history)
    profile = get_profile(db_path)
    proposal = _proposal_from_decision(decision, scorecard, profile)
    risk_gate = assess_trade(proposal, profile, portfolio)
    decision.pop("_ohlcv", None)
    return {
        "ok": True,
        "symbol": sym,
        "decision": decision,
        "scorecard": scorecard,
        "risk_gate": risk_gate,
        "proposal": proposal,
        "profile": profile,
        "paper_trade_ready": bool(scorecard["actionable"] and risk_gate["allowed"]),
    }


def open_paper_from_signal(
    symbol: str,
    ohlcv_fn,
    *,
    portfolio: dict | None = None,
    client_order_id: str | None = None,
    db_path: str | None = None,
) -> dict:
    if client_order_id:
        existing = paper_trading.get_trade_by_client_order_id(
            client_order_id, db_path=db_path
        )
        if existing:
            existing["deduplicated"] = True
            return {"ok": True, "evaluation": None, "trade": existing}
    result = evaluate(symbol, ohlcv_fn, portfolio=portfolio, db_path=db_path)
    if not result["paper_trade_ready"]:
        blockers = (
            result["scorecard"].get("blockers", [])
            + result["risk_gate"].get("blockers", [])
        )
        raise ValueError("目前不可建立模擬交易：" + "；".join(blockers[:8]))
    decision = result["decision"]
    proposal = result["proposal"]
    record = signal_history.record_signal(
        {
            "symbol": result["symbol"],
            "decision": decision.get("decision"),
            "top_tier_score": decision.get("top_tier_score"),
            "market_regime": decision.get("market_regime"),
            "market_score": decision.get("market_score"),
            "data_quality_status": (decision.get("data_quality") or {}).get("data_status"),
            "chase_risk_score": (decision.get("chase_risk") or {}).get("score"),
            "kill_signal_triggered": (decision.get("kill_signal") or {}).get("triggered", False),
            "position_size_level": decision.get("position_size_level"),
            "entry_price": proposal["entry_price"],
            "is_demo": proposal["is_demo"],
        }
    )
    if not record.get("ok"):
        raise RuntimeError(f"無法記錄訊號：{record.get('error', 'unknown error')}")
    trade_payload = {
        **proposal,
        "qty": result["risk_gate"].get("max_shares"),
        "confidence_score": result["scorecard"]["confidence_score"],
        "evidence_coverage": result["scorecard"]["evidence_coverage"],
        "scorecard_id": result["scorecard"]["scorecard_id"],
        "signal_record_id": record["id"],
        "risk_profile": result["profile"]["preset"],
        "client_order_id": client_order_id,
        "meta": {
            "confidence_grade": result["scorecard"]["confidence_grade"],
            "market_regime": decision.get("market_regime"),
            "main_reason": decision.get("main_reason"),
        },
    }
    trade = paper_trading.open_trade(trade_payload, result["risk_gate"], db_path=db_path)
    return {"ok": True, "evaluation": result, "trade": trade}


def sync_paper_outcome(trade: dict) -> dict:
    signal_id = trade.get("signal_record_id")
    if not signal_id:
        return {"ok": False, "error": "trade has no signal_record_id"}
    return signal_history.update_paper_outcome(
        int(signal_id),
        {
            "paper_trade_id": trade.get("id"),
            "paper_return_pct": trade.get("return_pct"),
            "paper_r_multiple": trade.get("r_multiple"),
            "paper_exit_reason": trade.get("exit_reason"),
            "paper_closed_at": trade.get("closed_at"),
            "exit_price": trade.get("exit_price"),
        },
    )
