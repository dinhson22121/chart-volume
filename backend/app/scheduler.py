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
from app.services import activity_log, crypto_screener, ingest, potential_screener, settings_service, top100
from app.services.analysis import run_analysis

logger = logging.getLogger("chart_volume.scheduler")

_TZ = ZoneInfo(TIMEZONE)  # BackgroundScheduler accepts the timezone name as a
# plain string, but datetime.now() needs an actual tzinfo instance.

_WEEKDAYS = "mon-fri"

_STOCK_JOB_IDS = ("half_session_morning", "half_session_afternoon", "daily_close")
_SCREENER_JOB_ID = "crypto_screener_scan"
_CRYPTO_JOB_ID = "crypto_analysis_refresh"
_TOP100_JOB_ID = "top100_refresh"
_POTENTIAL_SCREEN_JOB_ID = "potential_screen_refresh"

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
    # is_top100 is only ever True on crypto symbols (top100.seed_top100 always
    # sets asset_class=CRYPTO), so including it here is a no-op for the stock
    # batch and extends the crypto batch to also auto-analyze the Top 100
    # list -- needed so dashboard ranking has real Analysis data for them,
    # not just symbols the user happened to open manually.
    return session.exec(
        select(Symbol).where(
            (Symbol.is_vn30 == True) | (Symbol.is_watchlist == True) | (Symbol.is_top100 == True),  # noqa: E712
            Symbol.asset_class == asset_class,
        )
    ).all()


def _symbol_gets_ai(symbol: Symbol, ai_groups: dict) -> bool:
    """OR across the symbol's group memberships -- being in any AI-enabled
    group is enough (a VN30 stock the user also watchlisted follows whichever
    of the two toggles is on)."""
    return (
        (symbol.is_vn30 and ai_groups["vn30"])
        or (symbol.is_watchlist and ai_groups["watchlist"])
        or (symbol.is_top100 and ai_groups["top100"])
    )


def run_batch(session: Session, timeframe: str, use_ai: bool = True) -> int:
    """Ingest + analyse every tracked STOCK ticker. Returns how many succeeded."""
    ai_groups = settings_service.get_ai_narrative_groups(session)
    ok = 0
    for symbol in _tracked_symbols(session, AssetClass.STOCK):
        ticker = symbol.ticker
        try:
            if timeframe == Timeframe.DAILY:
                ingest.ingest_daily(session, ticker)
            else:
                ingest.ingest_half_session(session, ticker)
            run_analysis(session, ticker, timeframe, use_ai=use_ai and _symbol_gets_ai(symbol, ai_groups))
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
    ai_groups = settings_service.get_ai_narrative_groups(session)
    ok = 0
    for symbol in _tracked_symbols(session, AssetClass.CRYPTO):
        # Per-group AI narrative toggles (Settings): a symbol still gets the
        # quantitative analysis (phase/confidence/signals feed the dashboard
        # ranking) either way -- the toggle only controls the LLM call.
        # Top100 defaults off: ~100 coins x 3 timeframes per cycle of
        # narratives nobody opens is a token burn.
        symbol_use_ai = use_ai and _symbol_gets_ai(symbol, ai_groups)
        for timeframe in _CRYPTO_TIMEFRAMES:
            try:
                ingest.ingest_crypto(
                    session, symbol.ticker, timeframe,
                    exchange_symbol=symbol.display_symbol, exchanges=exchanges, symbol=symbol,
                )
                run_analysis(session, symbol.ticker, timeframe, use_ai=symbol_use_ai)
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


def _top100_job() -> None:
    # log_action_start/finish live inside seed_top100 -- the job only isolates
    # the exception so a failed CoinGecko fetch doesn't spam APScheduler.
    with Session(get_engine()) as session:
        try:
            top100.seed_top100(session, "scheduled")
        except Exception as exc:  # noqa: BLE001 - seed_top100 already logged the error
            logger.warning("scheduled top100 refresh failed: %s", exc)


def _potential_screen_job() -> None:
    # log_action_start/finish live inside run_potential_screen -- the job
    # only isolates the exception so a failure doesn't spam APScheduler.
    with Session(get_engine()) as session:
        try:
            potential_screener.run_potential_screen(session, "scheduled")
        except Exception as exc:  # noqa: BLE001 - run_potential_screen already logged the error
            logger.warning("scheduled potential screen failed: %s", exc)


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
        logger.warning("malformed HH:MM setting %r, falling back to %r", value, fallback)
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
    for job_id in (*_STOCK_JOB_IDS, _TOP100_JOB_ID, _POTENTIAL_SCREEN_JOB_ID):
        if scheduler.get_job(job_id):
            scheduler.remove_job(job_id)

    with Session(get_engine()) as session:
        stock_cfg = settings_service.get_scheduler_config(session)
        screener_cfg = settings_service.get_screener_config(session)
        crypto_analysis_cfg = settings_service.get_crypto_analysis_config(session)
        top100_cfg = settings_service.get_top100_config(session)
        potential_screen_cfg = settings_service.get_potential_screen_config(session)

    if stock_cfg["enabled"]:
        _add_jobs(scheduler, stock_cfg)
    else:
        logger.info("stock scheduler disabled by settings")

    if top100_cfg["enabled"]:
        top100_h, top100_m = _parse_hhmm(top100_cfg["time"], "07:00")
        # Every day of the week -- crypto has no weekend close, unlike the
        # _WEEKDAYS-bound stock jobs.
        scheduler.add_job(
            _top100_job, CronTrigger(hour=top100_h, minute=top100_m), id=_TOP100_JOB_ID
        )
    else:
        logger.info("top100 auto refresh disabled by settings")

    if potential_screen_cfg["enabled"]:
        ps_h, ps_m = _parse_hhmm(potential_screen_cfg["time"], "06:30")
        scheduler.add_job(
            _potential_screen_job, CronTrigger(hour=ps_h, minute=ps_m), id=_POTENTIAL_SCREEN_JOB_ID
        )
    else:
        logger.info("potential screen auto refresh disabled by settings")

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
