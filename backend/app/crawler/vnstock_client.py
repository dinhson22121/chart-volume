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
from typing import Callable, TypeVar

import pandas as pd
import requests
from vnstock.core.utils.user_agent import get_headers
from vnstock.explorer.vci.const import _TRADING_URL
from vnstock.explorer.vci.quote import Quote
from vnstock.explorer.vci.trading import Trading

logger = logging.getLogger("chart_volume.crawler")

_MAX_RETRIES = 3
_RETRY_BASE_DELAY = 1.0
# vnstock's own quota guardian (vnai.beam.quota) resets its free-tier window
# every 60s -- a few seconds' margin over that so a retry lands after the
# window has actually rolled over, not right at the edge of it.
_RATE_LIMIT_WAIT_SECONDS = 65.0
_T = TypeVar("_T")

# VN30 rebalances quarterly; this static seed is the fallback when the live
# group endpoint is unavailable. Refresh manually when the index rebalances.
VN30_FALLBACK: tuple[str, ...] = (
    "ACB", "BCM", "BID", "BVH", "CTG", "FPT", "GAS", "GVR", "HDB", "HPG",
    "LPB", "MBB", "MSN", "MWG", "PLX", "SAB", "SHB", "SSB", "SSI", "STB",
    "TCB", "TPB", "VCB", "VHM", "VIB", "VIC", "VJC", "VNM", "VPB", "VRE",
)


class CrawlError(RuntimeError):
    """Raised when a crawl fails after all retries."""


def _is_rate_limit_error(exc: BaseException) -> bool:
    # vnai's own quota guardian (vnai.beam.quota.CleanErrorContext.__exit__)
    # catches its RateLimitExceeded internally and re-raises via sys.exit(...)
    # instead of a normal exception -- SystemExit isn't an Exception subclass,
    # so it needs its own check rather than falling through the except below.
    return isinstance(exc, SystemExit) and "rate limit exceeded" in str(exc).lower()


def _with_retry(fn: Callable[[], _T], what: str) -> _T:
    last_exc: BaseException | None = None
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            # vnstock prints promo banners to stdout; keep our logs clean.
            with redirect_stdout(io.StringIO()):
                return fn()
        except (Exception, SystemExit) as exc:  # noqa: BLE001 - unofficial API, any failure retryable
            last_exc = exc
            if _is_rate_limit_error(exc):
                # A short exponential backoff (below) is pointless here -- the
                # free-tier window won't reset for up to ~60s regardless, so
                # retrying sooner just burns another attempt on the same
                # still-closed window.
                logger.warning(
                    "crawl %s rate-limited (attempt %d/%d), waiting %.0fs for quota reset",
                    what, attempt, _MAX_RETRIES, _RATE_LIMIT_WAIT_SECONDS,
                )
                time.sleep(_RATE_LIMIT_WAIT_SECONDS)
            else:
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


