"""Flask blueprint for the market-intelligence dashboard and API."""

from __future__ import annotations

import threading

from flask import Blueprint, jsonify, render_template, request

from .config import IntelligenceConfig
from .delivery import delivery_status
from .service import run_intelligence
from .storage import (
    get_symbol_catalyst,
    latest_report,
    list_catalysts,
    recent_articles,
    status,
)
from .user_context import extract_user_context


def create_blueprint(*, db_path: str, load_user_data, rate_limit) -> Blueprint:
    bp = Blueprint("market_intelligence", __name__)

    @bp.get("/intelligence")
    def dashboard():
        return render_template("intelligence.html")

    @bp.get("/api/intelligence/status")
    def api_status():
        config = IntelligenceConfig.from_env(db_path=db_path)
        context = extract_user_context(load_user_data(), max_symbols=config.max_symbols)
        return jsonify(
            ok=True,
            config=config.public_status(),
            storage=status(db_path),
            delivery=delivery_status(db_path),
            portfolio={
                "holdings": context["holding_symbols"],
                "watchlist": context["watchlist"],
            },
        )

    @bp.get("/api/intelligence/latest")
    def api_latest():
        return jsonify(ok=True, report=latest_report(db_path))

    @bp.get("/api/intelligence/events")
    def api_events():
        try:
            limit = int(request.args.get("limit", 50))
        except ValueError:
            limit = 50
        articles = recent_articles(db_path, limit=limit)
        for article in articles:
            article.pop("raw", None)
        return jsonify(ok=True, events=articles)

    @bp.get("/api/intelligence/catalysts")
    def api_catalysts():
        return jsonify(ok=True, catalysts=list_catalysts(db_path, limit=100))

    @bp.get("/api/intelligence/catalyst/<symbol>")
    def api_symbol_catalyst(symbol: str):
        item = get_symbol_catalyst(db_path, symbol)
        return jsonify(ok=bool(item), catalyst=item)

    @bp.post("/api/intelligence/run")
    def api_run():
        limited = rate_limit("market_intelligence_run", 3, 300)
        if limited is not None:
            return limited
        body = request.get_json(silent=True) or {}
        run_type = str(body.get("type") or "manual").lower()
        if run_type not in {"manual", "daily", "breaking"}:
            return jsonify(ok=False, error="type 必須是 manual、daily 或 breaking"), 400
        dispatch = bool(body.get("dispatch", False))
        config = IntelligenceConfig.from_env(db_path=db_path)
        user_data = load_user_data()

        def _run():
            run_intelligence(
                run_type,
                user_data=user_data,
                dispatch=dispatch,
                force=True,
                config=config,
            )

        threading.Thread(
            target=_run, daemon=True, name="manual-intelligence-run"
        ).start()
        return jsonify(
            ok=True, status="STARTED", message="市場情報更新已在背景啟動"
        ), 202

    return bp
