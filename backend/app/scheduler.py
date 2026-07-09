"""APScheduler jobs: crawl + analyse VN30 + watchlist on VN market cadence.

- ~11:35  morning half-session closed  -> half_session batch
- ~15:05  afternoon session closed     -> half_session batch
- ~15:15  daily bar finalised          -> daily batch

Per-ticker failures are isolated so one bad symbol never aborts the batch.
"""

from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlmodel import Session, select

from app.config import TIMEZONE
from app.db import get_engine
from app.models import Symbol, Timeframe
from app.services import ingest
from app.services.analysis import run_analysis

logger = logging.getLogger("chart_volume.scheduler")

_WEEKDAYS = "mon-fri"


def _tracked_tickers(session: Session) -> list[str]:
    rows = session.exec(
        select(Symbol).where((Symbol.is_vn30 == True) | (Symbol.is_watchlist == True))  # noqa: E712
    ).all()
    return [s.ticker for s in rows]


def run_batch(session: Session, timeframe: str, use_ai: bool = True) -> int:
    """Ingest + analyse every tracked ticker. Returns how many succeeded."""
    ok = 0
    for ticker in _tracked_tickers(session):
        try:
            if timeframe == Timeframe.DAILY:
                ingest.ingest_daily(session, ticker)
            else:
                ingest.ingest_half_session(session, ticker)
            run_analysis(session, ticker, timeframe, use_ai=use_ai)
            ok += 1
        except Exception as exc:  # noqa: BLE001 - isolate per-ticker failures
            logger.warning("batch %s failed for %s: %s", timeframe, ticker, exc)
    logger.info("batch %s complete: %d ok", timeframe, ok)
    return ok


def _daily_job() -> None:
    with Session(get_engine()) as session:
        run_batch(session, Timeframe.DAILY)


def _half_session_job() -> None:
    with Session(get_engine()) as session:
        run_batch(session, Timeframe.HALF_SESSION)


def build_scheduler() -> BackgroundScheduler:
    scheduler = BackgroundScheduler(timezone=TIMEZONE)
    scheduler.add_job(
        _half_session_job,
        CronTrigger(hour=11, minute=35, day_of_week=_WEEKDAYS),
        id="half_session_morning",
    )
    scheduler.add_job(
        _half_session_job,
        CronTrigger(hour=15, minute=5, day_of_week=_WEEKDAYS),
        id="half_session_afternoon",
    )
    scheduler.add_job(
        _daily_job,
        CronTrigger(hour=15, minute=15, day_of_week=_WEEKDAYS),
        id="daily_close",
    )
    return scheduler
