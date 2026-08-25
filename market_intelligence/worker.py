"""Independent Railway worker for scheduled intelligence runs."""

from __future__ import annotations

import argparse
import signal
import threading
from datetime import datetime
from time import monotonic
from zoneinfo import ZoneInfo

from database_backup import ensure_daily_backup

from .config import IntelligenceConfig
from .delivery import deliver_pending
from .service import run_intelligence
from .storage import get_state, set_state


def _run_daily_breakout_watch(config: IntelligenceConfig) -> None:
    """Run the read-only AI/semi breakout watch without risking worker uptime."""
    try:
        from breakout_watch import dispatch_breakout_watch, run_breakout_watch

        result = run_breakout_watch(config=config)
        delivery = dispatch_breakout_watch(result, config=config)
        symbols = [item.get("symbol") for item in result.get("candidates") or []]
        if symbols:
            print(
                f"[BREAKOUT] high-quality candidates={','.join(symbols)} "
                f"delivery={delivery}",
                flush=True,
            )
        else:
            print("[BREAKOUT] no high-quality signal; notification suppressed", flush=True)
    except Exception as exc:  # noqa: BLE001 - breakout watch must fail isolated
        print(f"[BREAKOUT] failed: {str(exc)[:300]}", flush=True)


def run_daemon(config: IntelligenceConfig) -> int:
    if not config.enabled:
        print(
            "[INTELLIGENCE] disabled; set MARKET_INTELLIGENCE_ENABLE=true", flush=True
        )
        return 0
    stop = threading.Event()

    def _stop(*_args):
        stop.set()

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    zone = ZoneInfo(config.timezone)
    next_poll = 0.0
    print(
        f"[INTELLIGENCE] worker started; timezone={config.timezone} "
        f"poll={config.poll_minutes}m daily={config.daily_time}",
        flush=True,
    )
    while not stop.is_set():
        now = datetime.now(zone)
        today = now.date().isoformat()
        hhmm = now.strftime("%H:%M")
        if (
            hhmm >= config.daily_time
            and get_state(config.db_path, "last_daily_date") != today
        ):
            report = run_intelligence("daily", dispatch=True, config=config)
            backup = ensure_daily_backup(config.db_path)
            if report.get("_run", {}).get("status") == "SUCCESS":
                set_state(config.db_path, "last_daily_date", today)
            print(f"[INTELLIGENCE] daily: {report.get('_run', report)}", flush=True)
            print(f"[BACKUP] daily: {backup}", flush=True)

            # Separate, read-only breakout candidate scan.  It deliberately
            # does not share the trading executor and dispatches only when a
            # candidate clears the strict quality threshold.
            _run_daily_breakout_watch(config)

        if monotonic() >= next_poll:
            report = run_intelligence("breaking", dispatch=True, config=config)
            next_poll = monotonic() + config.poll_minutes * 60
            print(f"[INTELLIGENCE] breaking: {report.get('_run', report)}", flush=True)
        try:
            deliver_pending(config.db_path)
        except Exception as exc:  # noqa: BLE001 - keep scheduler alive for retries
            print(f"[INTELLIGENCE] outbox: {str(exc)[:200]}", flush=True)
        stop.wait(30)
    print("[INTELLIGENCE] worker stopped", flush=True)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scott market-intelligence worker")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run", help="run one cycle")
    run.add_argument(
        "--type", choices=("daily", "breaking", "manual"), default="manual"
    )
    run.add_argument("--dispatch", action="store_true")
    run.add_argument("--force", action="store_true")
    sub.add_parser("daemon", help="run the independent scheduler")
    breakout = sub.add_parser("breakout", help="run the AI/semi breakout watch once")
    breakout.add_argument("--dispatch", action="store_true")
    args = parser.parse_args(argv)
    config = IntelligenceConfig.from_env()
    if args.command == "daemon":
        return run_daemon(config)
    if args.command == "breakout":
        from breakout_watch import dispatch_breakout_watch, run_breakout_watch

        result = run_breakout_watch(config=config)
        if args.dispatch:
            result["delivery"] = dispatch_breakout_watch(result, config=config)
        print(result, flush=True)
        return 0
    report = run_intelligence(
        args.type,
        dispatch=args.dispatch,
        force=args.force,
        config=config,
    )
    print(report, flush=True)
    return 0 if report.get("_run", {}).get("status") == "SUCCESS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
