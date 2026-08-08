"""Flask blueprint for Scott Evolution v2.

The legacy app injects only its market-data function and notification sender;
all domain logic stays testable without importing the 5k-line Flask module.
"""

from __future__ import annotations

from flask import Blueprint, jsonify, render_template, request

from scott_evolution import notifications, paper_trading, risk_brain
from scott_evolution.service import evaluate, open_paper_from_signal, sync_paper_outcome


def create_evolution_blueprint(ohlcv_fn, notification_sender=None) -> Blueprint:
    bp = Blueprint("evolution", __name__)

    @bp.get("/evolution")
    def evolution_page():
        return render_template("evolution.html")

    @bp.get("/api/evolution/health")
    def health():
        return jsonify(
            ok=True,
            service="scott-evolution-v2",
            notifications=notifications.summary(),
            paper=paper_trading.performance_summary(),
        )

    @bp.route("/api/evolution/risk-profile", methods=["GET", "PUT", "POST"])
    def risk_profile():
        try:
            if request.method == "GET":
                return jsonify(ok=True, profile=risk_brain.get_profile())
            body = request.get_json(silent=True)
            if not isinstance(body, dict):
                return jsonify(ok=False, error="JSON object required"), 400
            return jsonify(ok=True, profile=risk_brain.save_profile(body))
        except ValueError as exc:
            return jsonify(ok=False, error=str(exc)), 400

    @bp.post("/api/evolution/evaluate")
    def evaluate_signal():
        body = request.get_json(silent=True) or {}
        if not isinstance(body, dict):
            return jsonify(ok=False, error="JSON object required"), 400
        try:
            portfolio = body.get("portfolio") if isinstance(body.get("portfolio"), dict) else {}
            result = evaluate(
                body.get("symbol"),
                ohlcv_fn,
                portfolio=portfolio,
                cost=body.get("cost", 0),
                holding_days=body.get("holding_days", 0),
            )
            return jsonify(result)
        except (TypeError, ValueError) as exc:
            return jsonify(ok=False, error=str(exc)), 400
        except Exception as exc:
            return jsonify(ok=False, error=str(exc)), 500

    @bp.post("/api/evolution/paper/open")
    def paper_open():
        body = request.get_json(silent=True) or {}
        if not isinstance(body, dict):
            return jsonify(ok=False, error="JSON object required"), 400
        try:
            portfolio = body.get("portfolio") if isinstance(body.get("portfolio"), dict) else {}
            result = open_paper_from_signal(
                body.get("symbol"),
                ohlcv_fn,
                portfolio=portfolio,
                client_order_id=str(body.get("client_order_id") or "")[:120] or None,
            )
            return jsonify(result)
        except ValueError as exc:
            return jsonify(ok=False, blocked=True, error=str(exc)), 400
        except Exception as exc:
            return jsonify(ok=False, error=str(exc)), 500

    @bp.get("/api/evolution/paper/trades")
    def paper_list():
        try:
            status = request.args.get("status")
            limit = int(request.args.get("limit", 100))
            return jsonify(
                ok=True,
                trades=paper_trading.list_trades(status=status, limit=limit),
                summary=paper_trading.performance_summary(),
            )
        except (TypeError, ValueError) as exc:
            return jsonify(ok=False, error=str(exc)), 400

    @bp.post("/api/evolution/paper/mark")
    def paper_mark():
        body = request.get_json(silent=True) or {}
        if not isinstance(body, dict) or not isinstance(body.get("prices"), dict):
            return jsonify(ok=False, error="prices JSON object required"), 400
        try:
            return jsonify(
                paper_trading.mark_to_market(
                    body["prices"], outcome_hook=sync_paper_outcome
                )
            )
        except ValueError as exc:
            return jsonify(ok=False, error=str(exc)), 400

    @bp.post("/api/evolution/paper/<trade_id>/close")
    def paper_close(trade_id: str):
        body = request.get_json(silent=True) or {}
        if not isinstance(body, dict):
            return jsonify(ok=False, error="JSON object required"), 400
        try:
            trade = paper_trading.close_trade(
                trade_id,
                body.get("exit_price"),
                body.get("reason", "MANUAL"),
                outcome_hook=sync_paper_outcome,
            )
            return jsonify(ok=True, trade=trade)
        except ValueError as exc:
            return jsonify(ok=False, error=str(exc)), 400

    @bp.get("/api/evolution/notifications")
    def notification_list():
        try:
            limit = int(request.args.get("limit", 100))
            return jsonify(
                ok=True,
                notifications=notifications.list_notifications(
                    status=request.args.get("status"), limit=limit
                ),
                summary=notifications.summary(),
            )
        except (TypeError, ValueError) as exc:
            return jsonify(ok=False, error=str(exc)), 400

    @bp.post("/api/evolution/notifications/retry")
    def notification_retry():
        body = request.get_json(silent=True) or {}
        if not isinstance(body, dict):
            return jsonify(ok=False, error="JSON object required"), 400
        changed = notifications.retry(body.get("id"))
        delivered = None
        if notification_sender is not None:
            delivered = notifications.deliver_due(notification_sender)
        return jsonify(ok=True, reset=changed, delivery=delivered)

    @bp.post("/api/evolution/notifications/deliver")
    def notification_deliver():
        if notification_sender is None:
            return jsonify(ok=False, error="notification sender unavailable"), 503
        return jsonify(notifications.deliver_due(notification_sender))

    return bp
