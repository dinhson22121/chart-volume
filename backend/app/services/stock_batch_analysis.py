"""Manual "run analysis now for the whole tracked stock universe" trigger --
the stock analog of crypto_screener's discovery scan / potential_screener's
AI scoring, using the SAME background-task + lock + polled-status shape.

Unlike scheduler.run_batch (which only re-analyses whatever's ALREADY
tracked, on its fixed 3x/day cron), this first refreshes the HOSE+HNX
liquidity-filtered universe (see hose_hnx.seed_hose_hnx) so a ticker that was
never manually seeded/watchlisted gets pulled in too, then ingests + analyses
every tracked stock ticker (VN30 + watchlist + HOSE/HNX) exactly like
scheduler.run_stock_symbol does per-ticker -- just manually triggerable,
progress-tracked, and cancellable given how long a run across hundreds of
tickers can take.

Always daily-only and use_ai=False: half_session only makes sense run near
actual VN market session boundaries (not on an ad-hoc manual trigger), and
spending one real AI call per ticker across a run this size would be far too
slow/costly -- the deterministic template explanation is used instead, same
reasoning as app.services.scenario_backtest.
"""

from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from sqlmodel import Session

from app.models import AssetClass, Timeframe
from app.scheduler import MAX_WORKERS, run_stock_symbol, tracked_symbols
from app.services import activity_log, hose_hnx

logger = logging.getLogger("chart_volume.stock_batch_analysis")

_lock = threading.Lock()
# Set by request_cancel(), polled between completed tickers so a multi-minute
# run across hundreds of tickers can actually be stopped instead of running
# to completion regardless.
_cancel_requested = threading.Event()
_state: dict = {
    "running": False,
    "total": None,
    "completed": None,
    "failed": None,
    "current_ticker": None,
    "last_error": None,
    "last_cancelled": False,
    "last_completed_at": None,
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def get_status() -> dict:
    return dict(_state)


def request_cancel() -> dict:
    _cancel_requested.set()
    return get_status()


def run_full_universe_analysis(session: Session, trigger: str = "manual") -> dict:
    """Single ThreadPoolExecutor, capped by MAX_WORKERS -- same shape as
    scheduler.run_batch. A ProcessPoolExecutor split (analysis is CPU-bound
    and threads can't parallelize it, unlike ingest) was tried and measured
    to reproducibly hang ~1/3 of the time once ingest_for_ticker's real
    network calls preceded it in the same process -- a real, not fully
    understood interaction between threaded network I/O and spawning a new
    process afterward, not something safe to ship for a button a user
    expects to just work. Reverted; see git history for the two-pool
    version if this is worth retrying later. scheduler.ingest_for_ticker/
    analyze_for_ticker stay split (run_stock_symbol below just composes
    them in order) since the split itself is harmless and reads clearly,
    independent of the abandoned parallelization attempt."""
    if not _lock.acquire(blocking=False):
        logger.info("stock batch analysis already running, ignoring duplicate trigger")
        return get_status()

    log_id = activity_log.log_action_start(session, "stock_batch_analysis", trigger)
    completed = 0
    failed = 0
    total = 0
    try:
        _cancel_requested.clear()
        _state.update(
            running=True, total=None, completed=0, failed=0, current_ticker=None,
            last_error=None, last_cancelled=False,
        )

        # Refresh membership first -- a stock liquid enough to qualify but
        # never manually seeded/watchlisted must still be included below.
        hose_hnx.seed_hose_hnx(session, trigger=trigger)

        symbols = tracked_symbols(session, AssetClass.STOCK)
        total = len(symbols)
        _state["total"] = total

        engine = session.get_bind()
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {
                executor.submit(run_stock_symbol, engine, symbol.ticker, Timeframe.DAILY, False): symbol.ticker
                for symbol in symbols
            }
            for future in as_completed(futures):
                ticker = futures[future]
                _state["current_ticker"] = ticker
                if future.result():
                    completed += 1
                else:
                    failed += 1
                _state["completed"] = completed
                _state["failed"] = failed
                if _cancel_requested.is_set():
                    # Only affects futures the pool hasn't started yet --
                    # already-running workers finish naturally (safe: each
                    # ingest+analyze is self-contained and idempotent).
                    for remaining in futures:
                        remaining.cancel()
                    break

        _state["last_cancelled"] = _cancel_requested.is_set()
        if _state["last_cancelled"]:
            activity_log.log_action_finish(session, log_id, "cancelled", f"{completed}/{total} mã")
        else:
            activity_log.log_action_finish(session, log_id, "success", f"{completed}/{total} mã")
    except Exception as exc:  # noqa: BLE001 - never let this crash the caller
        logger.warning("stock batch analysis failed: %s", exc)
        _state["last_error"] = str(exc)
        activity_log.log_action_finish(session, log_id, "error", str(exc))
    finally:
        _state["current_ticker"] = None
        _state["running"] = False
        _state["last_completed_at"] = _utcnow().isoformat()
        _lock.release()
    return get_status()
