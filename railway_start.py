"""Run RocketStock web and optional intelligence worker in one container.

The launcher remains Railway-compatible but is intentionally platform-neutral so
Zeabur and other container platforms can use the same production entrypoint.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
from collections.abc import Mapping

_FALSE_VALUES = {"0", "false", "no", "off"}


def worker_enabled(env: Mapping[str, str] | None = None) -> bool:
    values = os.environ if env is None else env
    raw = str(
        values.get("MARKET_INTELLIGENCE_WORKER_ENABLE")
        or values.get("RAILWAY_INTELLIGENCE_WORKER_ENABLE")
        or ""
    ).strip()
    if raw:
        return raw.lower() not in _FALSE_VALUES
    # Auto-start only when durable storage is explicitly present. This avoids
    # duplicate schedulers on stale/stateless deployments.
    return bool(
        str(values.get("RAILWAY_VOLUME_MOUNT_PATH", "")).strip()
        or str(values.get("USER_DATA_DB", "")).strip()
        or str(values.get("DATABASE_URL", "")).strip()
    )


def runtime_env(env: Mapping[str, str] | None = None) -> dict[str, str]:
    child = dict(os.environ if env is None else env)
    child["ALPACA_PAPER"] = "true"
    child.pop("ENABLE_LIVE_TRADING", None)
    child.setdefault("ROCKETSTOCK_ENGINE_VERSION", "institutional-core-v4")
    if worker_enabled(child):
        child["MARKET_INTELLIGENCE_ENABLE"] = "true"
    return child


def gunicorn_command(env: Mapping[str, str]) -> list[str]:
    port = str(env.get("PORT", "5000")).strip() or "5000"
    return [
        sys.executable, "-m", "gunicorn",
        "--workers", "1", "--threads", "8", "--timeout", "120",
        "--bind", f"0.0.0.0:{port}",
        "production_app:app",
    ]


def intelligence_command() -> list[str]:
    return [sys.executable, "-m", "market_intelligence.worker", "daemon"]


def _spawn(label: str, command: list[str], env: Mapping[str, str]):
    print(f"[PRODUCTION] starting {label}: {' '.join(command)}", flush=True)
    return subprocess.Popen(command, env=dict(env))  # noqa: S603


def _stop_process(label: str, process: subprocess.Popen | None) -> None:
    if process is None or process.poll() is not None:
        return
    print(f"[PRODUCTION] stopping {label}", flush=True)
    process.terminate()
    try:
        process.wait(timeout=20)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def main() -> int:
    env = runtime_env()
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    web = None
    intelligence = None
    worker_failures = 0
    try:
        web = _spawn("web", gunicorn_command(env), env)
        if worker_enabled(env):
            intelligence = _spawn("intelligence worker", intelligence_command(), env)
        else:
            print("[PRODUCTION] intelligence worker disabled", flush=True)
        while not stop.wait(1):
            web_code = web.poll()
            if web_code is not None:
                print(f"[PRODUCTION] web exited with code {web_code}", flush=True)
                return web_code if web_code != 0 else 1
            if intelligence is None or intelligence.poll() is None:
                continue
            worker_failures += 1
            delay = min(60, 2 ** min(worker_failures, 5))
            print(f"[PRODUCTION] worker exited; restarting in {delay}s", flush=True)
            if stop.wait(delay):
                break
            intelligence = _spawn("intelligence worker", intelligence_command(), env)
        return 0
    finally:
        _stop_process("intelligence worker", intelligence)
        _stop_process("web", web)


if __name__ == "__main__":
    raise SystemExit(main())
