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
import threading
from datetime import datetime, timedelta
from functools import partial
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlmodel import Session, select

from app.config import TIMEZONE
from app.db import get_engine
from app.models import AssetClass, Symbol, Timeframe
from app.services import activity_log, crypto_screener, ingest, settings_service
from app.services.analysis import run_analysis

logger = logging.getLogger("chart_volume.scheduler")

_TZ = ZoneInfo(TIMEZONE)  # BackgroundScheduler accepts the timezone name as a
# plain string, but datetime.now() needs an actual tzinfo instance.

_WEEKDAYS = "mon-fri"

_STOCK_JOB_IDS = ("half_session_morning", "half_session_afternoon", "daily_close")
_SCREENER_JOB_ID = "crypto_screener_scan"
_CRYPTO_JOB_ID = "crypto_analysis_refresh"

# Set once by build_scheduler() -- interval jobs are self-rescheduling (see
# _reschedule_after_run) and need a handle to the live scheduler to queue
# their own next run once they finish.
_active_scheduler: BackgroundScheduler | None = None

# crypto_analysis_refresh has no lock of its own (unlike run_scan_guarded's
# _scan_lock) -- needed so a reschedule() call that lands mid-run (see
# _sync_interval_job) can't launch a genuinely overlapping second run.
_crypto_batch_lock = threading.Lock()

# Crypto has no morning/afternoon session split -- one job sweeps all 3
# timeframes on its own interval, unlike stocks (one job per timeframe/time).
_CRYPTO_TIMEFRAMES = (Timeframe.HOUR_1, Timeframe.HOUR_4, Timeframe.DAILY)

# scan_interval/crypto_analysis_interval setting -> timedelta() kwargs, used
# by _reschedule_after_run to compute each job's next fixed-delay run date.
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
        log_id = activity_log.log_action_start(session, "daily_close", "scheduled")
        try:
            ok = run_batch(session, Timeframe.DAILY)
            activity_log.log_action_finish(session, log_id, "success", f"{ok} mã")
        except Exception as exc:  # noqa: BLE001 - isolate; run_batch already isolates per-ticker
            activity_log.log_action_finish(session, log_id, "error", str(exc))
            raise


def _half_session_job(action: str) -> None:
    # Same function backs both the morning and afternoon cron triggers (see
    # _add_jobs()) -- `action` is bound via functools.partial at registration
    # time so the log can still tell the two apart.
    with Session(get_engine()) as session:
        log_id = activity_log.log_action_start(session, action, "scheduled")
        try:
            ok = run_batch(session, Timeframe.HALF_SESSION)
            activity_log.log_action_finish(session, log_id, "success", f"{ok} mã")
        except Exception as exc:  # noqa: BLE001 - isolate; run_batch already isolates per-ticker
            activity_log.log_action_finish(session, log_id, "error", str(exc))
            raise


def _reschedule_after_run(job_id: str, func, enabled: bool, interval_key: str) -> None:
    """Queues the next run exactly ``interval_key`` after THIS run's
    completion (fixed-delay), not fixed-rate from registration time -- call
    this right after a run finishes (success or error). No-ops if the job's
    been disabled in the meantime (checked fresh, not at the run's start)."""
    if _active_scheduler is None or not enabled:
        return
    kwargs = _INTERVAL_TRIGGER_KWARGS.get(interval_key, {"hours": 1})
    next_run = datetime.now(_TZ) + timedelta(**kwargs)
    _active_scheduler.add_job(func, "date", run_date=next_run, id=job_id, replace_existing=True)


def _crypto_batch_job() -> None:
    if not _crypto_batch_lock.acquire(blocking=False):
        logger.info("crypto_analysis_refresh already running, ignoring duplicate trigger")
        return
    try:
        with Session(get_engine()) as session:
            log_id = activity_log.log_action_start(session, "crypto_analysis_refresh", "scheduled")
            try:
                ok = run_crypto_batch(session)
                activity_log.log_action_finish(session, log_id, "success", f"{ok} mã/khung")
            except Exception as exc:  # noqa: BLE001 - isolate; run_crypto_batch already isolates per-symbol
                activity_log.log_action_finish(session, log_id, "error", str(exc))
                raise
    finally:
        _crypto_batch_lock.release()
    with Session(get_engine()) as session:
        fresh_cfg = settings_service.get_crypto_analysis_config(session)
    _reschedule_after_run(_CRYPTO_JOB_ID, _crypto_batch_job, fresh_cfg["enabled"], fresh_cfg["interval"])


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
            trigger="scheduled",
        )
    with Session(get_engine()) as session:
        fresh_cfg = settings_service.get_screener_config(session)
    _reschedule_after_run(_SCREENER_JOB_ID, _screener_job, fresh_cfg["enabled"], fresh_cfg["scan_interval"])


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
        partial(_half_session_job, "half_session_morning"),
        CronTrigger(hour=morning_h, minute=morning_m, day_of_week=_WEEKDAYS),
        id="half_session_morning",
    )
    scheduler.add_job(
        partial(_half_session_job, "half_session_afternoon"),
        CronTrigger(hour=afternoon_h, minute=afternoon_m, day_of_week=_WEEKDAYS),
        id="half_session_afternoon",
    )
    scheduler.add_job(
        _daily_job,
        CronTrigger(hour=daily_h, minute=daily_m, day_of_week=_WEEKDAYS),
        id="daily_close",
    )


def _sync_interval_job(scheduler: BackgroundScheduler, job_id: str, func, enabled: bool, label: str) -> None:
    """Only starts or stops the self-rescheduling chain (see
    _reschedule_after_run) -- never disrupts a job that already has a
    pending next-run queued, so an unrelated settings save doesn't reset
    jobs that are mid-wait or mid-run back to "run immediately"."""
    existing = scheduler.get_job(job_id)
    if not enabled:
        if existing:
            scheduler.remove_job(job_id)
        logger.info("%s disabled by settings", label)
        return
    if existing:
        return  # chain already running -- leave its pending next-run alone
    scheduler.add_job(func, "date", run_date=datetime.now(_TZ), id=job_id)


def reschedule(scheduler: BackgroundScheduler) -> None:
    """Re-read settings and rebuild jobs. Safe to call while the scheduler runs.

    Stock (VN market cadence) and crypto-screener jobs have independent
    enable toggles -- one can be off while the other runs.
    """
    for job_id in _STOCK_JOB_IDS:
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

    _sync_interval_job(scheduler, _SCREENER_JOB_ID, _screener_job, screener_cfg["enabled"], "crypto screener")
    _sync_interval_job(
        scheduler, _CRYPTO_JOB_ID, _crypto_batch_job, crypto_analysis_cfg["enabled"], "crypto analysis refresh"
    )

    logger.info("scheduler jobs (re)registered: %s", [j.id for j in scheduler.get_jobs()])


def build_scheduler() -> BackgroundScheduler:
    global _active_scheduler
    scheduler = BackgroundScheduler(timezone=TIMEZONE)
    _active_scheduler = scheduler
    reschedule(scheduler)
    return scheduler
