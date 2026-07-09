"""FastAPI application entrypoint."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import analysis, candles, symbols
from app.config import get_settings
from app.db import init_db
from app.scheduler import build_scheduler

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
logger = logging.getLogger("chart_volume")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    settings = get_settings()
    token = settings.resolved_token()
    logger.info("Chart-Volume backend up on %s:%s", settings.host, settings.port)
    logger.info("DEV API TOKEN: %s", token)
    scheduler = build_scheduler()
    scheduler.start()
    logger.info("Scheduler started with jobs: %s", [j.id for j in scheduler.get_jobs()])
    try:
        yield
    finally:
        scheduler.shutdown(wait=False)


app = FastAPI(title="Chart-Volume", lifespan=lifespan)

# Loopback-bound + token-gated; allow the Vite dev server / Electron renderer.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(symbols.router)
app.include_router(candles.router)
app.include_router(analysis.router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
