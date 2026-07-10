"""Crypto screener: find coins in a market-cap band with rising 24h volume.

CoinGecko never reports a volume *trend* -- only the current 24h total -- so
we keep our own snapshot history (CryptoVolumeSnapshot) and compute the
"rising" percentage ourselves by comparing the latest snapshot to the prior
one for each coin. The very first scan for a coin has nothing to compare
against, so volume_change_pct stays null until a second scan exists (same
"needs a second data point" shape as SignalOutcome's forward returns).

CoinGecko's public API is aggressively rate-limited, so a full scan pages
through the market list slowly (a pause between pages) rather than firing
requests back to back -- a real scan takes minutes, not seconds.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone

from sqlmodel import Session, func, select

from app.crawler import binance_client, coingecko_client, geckoterminal_client, kucoin_client, mexc_client
from app.models import CryptoExchange, CryptoVolumeSnapshot, ScreenerCandidate

logger = logging.getLogger("chart_volume.screener")

PAGE_PAUSE_SECONDS = 3.0  # be gentle with CoinGecko's rate limit between pages
MAX_PAGES = 40  # safety cap: 40 * 250 = 10,000 coins, far past any realistic mcap band
MIN_MARKET_CAP_FLOOR = 100_000.0  # ignore near-zero/broken market cap entries

DEX_PAGE_PAUSE_SECONDS = 6.0  # GeckoTerminal rate-limits at least as hard as CoinGecko
DEX_MAX_PAGES = 5  # per discovery endpoint (new_pools, trending_pools) -- kept modest

# A scan takes minutes (rate-limited pagination) and can be triggered either by
# the scheduler or a manual "scan now" click -- this lock/state is shared so
# both paths go through run_scan_guarded() and never overlap.
_scan_lock = threading.Lock()
_scan_state: dict = {
    "running": False,
    "last_completed_at": None,
    "last_hits": None,
    "last_error": None,
    "last_cancelled": False,
    # Live progress, updated as scan()/_scan_dex_pools() page through results --
    # polled by the frontend so it can show a progress line while running.
    # There's no reliable upfront "total pages" (CoinGecko doesn't report one,
    # and the real page count depends on where the mcap band happens to end),
    # so this is "how far in" rather than a true percentage.
    "phase": None,
    "current_page": None,
    "hits_so_far": None,
}
# Set by request_cancel(), polled by scan() between pages so a multi-minute
# scan can be stopped without waiting for it to finish on its own.
_cancel_requested = threading.Event()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _previous_snapshot(session: Session, coin_id: str, before: datetime) -> CryptoVolumeSnapshot | None:
    return session.exec(
        select(CryptoVolumeSnapshot)
        .where(CryptoVolumeSnapshot.coin_id == coin_id, CryptoVolumeSnapshot.scanned_at < before)
        .order_by(CryptoVolumeSnapshot.scanned_at.desc())
    ).first()


def _tradeable_symbols(exchanges: tuple[str, ...]) -> set[str] | None:
    """Combined base-asset symbols (e.g. "BTC") tradeable on any enabled
    exchange -- used to skip screener hits we could never chart anyway (see
    the ARGN case: CoinGecko lists thousands of coins that never made it onto
    a centralized exchange). Returns None if every enabled exchange failed to
    respond, so a transient network hiccup doesn't silently zero out a scan.
    """
    symbols: set[str] = set()
    fetched_any = False
    if CryptoExchange.BINANCE in exchanges:
        try:
            symbols |= binance_client.fetch_tradeable_symbols()
            fetched_any = True
        except Exception as exc:  # noqa: BLE001 - degrade to "no filter", don't kill the scan
            logger.warning("could not fetch Binance tradeable symbols: %s", exc)
    if CryptoExchange.KUCOIN in exchanges:
        try:
            symbols |= kucoin_client.fetch_tradeable_symbols()
            fetched_any = True
        except Exception as exc:  # noqa: BLE001
            logger.warning("could not fetch KuCoin tradeable symbols: %s", exc)
    if CryptoExchange.MEXC in exchanges:
        try:
            symbols |= mexc_client.fetch_tradeable_symbols()
            fetched_any = True
        except Exception as exc:  # noqa: BLE001
            logger.warning("could not fetch MEXC tradeable symbols: %s", exc)
    return symbols if fetched_any else None


def _upsert_candidate(
    session: Session,
    coin_id: str,
    symbol: str,
    name: str,
    mcap: float,
    volume_24h: float,
    change_pct: float | None,
    now: datetime,
    source: str = "coingecko",
    network: str | None = None,
    pool_address: str | None = None,
) -> None:
    values = dict(
        symbol=symbol,
        name=name,
        market_cap=mcap,
        volume_24h=volume_24h,
        volume_change_pct=change_pct,
        last_seen_at=now,
        source=source,
        network=network,
        pool_address=pool_address,
    )
    existing = session.get(ScreenerCandidate, coin_id)
    if existing:
        for key, value in values.items():
            setattr(existing, key, value)
        session.add(existing)
    else:
        session.add(ScreenerCandidate(coin_id=coin_id, **values))


def _maybe_add_candidate(
    session: Session,
    coin_id: str,
    symbol: str,
    name: str,
    mcap: float,
    volume_24h: float,
    now: datetime,
    require_volume_rising: bool,
    min_volume_change_pct: float,
    source: str = "coingecko",
    network: str | None = None,
    pool_address: str | None = None,
) -> bool:
    """Records a volume snapshot and, if it qualifies under
    ``require_volume_rising``, upserts it as a candidate. Returns whether it
    became/stayed a candidate. Shared between the CoinGecko market-list pass
    and the GeckoTerminal DEX-pool pass so both apply identical logic."""
    prev = _previous_snapshot(session, coin_id, now)
    session.add(
        CryptoVolumeSnapshot(coin_id=coin_id, symbol=symbol, market_cap=mcap, volume_24h=volume_24h, scanned_at=now)
    )
    change_pct = None
    if prev and prev.volume_24h > 0:
        change_pct = (volume_24h - prev.volume_24h) / prev.volume_24h * 100
    if require_volume_rising and not (change_pct is not None and change_pct >= min_volume_change_pct):
        return False
    _upsert_candidate(session, coin_id, symbol, name, mcap, volume_24h, change_pct, now, source, network, pool_address)
    return True


def _scan_dex_pools(
    session: Session,
    mcap_max: float,
    mcap_min: float,
    min_volume_change_pct: float,
    require_volume_rising: bool,
    now: datetime,
    base_hits: int = 0,
) -> int:
    """DEX-pool discovery pass via GeckoTerminal: new_pools then
    trending_pools, both across all networks at once. Unlike the CoinGecko
    pass these aren't sorted by market cap, so there's no early-exit
    optimization -- just a hard page cap given the strict rate limit."""
    hits = 0
    _scan_state["phase"] = "dex_pools"
    for fetch_page in (geckoterminal_client.fetch_new_pools, geckoterminal_client.fetch_trending_pools):
        for page in range(1, DEX_MAX_PAGES + 1):
            _scan_state["current_page"] = page
            if _cancel_requested.is_set():
                logger.info("DEX discovery cancelled by user")
                return hits
            try:
                pools = fetch_page(page)
            except geckoterminal_client.CrawlError as exc:
                logger.warning("DEX discovery stopped early at page %d: %s", page, exc)
                break
            if not pools:
                break
            for pool in pools:
                mcap = pool["market_cap"]
                if mcap <= 0 or mcap > mcap_max or mcap < mcap_min:
                    continue
                coin_id = pool["coingecko_coin_id"] or f"gt:{pool['network']}:{pool['pool_address']}"
                if _maybe_add_candidate(
                    session,
                    coin_id,
                    pool["symbol"],
                    pool["name"],
                    mcap,
                    pool["volume_24h"],
                    now,
                    require_volume_rising,
                    min_volume_change_pct,
                    source="geckoterminal",
                    network=pool["network"],
                    pool_address=pool["pool_address"],
                ):
                    hits += 1
            session.commit()
            _scan_state["hits_so_far"] = base_hits + hits
            time.sleep(DEX_PAGE_PAUSE_SECONDS)
    logger.info("DEX discovery complete: %d candidates found", hits)
    return hits


def scan(
    session: Session,
    mcap_max: float,
    min_volume_change_pct: float,
    mcap_min: float = MIN_MARKET_CAP_FLOOR,
    require_volume_rising: bool = True,
    exchanges: tuple[str, ...] = CryptoExchange.ALL,
) -> int:
    """Run one full screener pass. Returns the number of candidates found.

    When ``require_volume_rising`` is True (default), a coin only becomes a
    candidate once its volume has risen at least ``min_volume_change_pct``
    since the previous scan. When False, every coin in the market-cap band
    becomes a candidate regardless of volume trend -- useful to just browse
    the whole band before deciding on a threshold. Snapshots are always
    recorded either way, so switching the toggle on later still has history
    to compare against.

    Coins that aren't tradeable on any enabled ``exchanges`` are skipped
    entirely -- CoinGecko's discovery list includes many DEX-only coins we
    could never fetch candles for, and without this filter they used to flood
    the candidate list (thousands of un-chartable hits) making it both
    useless and slow to render.
    """
    now = _utcnow()
    hits = 0
    page = 1
    tradeable = _tradeable_symbols(exchanges)
    _scan_state["phase"] = "coingecko"
    _scan_state["current_page"] = page
    _scan_state["hits_so_far"] = 0

    while page <= MAX_PAGES:
        if _cancel_requested.is_set():
            logger.info("screener scan cancelled by user at page %d", page)
            break
        try:
            coins = coingecko_client.fetch_markets_page(page)
        except coingecko_client.CrawlError as exc:
            logger.warning("screener scan stopped early at page %d: %s", page, exc)
            break
        if not coins:
            break

        below_range_count = 0
        for coin in coins:
            mcap = coin.get("market_cap") or 0
            if mcap <= 0 or mcap > mcap_max:
                continue
            if mcap < mcap_min:
                below_range_count += 1
                continue
            if tradeable is not None and coin.get("symbol", "").upper() not in tradeable:
                continue

            if _maybe_add_candidate(
                session,
                coin["id"],
                coin.get("symbol", ""),
                coin.get("name", ""),
                mcap,
                coin.get("total_volume") or 0.0,
                now,
                require_volume_rising,
                min_volume_change_pct,
            ):
                hits += 1

        session.commit()
        _scan_state["current_page"] = page
        _scan_state["hits_so_far"] = hits

        # Coins are sorted market_cap_desc; once an entire page falls below
        # mcap_min, everything further down the list is smaller still.
        if below_range_count == len(coins):
            break
        page += 1
        time.sleep(PAGE_PAUSE_SECONDS)

    if CryptoExchange.GECKOTERMINAL in exchanges and not _cancel_requested.is_set():
        hits += _scan_dex_pools(
            session, mcap_max, mcap_min, min_volume_change_pct, require_volume_rising, now, base_hits=hits
        )

    logger.info("screener scan complete: %d candidates found", hits)
    return hits


CANDIDATE_SORT_CHOICES = ("volume_change", "market_cap")
DEFAULT_PAGE_SIZE = 50


def list_candidates(
    session: Session,
    sort: str = "volume_change",
    page: int = 1,
    page_size: int = DEFAULT_PAGE_SIZE,
    query: str | None = None,
) -> tuple[list[ScreenerCandidate], int]:
    """Returns (items for this page, total candidate count).

    ``query`` filters by symbol/name substring (case-insensitive) server-side
    -- candidates can number in the hundreds and are paginated, so a
    client-side-only filter would miss matches not yet scrolled into view.
    """
    order_col = (
        ScreenerCandidate.market_cap.desc()
        if sort == "market_cap"
        else ScreenerCandidate.volume_change_pct.desc()
    )
    stmt = select(ScreenerCandidate)
    count_stmt = select(func.count()).select_from(ScreenerCandidate)
    if query:
        needle = query.lower()
        # autoescape=True treats a literal "%"/"_" in the search text as
        # itself rather than a SQL LIKE wildcard -- otherwise a search like
        # "50%" would over-match anything, not just candidates named "50%".
        condition = func.lower(ScreenerCandidate.symbol).contains(needle, autoescape=True) | func.lower(
            ScreenerCandidate.name
        ).contains(needle, autoescape=True)
        stmt = stmt.where(condition)
        count_stmt = count_stmt.where(condition)

    total = session.exec(count_stmt).one()
    items = session.exec(stmt.order_by(order_col).offset((page - 1) * page_size).limit(page_size)).all()
    return items, total


def get_scan_status() -> dict:
    return dict(_scan_state)


def run_scan_guarded(
    session: Session,
    mcap_max: float,
    min_volume_change_pct: float,
    require_volume_rising: bool = True,
    exchanges: tuple[str, ...] = CryptoExchange.ALL,
) -> dict:
    """Runs scan() guarded by a lock so an overlapping trigger (manual click
    while the scheduled job is mid-scan, or vice versa) is a no-op instead of
    two scans racing each other."""
    if not _scan_lock.acquire(blocking=False):
        logger.info("screener scan already running, ignoring duplicate trigger")
        return get_scan_status()
    try:
        _cancel_requested.clear()
        _scan_state["running"] = True
        _scan_state["last_error"] = None
        _scan_state["last_cancelled"] = False
        _scan_state["phase"] = None
        _scan_state["current_page"] = None
        _scan_state["hits_so_far"] = None
        _scan_state["last_hits"] = scan(
            session,
            mcap_max,
            min_volume_change_pct,
            require_volume_rising=require_volume_rising,
            exchanges=exchanges,
        )
        _scan_state["last_cancelled"] = _cancel_requested.is_set()
    except Exception as exc:  # noqa: BLE001 - never let a scan failure crash the caller
        logger.warning("screener scan failed: %s", exc)
        _scan_state["last_error"] = str(exc)
    finally:
        _scan_state["running"] = False
        _scan_state["last_completed_at"] = _utcnow().isoformat()
        _scan_lock.release()
    return get_scan_status()


def request_cancel() -> dict:
    """Ask a currently-running scan to stop at its next page boundary. A
    no-op (but harmless) if nothing is running."""
    if _scan_state["running"]:
        _cancel_requested.set()
    return get_scan_status()
