"""Run the Railway web process and intelligence worker in one container.

Railway attaches the persistent SQLite volume to the web service.  Keeping both
processes in that service guarantees that they read and write the same database
without copying credentials or data between services.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Mapping


_FALSE_VALUES = {"0", "false", "no", "off"}


def worker_enabled(env: Mapping[str, str] | None = None) -> bool:
    """Return whether Railway should supervise the intelligence worker."""
    values = os.environ if env is None else env
    raw = str(values.get("RAILWAY_INTELLIGENCE_WORKER_ENABLE", "")).strip()
    if raw:
        return raw.lower() not in _FALSE_VALUES
    # A repository can remain connected to more than one Railway project.
    # Auto-start only beside the persistent user database, so an old stateless
    # deployment cannot create a duplicate scheduler or spend provider quota.
    return bool(str(values.get("RAILWAY_VOLUME_MOUNT_PATH", "")).strip())


def runtime_env(env: Mapping[str, str] | None = None) -> dict[str, str]:
    """Build a child environment with production safety invariants."""
    child = dict(os.environ if env is None else env)
    # Deployment never grants live-trading authority.  Even a stale Railway
    # variable cannot switch this release away from Alpaca paper trading.
    child["ALPACA_PAPER"] = "true"
    child.pop("ENABLE_LIVE_TRADING", None)
    if worker_enabled(child):
        child["MARKET_INTELLIGENCE_ENABLE"] = "true"
    return child


def gunicorn_command(env: Mapping[str, str]) -> list[str]:
    port = str(env.get("PORT", "5000")).strip() or "5000"
    return [
        sys.executable,
        "-m",
        "gunicorn",
        "--workers",
        "1",
        "--threads",
        "8",
        "--timeout",
        "120",
        "--bind",
        f"0.0.0.0:{port}",
        "app:app",
    ]


def intelligence_command() -> list[str]:
    return [sys.executable, "-m", "market_intelligence.worker", "daemon"]


def _spawn(label: str, command: list[str], env: Mapping[str, str]):
    print(f"[RAILWAY] starting {label}: {' '.join(command)}", flush=True)
    return subprocess.Popen(command, env=dict(env))  # noqa: S603


def _stop_process(label: str, process: subprocess.Popen | None) -> None:
    if process is None or process.poll() is not None:
        return
    print(f"[RAILWAY] stopping {label}", flush=True)
    process.terminate()
    try:
        process.wait(timeout=20)
    except subprocess.TimeoutExpired:
        print(f"[RAILWAY] killing unresponsive {label}", flush=True)
        process.kill()
        process.wait(timeout=5)


def main() -> int:
    env = runtime_env()
    stop = threading.Event()

    def _request_stop(*_args) -> None:
        stop.set()

    signal.signal(signal.SIGTERM, _request_stop)
    signal.signal(signal.SIGINT, _request_stop)

    web: subprocess.Popen | None = None
    intelligence: subprocess.Popen | None = None
    worker_failures = 0
    try:
        web = _spawn("web", gunicorn_command(env), env)
        if worker_enabled(env):
            intelligence = _spawn("intelligence worker", intelligence_command(), env)
        else:
            print("[RAILWAY] intelligence worker disabled", flush=True)

        while not stop.wait(1):
            web_code = web.poll()
            if web_code is not None:
                print(f"[RAILWAY] web exited with code {web_code}", flush=True)
                return web_code if web_code != 0 else 1

            if intelligence is None or intelligence.poll() is None:
                continue

            worker_code = intelligence.returncode
            worker_failures += 1
            delay = min(60, 2 ** min(worker_failures, 5))
            print(
                f"[RAILWAY] intelligence worker exited with code {worker_code}; "
                f"restarting in {delay}s",
                flush=True,
            )
            if stop.wait(delay):
                break
            intelligence = _spawn(
                "intelligence worker", intelligence_command(), env
            )
        return 0
    finally:
        _stop_process("intelligence worker", intelligence)
        _stop_process("web", web)


if __name__ == "__main__":
    raise SystemExit(main())