def _fetch_vn30_live() -> list[str]:
    # Deliberately bypasses vnstock's own Listing.symbols_by_group("VN30"):
    # that call is wrapped by vnai's "optimize_execution" quota/telemetry
    # decorator (bundled with vnstock), which reliably breaks this specific
    # endpoint with a JSON-decode error ("Expecting value...") -- an issue
    # vnstock's own CLI banner attributes to needing their paid "Insiders"
    # tier for "increased API limits", i.e. an intentional free-tier quota
    # gate, not a random bug. A raw request with the exact same headers
    # vnstock itself generates, but routed around that wrapper, succeeds
    # consistently (verified with repeated manual runs). Reuses vnstock's
    # own header-generation helper and base URL so it still tracks any
    # future header/URL changes on their end.
    headers = get_headers(data_source="VCI")
    url = f"{_TRADING_URL}price/symbols/getByGroup?group=VN30"
    resp = requests.get(url, headers=headers, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if not data:
        raise CrawlError("VN30 group endpoint returned empty data")
    return [str(item["symbol"]).upper() for item in data if item.get("symbol")]


def fetch_vn30() -> tuple[list[str], str]:
    """Live VN30 membership, falling back to the static seed on failure.

    Returns (tickers, source) where source is "live" or "fallback" -- surfaced
    up to the UI so a stale/offline fallback list isn't silently mistaken for
    fresh data (mirrors how the crypto screener surfaces last_error/status).
    """
    try:
        tickers = _with_retry(_fetch_vn30_live, "vn30 list")
        if tickers:
            return tickers, "live"
        logger.warning("VN30 live fetch returned empty, using fallback")
    except CrawlError as exc:
        logger.warning("VN30 live fetch failed, using fallback: %s", exc)
    return list(VN30_FALLBACK), "fallback"


# getAll's board values -> this app's own Exchange constants (app.models).
# Only the two listed exchanges are ever returned here; UPCOM/DELISTED/BOND
# rows are filtered out entirely in _fetch_hose_hnx_universe_live.
_BOARD_TO_EXCHANGE = {"HSX": "HOSE", "HNX": "HNX"}


def _fetch_hose_hnx_universe_live() -> list[dict[str, str]]:
    """Every common stock listed on HOSE or HNX, as {"ticker", "exchange"}
    dicts. Same raw-bypass rationale as _fetch_vn30_live (routes around
    vnai's broken quota decorator on vnstock's own Listing wrapper) hitting
    getAll instead of getByGroup -- getAll returns every instrument on every
    board (stocks, covered warrants, ETFs, futures, REITs, UPCOM, bonds,
    delisted), so board/type filtering here is what narrows it down to
    "common stock currently listed on one of the two exchanges" (verified
    against the live endpoint: ~402 HOSE + ~299 HNX stocks, out of ~3500
    total instruments across every board)."""
    headers = get_headers(data_source="VCI")
    url = f"{_TRADING_URL}price/symbols/getAll"
    resp = requests.get(url, headers=headers, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if not data:
        raise CrawlError("getAll endpoint returned empty data")
    return [
        {
            "ticker": str(item["symbol"]).upper(),
            "exchange": _BOARD_TO_EXCHANGE[item["board"]],
            "name": item.get("organName") or item.get("enOrganName") or "",
        }
        for item in data
        if item.get("symbol") and item.get("type") == "STOCK" and item.get("board") in _BOARD_TO_EXCHANGE
    ]


def fetch_hose_hnx_universe() -> list[dict[str, str]]:
    """Live HOSE+HNX common-stock universe. Unlike fetch_vn30, there is no
    static fallback: VN30 is a small, quarterly-rebalanced index worth
    hardcoding a seed for, but this list changes daily (new listings,
    delistings) with no small stable subset worth freezing -- a persistent
    failure surfaces as CrawlError instead of silently serving stale data."""
    return _with_retry(_fetch_hose_hnx_universe_live, "hose/hnx universe")


_LIQUIDITY_BATCH_SIZE = 100


def fetch_liquidity_snapshot(tickers: list[str], batch_size: int = _LIQUIDITY_BATCH_SIZE) -> dict[str, float]:
    """Today's accumulated traded value (VND) per ticker, via VCI's batch
    price-board endpoint -- used to filter the ~700-strong HOSE/HNX universe
    down to tickers worth crawling full history for, before that (much
    heavier) crawl ever runs. A single day's snapshot, not a rolling
    average -- cheap, and consistent with how the crypto Top100 seed also
    ranks off a single market-data snapshot (see app.services.top100), not a
    smoothed multi-day figure. Batched since a single request for the full
    universe is untested and risky against an unofficial API. The endpoint's
    accumulated_value is in MILLIONS of VND (empirically confirmed by
    cross-checking against known daily traded values for large, liquid
    tickers -- undocumented in the API itself), converted to plain VND here
    so callers share a unit with settings_service.stock_min_avg_value_vnd."""
    if not tickers:
        return {}
    trading = Trading(symbol=tickers[0])
    result: dict[str, float] = {}
    for i in range(0, len(tickers), batch_size):
        batch = tickers[i : i + batch_size]
        try:
            df = _with_retry(lambda b=batch: trading.price_board(b), f"price board batch {i // batch_size}")
        except CrawlError as exc:
            # One batch's transient failure (network timeout, unofficial API
            # hiccup) must not cost every OTHER batch's liquidity data too --
            # those tickers just come out missing from the result, which
            # callers (seed_hose_hnx) already treat as "not enough data to
            # qualify" via their own 0.0 fallback, not a hard failure.
            logger.warning(
                "liquidity snapshot batch %d failed, skipping %d ticker(s): %s", i // batch_size, len(batch), exc
            )
            continue
        for _, row in df.iterrows():
            ticker = row.get(("listing", "symbol"))
            value = row.get(("match", "accumulated_value"))
            if ticker and value is not None:
                result[str(ticker).upper()] = float(value) * 1_000_000
    return result
