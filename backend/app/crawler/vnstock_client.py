"""Thin wrapper over vnstock's VCI explorer with retry + graceful failure.

We deliberately use the low-level ``vnstock.explorer.vci`` classes instead of
the high-level ``Vnstock().stock()`` facade: the facade eagerly fetches company
metadata on construction and currently breaks on the VCI backend, whereas the
explorer ``Quote`` only hits the price-history endpoint we need.

vnstock is an unofficial scraper, so every call is wrapped in retry and, where a
sensible static fallback exists (VN30 membership), degrades gracefully instead
of crashing the scheduler.
"""

from __future__ import annotations

import io
import logging
import time
from contextlib import redirect_stdout
from typing import Callable

import pandas as pd
from vnstock.explorer.vci.listing import Listing
from vnstock.explorer.vci.quote import Quote

logger = logging.getLogger("chart_volume.crawler")

_MAX_RETRIES = 3
_RETRY_BASE_DELAY = 1.0

# VN30 rebalances quarterly; this static seed is the fallback when the live
# group endpoint is unavailable. Refresh manually when the index rebalances.
VN30_FALLBACK: tuple[str, ...] = (
    "ACB", "BCM", "BID", "BVH", "CTG", "FPT", "GAS", "GVR", "HDB", "HPG",
    "LPB", "MBB", "MSN", "MWG", "PLX", "SAB", "SHB", "SSB", "SSI", "STB",
    "TCB", "TPB", "VCB", "VHM", "VIB", "VIC", "VJC", "VNM", "VPB", "VRE",
)


class CrawlError(RuntimeError):
    """Raised when a crawl fails after all retries."""


def _with_retry(fn: Callable[[], pd.DataFrame], what: str) -> pd.DataFrame:
    last_exc: Exception | None = None
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            # vnstock prints promo banners to stdout; keep our logs clean.
            with redirect_stdout(io.StringIO()):
                return fn()
        except Exception as exc:  # noqa: BLE001 - unofficial API, any failure retryable
            last_exc = exc
            logger.warning("crawl %s attempt %d/%d failed: %s", what, attempt, _MAX_RETRIES, exc)
            time.sleep(_RETRY_BASE_DELAY * attempt)
    raise CrawlError(f"{what} failed after {_MAX_RETRIES} attempts: {last_exc}") from last_exc


def fetch_daily(ticker: str, start: str, end: str) -> pd.DataFrame:
    """Daily OHLCV. Columns: time, open, high, low, close, volume."""
    return _with_retry(
        lambda: Quote(ticker).history(start=start, end=end, interval="1D"),
        f"daily {ticker}",
    )


def fetch_hourly(ticker: str, start: str, end: str) -> pd.DataFrame:
    """Hourly OHLCV, used to build half-session candles."""
    return _with_retry(
        lambda: Quote(ticker).history(start=start, end=end, interval="1H"),
        f"hourly {ticker}",
    )


def fetch_vn30() -> tuple[list[str], str]:
    """Live VN30 membership, falling back to the static seed on failure.

    Returns (tickers, source) where source is "live" or "fallback" -- surfaced
    up to the UI so a stale/offline fallback list isn't silently mistaken for
    fresh data (mirrors how the crypto screener surfaces last_error/status).
    """
    try:
        group = _with_retry(lambda: Listing().symbols_by_group("VN30"), "vn30 list")
        tickers = [str(t).upper() for t in list(group) if str(t).strip()]
        if tickers:
            return tickers, "live"
        logger.warning("VN30 live fetch returned empty, using fallback")
    except CrawlError as exc:
        logger.warning("VN30 live fetch failed, using fallback: %s", exc)
    return list(VN30_FALLBACK), "fallback"
