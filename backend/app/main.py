"""FastAPI application entrypoint."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import analysis, candles, crypto, logs, ollama, settings as settings_api, signals, strategies, symbols
from app.config import get_settings
from app.db import init_db
from app.scheduler import build_scheduler

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("chart_volume")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    settings = get_settings()
    settings.resolved_token()  # ensure a token exists; never logged (it's the only auth secret)
    logger.info("Chart-Volume backend up on %s:%s", settings.host, settings.port)
    scheduler = build_scheduler()
    scheduler.start()
    app.state.scheduler = scheduler
    logger.info("Scheduler started with jobs: %s", [j.id for j in scheduler.get_jobs()])
    try:
        yield
    finally:
        scheduler.shutdown(wait=False)


app = FastAPI(title="Chart-Volume", lifespan=lifespan)

# Loopback-bound + token-gated. CORS is scoped to only the two origins that
# ever actually need it -- the Vite dev server, and Electron's packaged
# renderer (loaded via loadFile(), whose fetches carry Origin: null) -- not a
# wildcard, so a random webpage the user has open elsewhere can't read
# responses from this API even if it somehow obtained the bearer token.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173", "null"],
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Content-Type", "Authorization"],
)

app.include_router(symbols.router)
app.include_router(candles.router)
app.include_router(analysis.router)
app.include_router(settings_api.router)
app.include_router(signals.router)
app.include_router(ollama.router)
app.include_router(strategies.router)
app.include_router(crypto.router)
app.include_router(logs.router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
