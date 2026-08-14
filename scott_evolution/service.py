"""Application service orchestrating decision, scorecard, risk and paper loop."""

from __future__ import annotations

import re
from datetime import datetime, timezone

import signal_confidence_engine as signal_history
import top_tier_decision_engine as top_tier

from . import paper_trading
from .evidence import build_decision_evidence
from .risk_brain import assess_trade, get_profile
from .scorecard import build_scorecard


_SYMBOL_RE = re.compile(r"[A-Z0-9.\-]{1,20}")


def _round_trip_cost_pct(symbol: str) -> float:
    try:
        from trade_cost import default_params

        params = default_params(symbol)
        return round((
            float(params.get("slippage_pct", 0.001)) * 2
            + float(params.get("commission_buy", 0))
            + float(params.get("commission_sell", 0))
            + float(params.get("transaction_tax", 0))
        ) * 100, 4)
    except Exception:
        return 0.2


def _latest_completed_bar(ohlcv: dict) -> dict | None:
    closes = ohlcv.get("closes") or []
    if not closes:
        return None
    index = len(closes) - 1
    dates = ohlcv.get("dates") or []
    day = str(dates[index])[:10] if index < len(dates) else ""
    if not day:
        timestamps = ohlcv.get("timestamps") or []
        if index < len(timestamps):
            try:
                day = datetime.fromtimestamp(
                    float(timestamps[index]), tz=timezone.utc
                ).date().isoformat()
            except (TypeError, ValueError, OSError):
                day = ""
    if not day:
        return None

    def value(name: str, fallback):
        values = ohlcv.get(name) or []
        return values[index] if index < len(values) and values[index] else fallback

    close = float(closes[index])
    return {
        "date": day,
        "open": float(value("opens", close)),
        "high": float(value("highs", close)),
        "low": float(value("lows", close)),
        "close": close,
    }


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
    quote_fn=None,
    flow_fn=None,
) -> dict:
    sym = _symbol(symbol)
    if not callable(ohlcv_fn):
        raise ValueError("ohlcv_fn 必須可呼叫")
    ohlcv = ohlcv_fn(sym)
    if not isinstance(ohlcv, dict) or not ohlcv.get("closes"):
        raise ValueError(f"無法取得 {sym} 的有效行情")
    paper_sync = None
    latest_bar = _latest_completed_bar(ohlcv)
    if latest_bar:
        try:
            sync_result = paper_trading.mark_to_market(
                {sym: latest_bar},
                outcome_hook=sync_paper_outcome,
                db_path=db_path,
            )
            paper_sync = {
                "updated_count": sync_result.get("updated_count", 0),
                "closed_count": sync_result.get("closed_count", 0),
            }
        except Exception as exc:
            paper_sync = {"error": str(exc)[:200]}
    quote_snapshot = None
    if callable(quote_fn):
        try:
            quote_snapshot = quote_fn(sym)
        except Exception:
            quote_snapshot = None
    flow_snapshot = None
    if callable(flow_fn):
        try:
            flow_snapshot = flow_fn(sym)
        except Exception:
            flow_snapshot = None
    decision = top_tier.run_top_tier_decision(
        sym,
        lambda requested: ohlcv if requested.upper() == sym else ohlcv_fn(requested),
        cost=float(cost or 0),
        holding_days=max(0, int(holding_days or 0)),
        quote_snapshot=quote_snapshot,
        flow_snapshot=flow_snapshot,
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
        "paper_sync": paper_sync,
    }


def open_paper_from_signal(
    symbol: str,
    ohlcv_fn,
    *,
    portfolio: dict | None = None,
    client_order_id: str | None = None,
    db_path: str | None = None,
    quote_fn=None,
    flow_fn=None,
) -> dict:
    if client_order_id:
        existing = paper_trading.get_trade_by_client_order_id(
            client_order_id, db_path=db_path
        )
        if existing:
            existing["deduplicated"] = True
            return {"ok": True, "evaluation": None, "trade": existing}
    result = evaluate(
        symbol,
        ohlcv_fn,
        portfolio=portfolio,
        db_path=db_path,
        quote_fn=quote_fn,
        flow_fn=flow_fn,
    )
    if not result["paper_trade_ready"]:
        blockers = (
            result["scorecard"].get("blockers", [])
            + result["risk_gate"].get("blockers", [])
        )
        raise ValueError("目前不可建立模擬交易：" + "；".join(blockers[:8]))
    decision = result["decision"]
    proposal = result["proposal"]
    quote = decision.get("quote_snapshot") or {}
    calibration = decision.get("signal_calibration") or {}
    readiness = decision.get("institutional_readiness") or {}
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
            "prediction_probability": (
                float(calibration.get("probability_5d_pct")) / 100
                if calibration.get("probability_5d_pct") is not None
                else None
            ),
            "quote_source": quote.get("source"),
            "quote_timestamp": quote.get("timestamp"),
            "quote_age_seconds": quote.get("age_seconds"),
            "calibration_regime": decision.get("market_regime"),
            "data_snapshot": {
                "readiness": readiness.get("status"),
                "quote_feed_scope": quote.get("feed_scope"),
                "ohlcv_source": (decision.get("data_quality") or {}).get("source"),
                "last_bar": (decision.get("data_quality") or {}).get("last_date"),
                "scorecard_id": result["scorecard"].get("scorecard_id"),
            },
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
        "fill_model": "NEXT_OPEN",
        "round_trip_cost_pct": _round_trip_cost_pct(result["symbol"]),
        "meta": {
            "confidence_grade": result["scorecard"]["confidence_grade"],
            "market_regime": decision.get("market_regime"),
            "main_reason": decision.get("main_reason"),
            "signal_date": (decision.get("data_quality") or {}).get("last_date"),
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
