"""FastAPI application entrypoint."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from logging.handlers import RotatingFileHandler

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from sqlmodel import Session

from app.api import (
    analysis,
    candles,
    crypto,
    logs,
    ollama,
    potential_screen,
    settings as settings_api,
    signals,
    strategies,
    symbols,
    trade_history,
)
from app.config import get_settings, log_file_path
from app.db import get_engine, init_db
from app.scheduler import build_scheduler
from app.services import activity_log

# Console handler (dev terminal / Electron's stdout capture) + a rotating
# file next to the DB, so "Download log" in the UI has real content to
# export even in a packaged build where there's no terminal to read from.
_LOG_FORMAT = "%(asctime)s %(name)s %(levelname)s %(message)s"
_root_logger = logging.getLogger()
_root_logger.setLevel(logging.INFO)
_console_handler = logging.StreamHandler()
_console_handler.setFormatter(logging.Formatter(_LOG_FORMAT))
_root_logger.addHandler(_console_handler)
_file_handler = RotatingFileHandler(log_file_path(), maxBytes=5_000_000, backupCount=2, encoding="utf-8")
_file_handler.setFormatter(logging.Formatter(_LOG_FORMAT))
_root_logger.addHandler(_file_handler)
logger = logging.getLogger("chart_volume")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    with Session(get_engine()) as session:
        fixed = activity_log.mark_stale_running_as_interrupted(session)
        if fixed:
            logger.info("marked %d stale 'running' activity log row(s) as interrupted", fixed)
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
app.include_router(potential_screen.router)
app.include_router(trade_history.router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
