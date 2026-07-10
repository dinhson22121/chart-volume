"""APScheduler jobs: crawl + analyse VN30 + watchlist on VN market cadence.

Default cadence (user-configurable via Settings, HH:MM, Asia/Ho_Chi_Minh):
- 11:35  morning half-session closed  -> half_session batch
- 15:05  afternoon session closed     -> half_session batch
- 15:15  daily bar finalised          -> daily batch

The whole scheduler can be disabled from Settings. Per-ticker failures are
isolated so one bad symbol never aborts the batch. ``reschedule`` lets the
Settings API apply changes (enable/disable, times) without restarting the app.
"""

from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlmodel import Session, select

from app.config import TIMEZONE
from app.db import get_engine
from app.models import AssetClass, Symbol, Timeframe
from app.services import crypto_screener, ingest, settings_service
from app.services.analysis import run_analysis

logger = logging.getLogger("chart_volume.scheduler")

_WEEKDAYS = "mon-fri"

_STOCK_JOB_IDS = ("half_session_morning", "half_session_afternoon", "daily_close")
_SCREENER_JOB_ID = "crypto_screener_scan"
_CRYPTO_JOB_ID = "crypto_analysis_refresh"
_JOB_IDS = _STOCK_JOB_IDS + (_SCREENER_JOB_ID, _CRYPTO_JOB_ID)

# Crypto has no morning/afternoon session split -- one job sweeps all 3
# timeframes on its own interval, unlike stocks (one job per timeframe/time).
_CRYPTO_TIMEFRAMES = (Timeframe.HOUR_1, Timeframe.HOUR_4, Timeframe.DAILY)

# Screener scan_interval setting -> IntervalTrigger kwargs.
_INTERVAL_TRIGGER_KWARGS = {
    "10m": {"minutes": 10},
    "30m": {"minutes": 30},
    "1h": {"hours": 1},
    "4h": {"hours": 4},
    "12h": {"hours": 12},
    "1d": {"days": 1},
}


def _tracked_symbols(session: Session, asset_class: str) -> list[Symbol]:
    return session.exec(
        select(Symbol).where(
            (Symbol.is_vn30 == True) | (Symbol.is_watchlist == True),  # noqa: E712
            Symbol.asset_class == asset_class,
        )
    ).all()


def run_batch(session: Session, timeframe: str, use_ai: bool = True) -> int:
    """Ingest + analyse every tracked STOCK ticker. Returns how many succeeded."""
    ok = 0
    for symbol in _tracked_symbols(session, AssetClass.STOCK):
        ticker = symbol.ticker
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


def run_crypto_batch(session: Session, use_ai: bool = True) -> int:
    """Ingest + analyse every tracked CRYPTO ticker across all 3 timeframes.

    Previously crypto tickers fell through run_batch() (which only knows how
    to ingest stocks), so a promoted coin never got re-analysed unless the
    user manually hit "Cập nhật" for it. This gives crypto the same kind of
    scheduled refresh stocks already had.
    """
    exchanges = settings_service.get_crypto_exchanges(session)
    ok = 0
    for symbol in _tracked_symbols(session, AssetClass.CRYPTO):
        for timeframe in _CRYPTO_TIMEFRAMES:
            try:
                ingest.ingest_crypto(
                    session, symbol.ticker, timeframe,
                    exchange_symbol=symbol.display_symbol, exchanges=exchanges, symbol=symbol,
                )
                run_analysis(session, symbol.ticker, timeframe, use_ai=use_ai)
                ok += 1
            except Exception as exc:  # noqa: BLE001 - isolate per-ticker/timeframe failures
                logger.warning("crypto batch %s/%s failed: %s", symbol.ticker, timeframe, exc)
    logger.info("crypto batch complete: %d ok", ok)
    return ok


def _daily_job() -> None:
    with Session(get_engine()) as session:
        run_batch(session, Timeframe.DAILY)


def _half_session_job() -> None:
    with Session(get_engine()) as session:
        run_batch(session, Timeframe.HALF_SESSION)


def _crypto_batch_job() -> None:
    with Session(get_engine()) as session:
        run_crypto_batch(session)


def _screener_job() -> None:
    with Session(get_engine()) as session:
        cfg = settings_service.get_screener_config(session)
        exchanges = settings_service.get_crypto_exchanges(session)
        crypto_screener.run_scan_guarded(
            session,
            cfg["mcap_max"],
            cfg["min_volume_change_pct"],
            require_volume_rising=cfg["require_volume_rising"],
            exchanges=exchanges,
        )


def _parse_hhmm(value: str, fallback: str) -> tuple[int, int]:
    raw = value or fallback
    try:
        hour_s, minute_s = raw.split(":")
        return int(hour_s), int(minute_s)
    except (ValueError, AttributeError):
        hour_s, minute_s = fallback.split(":")
        return int(hour_s), int(minute_s)


def _add_jobs(scheduler: BackgroundScheduler, cfg: dict) -> None:
    morning_h, morning_m = _parse_hhmm(cfg["half_morning_time"], "11:35")
    afternoon_h, afternoon_m = _parse_hhmm(cfg["half_afternoon_time"], "15:05")
    daily_h, daily_m = _parse_hhmm(cfg["daily_time"], "15:15")

    scheduler.add_job(
        _half_session_job,
        CronTrigger(hour=morning_h, minute=morning_m, day_of_week=_WEEKDAYS),
        id="half_session_morning",
    )
    scheduler.add_job(
        _half_session_job,
        CronTrigger(hour=afternoon_h, minute=afternoon_m, day_of_week=_WEEKDAYS),
        id="half_session_afternoon",
    )
    scheduler.add_job(
        _daily_job,
        CronTrigger(hour=daily_h, minute=daily_m, day_of_week=_WEEKDAYS),
        id="daily_close",
    )


def reschedule(scheduler: BackgroundScheduler) -> None:
    """Re-read settings and rebuild jobs. Safe to call while the scheduler runs.

    Stock (VN market cadence) and crypto-screener jobs have independent
    enable toggles -- one can be off while the other runs.
    """
    for job_id in _JOB_IDS:
        if scheduler.get_job(job_id):
            scheduler.remove_job(job_id)

    with Session(get_engine()) as session:
        stock_cfg = settings_service.get_scheduler_config(session)
        screener_cfg = settings_service.get_screener_config(session)
        crypto_analysis_cfg = settings_service.get_crypto_analysis_config(session)

    if stock_cfg["enabled"]:
        _add_jobs(scheduler, stock_cfg)
    else:
        logger.info("stock scheduler disabled by settings")

    if screener_cfg["enabled"]:
        interval_kwargs = _INTERVAL_TRIGGER_KWARGS.get(screener_cfg["scan_interval"], {"hours": 1})
        scheduler.add_job(_screener_job, IntervalTrigger(**interval_kwargs), id=_SCREENER_JOB_ID)
    else:
        logger.info("crypto screener disabled by settings")

    if crypto_analysis_cfg["enabled"]:
        interval_kwargs = _INTERVAL_TRIGGER_KWARGS.get(crypto_analysis_cfg["interval"], {"hours": 4})
        scheduler.add_job(_crypto_batch_job, IntervalTrigger(**interval_kwargs), id=_CRYPTO_JOB_ID)
    else:
        logger.info("crypto analysis refresh disabled by settings")

    logger.info("scheduler jobs (re)registered: %s", [j.id for j in scheduler.get_jobs()])


def build_scheduler() -> BackgroundScheduler:
    scheduler = BackgroundScheduler(timezone=TIMEZONE)
    reschedule(scheduler)
    return scheduler
