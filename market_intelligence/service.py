"""Orchestrate collection, grounded analysis, persistence, and delivery."""

from __future__ import annotations

import uuid
from copy import deepcopy
from datetime import datetime, timezone

from .analysis import aggregate_catalysts, analyze_articles, build_report
from .config import IntelligenceConfig
from .delivery import queue_report
from .sources import collect_news
from .storage import (
    acquire_lease,
    article_analyses,
    finish_run,
    release_lease,
    save_catalyst,
    start_run,
    upsert_articles,
)
from .user_context import extract_user_context, load_kv
from database_backup import ensure_daily_backup


def _breaking_report(report: dict, threshold: int) -> dict | None:
    qualifying = [
        item
        for item in report.get("top_events") or []
        if item.get("is_new")
        and int(item.get("importance") or 0) >= threshold
        and int(item.get("confidence") or 0) >= 60
        and int(item.get("relevance") or 0) >= 80
        and bool(item.get("affected_symbols"))
    ]
    if not qualifying:
        return None
    result = deepcopy(report)
    result["top_events"] = qualifying[:6]
    result["summary"]["high_importance_count"] = len(qualifying)
    return result


def run_intelligence(
    run_type: str = "manual",
    *,
    user_data: dict | None = None,
    dispatch: bool = False,
    force: bool = False,
    config: IntelligenceConfig | None = None,
    session=None,
    ai_client=None,
) -> dict:
    """Run one complete intelligence cycle.

    Scheduled runs respect ``MARKET_INTELLIGENCE_ENABLE``. Authenticated manual
    runs may pass ``force=True`` so setup can be tested before enabling a worker.
    """
    config = config or IntelligenceConfig.from_env()
    run_type = str(run_type or "manual").lower().strip()
    if run_type not in {"daily", "breaking", "manual"}:
        raise ValueError("run_type 必須是 daily、breaking 或 manual")
    if not config.enabled and not force:
        return {
            "ok": False,
            "status": "SKIPPED",
            "error": "MARKET_INTELLIGENCE_ENABLE 尚未啟用",
        }

    owner = uuid.uuid4().hex
    if not acquire_lease(config.db_path, "market_intelligence", owner, seconds=900):
        return {"ok": False, "status": "BUSY", "error": "已有情報任務正在執行"}

    run_id = ""
    fetched_count = analyzed_count = 0
    try:
        run_id = start_run(config.db_path, run_type)
        raw_user_data = user_data if user_data is not None else load_kv(config.db_path)
        user_context = extract_user_context(
            raw_user_data, max_symbols=config.max_symbols
        )
        articles, provider_health = collect_news(
            user_context["symbols"],
            config,
            session=session,
            include_slow_sources=run_type != "breaking",
        )
        fetched_count = len(articles)
        prior_analyses = article_analyses(
            config.db_path, [article["dedupe_key"] for article in articles]
        )
        # Rules are deterministic and cheap. Only genuinely new items may call
        # Claude; unchanged stories retain their earlier validated result.
        analyzed = analyze_articles(
            articles,
            holding_symbols=user_context["holding_symbols"],
            watchlist_symbols=user_context["watchlist"],
            model=config.anthropic_model,
            client=False,
        )
        new_articles = [
            article
            for article in analyzed
            if article["dedupe_key"] not in prior_analyses
        ]
        analyze_articles(
            new_articles,
            holding_symbols=user_context["holding_symbols"],
            watchlist_symbols=user_context["watchlist"],
            model=config.anthropic_model,
            client=ai_client,
        )
        for article in analyzed:
            if article["dedupe_key"] in prior_analyses:
                article["analysis"] = prior_analyses[article["dedupe_key"]]
        analyzed_count = len(analyzed)
        new_keys = upsert_articles(config.db_path, analyzed)
        catalysts = aggregate_catalysts(analyzed, user_context["symbols"])
        for catalyst in catalysts:
            save_catalyst(config.db_path, catalyst, ttl_hours=48)

        report = build_report(
            run_type=run_type,
            articles=analyzed,
            catalysts=catalysts,
            user_context=user_context,
            provider_health=provider_health,
            new_keys=new_keys,
            generated_at=datetime.now(timezone.utc).isoformat(),
        )
        report["portfolio"] = {
            "holding_symbols": user_context["holding_symbols"],
            "watchlist": user_context["watchlist"],
        }
        report["_run"] = {"id": run_id, "status": "SUCCESS", "run_type": run_type}

        notification_report = report
        if run_type == "breaking":
            notification_report = _breaking_report(report, config.breaking_importance)
        if dispatch and notification_report:
            report["delivery"] = queue_report(
                notification_report,
                user_context=user_context,
                config=config,
            )
        elif dispatch:
            report["delivery"] = {
                "queued": [],
                "skipped": [{"reason": "沒有符合門檻的新持股事件"}],
            }

        finish_run(
            config.db_path,
            run_id,
            status="SUCCESS",
            fetched_count=fetched_count,
            new_count=len(new_keys),
            analyzed_count=analyzed_count,
            report=report,
        )
        if run_type == "daily":
            try:
                report["backup"] = ensure_daily_backup(config.db_path)
                # Persist backup diagnostics without changing the run outcome.
                finish_run(
                    config.db_path,
                    run_id,
                    status="SUCCESS",
                    fetched_count=fetched_count,
                    new_count=len(new_keys),
                    analyzed_count=analyzed_count,
                    report=report,
                )
            except Exception as backup_exc:  # noqa: BLE001 - intelligence remains usable
                report["backup"] = {
                    "ok": False,
                    "status": "FAILED",
                    "error": str(backup_exc)[:300],
                }
        return report
    except Exception as exc:  # noqa: BLE001 - run boundary records any provider failure
        if run_id:
            finish_run(
                config.db_path,
                run_id,
                status="FAILED",
                fetched_count=fetched_count,
                analyzed_count=analyzed_count,
                error=str(exc),
            )
        return {
            "ok": False,
            "status": "FAILED",
            "error": str(exc)[:500],
            "_run": {"id": run_id or None, "status": "FAILED", "run_type": run_type},
        }
    finally:
        release_lease(config.db_path, "market_intelligence", owner)
