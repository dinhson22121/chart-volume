"""Crypto screener: manual scan trigger, status, candidates, promote-to-watchlist.

Scanning takes minutes (CoinGecko's rate limit forces slow pagination -- see
app.services.crypto_screener), so the manual trigger runs in a background
task and returns immediately; the UI polls /crypto/screener/status for
progress, the same way the scheduled scan runs unattended.
"""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlmodel import Session

from app.auth import require_token
from app.db import get_engine, get_session
from app.models import AssetClass, ScreenerCandidate, Symbol
from app.services import crypto_screener, settings_service

router = APIRouter(prefix="/crypto", tags=["crypto"], dependencies=[Depends(require_token)])


def _run_scan_task(
    mcap_max: float,
    min_volume_change_pct: float,
    require_volume_rising: bool,
    exchanges: tuple[str, ...],
) -> None:
    # Background tasks outlive the request, so they need their own session --
    # a multi-minute scan can't reuse the request-scoped `session` dependency.
    with Session(get_engine()) as session:
        crypto_screener.run_scan_guarded(
            session,
            mcap_max,
            min_volume_change_pct,
            require_volume_rising=require_volume_rising,
            exchanges=exchanges,
        )


@router.post("/screener/scan")
def trigger_scan(background_tasks: BackgroundTasks, session: Session = Depends(get_session)) -> dict:
    status = crypto_screener.get_scan_status()
    if status["running"]:
        return {"status": "already_running"}
    cfg = settings_service.get_screener_config(session)
    exchanges = settings_service.get_crypto_exchanges(session)
    background_tasks.add_task(
        _run_scan_task, cfg["mcap_max"], cfg["min_volume_change_pct"], cfg["require_volume_rising"], exchanges
    )
    return {"status": "started"}


@router.get("/screener/status")
def get_scan_status() -> dict:
    return crypto_screener.get_scan_status()


@router.post("/screener/cancel")
def cancel_scan() -> dict:
    return crypto_screener.request_cancel()


def _candidate_out(c: ScreenerCandidate) -> dict:
    return {
        "coin_id": c.coin_id,
        "symbol": c.symbol,
        "name": c.name,
        "market_cap": c.market_cap,
        "volume_24h": c.volume_24h,
        "volume_change_pct": c.volume_change_pct,
        "last_seen_at": c.last_seen_at,
        "source": c.source,
        "network": c.network,
        "exchange": c.exchange,
    }


@router.get("/screener/candidates")
def get_candidates(
    sort: str = Query("volume_change"),
    page: int = Query(1, ge=1),
    page_size: int = Query(crypto_screener.DEFAULT_PAGE_SIZE, ge=1, le=200),
    q: str | None = Query(None, description="Filter by symbol/name substring"),
    exchange: str | None = Query(None, description="Filter by resolved exchange"),
    session: Session = Depends(get_session),
) -> dict:
    if sort not in crypto_screener.CANDIDATE_SORT_CHOICES:
        raise HTTPException(status_code=400, detail=f"sort must be one of {crypto_screener.CANDIDATE_SORT_CHOICES}")
    if exchange is not None and exchange not in crypto_screener.CANDIDATE_EXCHANGE_CHOICES:
        raise HTTPException(
            status_code=400, detail=f"exchange must be one of {crypto_screener.CANDIDATE_EXCHANGE_CHOICES}"
        )
    items, total = crypto_screener.list_candidates(
        session, sort=sort, page=page, page_size=page_size, query=q, exchange=exchange
    )
    return {
        "items": [_candidate_out(c) for c in items],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.post("/screener/candidates/{coin_id}/promote")
def promote_candidate(coin_id: str, session: Session = Depends(get_session)) -> dict:
    """Turn a screener hit into a tracked Symbol so the user can chart/analyse
    it. Does not ingest candles itself -- the first /analysis/{ticker}/refresh
    call does that, same as adding any other symbol.

    Carries over whatever the screener already knows about how to fetch
    candles for this coin, so ingest never has to re-resolve it: a
    GeckoTerminal hit already has its pool pinned down, and a CoinGecko hit
    keeps its coin id for the lazy CoinGecko-platforms-> GeckoTerminal-pool
    lookup if it's ever needed (i.e. not on Binance/KuCoin).

    Keyed by candidate.coin_id, not candidate.symbol: many unrelated coins
    share the same ticker symbol on CoinGecko (e.g. several different "pepe"
    projects), and coin_id is the only field guaranteed unique between them.
    Using the human symbol as the Symbol primary key would silently merge
    two different coins' data into one row on a second promote.

    Uppercased before use as the key: every other endpoint that takes a
    ticker path param (refresh, get_analysis, get_candles, ...) uppercases it
    before looking the Symbol up, so a lowercase coin_id like "balancer"
    stored as-is would never match on the next request -- it'd look
    untracked, get silently recreated as a brand new *stock* Symbol (the
    model's default asset_class), and then fail trying to crawl it from
    vnstock instead of an exchange.
    """
    candidate = session.get(ScreenerCandidate, coin_id)
    if not candidate:
        raise HTTPException(status_code=404, detail="candidate not found")

    symbol_key = candidate.coin_id.upper()
    symbol = session.get(Symbol, symbol_key) or Symbol(ticker=symbol_key)
    symbol.name = candidate.name
    symbol.display_symbol = candidate.symbol.upper()
    symbol.is_watchlist = True
    symbol.asset_class = AssetClass.CRYPTO
    if candidate.source == "geckoterminal":
        symbol.dex_network = candidate.network
        symbol.dex_pool_address = candidate.pool_address
    else:
        symbol.coingecko_id = candidate.coin_id
    session.add(symbol)
    session.commit()

    return {"ticker": symbol_key, "asset_class": AssetClass.CRYPTO}
