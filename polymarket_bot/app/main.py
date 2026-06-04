"""FastAPI application entrypoint + dashboard API.

Run with:  uvicorn app.main:app --host 0.0.0.0 --port 8000
or:        python -m app.main
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import repository as repo
from .config import get_settings
from .db import close_pool, init_pool
from .orchestrator import Orchestrator

settings = get_settings()
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
log = logging.getLogger("polymarket_bot")

STATIC_DIR = Path(__file__).resolve().parent / "static"

orchestrator: Orchestrator | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global orchestrator
    # Hard safety assertion: refuse to boot if someone flips the live flag.
    if settings.live_trading_enabled:
        raise RuntimeError("LIVE_TRADING_ENABLED=true is not permitted in v1 (paper-only).")

    await init_pool()
    orchestrator = Orchestrator()
    await orchestrator.start()
    log.info("Startup complete")
    try:
        yield
    finally:
        if orchestrator:
            await orchestrator.stop()
        await close_pool()
        log.info("Shutdown complete")


app = FastAPI(title="Polymarket Paper Trading Bot", version="0.1.0", lifespan=lifespan)


# --------------------------------------------------------------------------- #
# Dashboard API
# --------------------------------------------------------------------------- #
@app.get("/api/health")
async def health():
    return {"status": "ok", "live_trading_enabled": settings.live_trading_enabled,
            "open_positions": orchestrator.trader.open_count if orchestrator else 0}


@app.get("/api/markets")
async def api_markets():
    markets = await repo.list_active_markets()
    # enrich with live top-of-book from the in-memory store
    out = []
    for m in markets:
        book = orchestrator.store.get(m["yes_token_id"]) if orchestrator and m.get("yes_token_id") else None
        out.append({
            "condition_id": m["condition_id"],
            "question": m["question"],
            "yes_token_id": m.get("yes_token_id"),
            "best_bid": book.best_bid if book else None,
            "best_ask": book.best_ask if book else None,
            "mid_price": book.mid_price if book else None,
            "last_trade": book.last_trade if book else None,
        })
    return out


@app.get("/api/news")
async def api_news(limit: int = 20):
    return await repo.recent_news(limit)


@app.get("/api/signals")
async def api_signals(limit: int = 20):
    return await repo.recent_signals(limit)


@app.get("/api/trades")
async def api_trades():
    return await repo.open_trades()


@app.get("/api/stats")
async def api_stats():
    stats = await repo.performance_stats()
    stats["daily_pnl"] = await repo.daily_realized_pnl()
    stats["starting_cash"] = settings.paper_starting_cash
    return stats


@app.get("/")
async def dashboard():
    index = STATIC_DIR / "dashboard.html"
    if index.exists():
        return FileResponse(index)
    return JSONResponse({"message": "dashboard.html not found"}, status_code=404)


if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def run() -> None:
    import uvicorn
    uvicorn.run("app.main:app", host=settings.host, port=settings.port,
                log_level=settings.log_level.lower())


if __name__ == "__main__":
    run()
