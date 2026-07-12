"""Top-100-by-market-cap crypto list -- the crypto analog of the VN30 seed.

One CoinGecko /coins/markets call (page 1, already sorted market_cap_desc)
covers the whole list, so unlike the screener there is no pagination or
rate-limit pacing to worry about. Lives in a service (not inline in the
symbols router like seed_vn30) because both the manual endpoint and the
scheduled top100_refresh cron job call it.
"""

from __future__ import annotations

import logging

from sqlmodel import Session, select

from app.crawler import coingecko_client
from app.models import AssetClass, Symbol
from app.services import activity_log
from app.validation import is_valid_ticker

logger = logging.getLogger("chart_volume.top100")

TOP_N = 100


def seed_top100(session: Session, trigger: str = "manual") -> dict:
    """Seeds/refreshes top-100 membership. Coins that dropped out of the top
    100 keep their Symbol row (they may be on the watchlist) but lose the
    is_top100 flag, mirroring how VN30 members are never deleted."""
    log_id = activity_log.log_action_start(session, "top100_seed", trigger)
    try:
        coins = coingecko_client.fetch_markets_page(1)[:TOP_N]
    except coingecko_client.CrawlError as exc:
        activity_log.log_action_finish(session, log_id, "error", str(exc))
        raise

    seeded_keys: set[str] = set()
    for rank, coin in enumerate(coins, start=1):
        coin_id = (coin.get("id") or "").strip()
        symbol_key = coin_id.upper()
        display_symbol = (coin.get("symbol") or "").strip().upper()
        # Same bar as promote_candidate: these are third-party-sourced strings
        # that end up as the ticker passed into the LLM prompt -- skip anything
        # that doesn't look like a real ticker instead of persisting it.
        if not is_valid_ticker(symbol_key) or not is_valid_ticker(display_symbol):
            logger.warning("skipping top100 coin with invalid id/symbol: %r", coin_id)
            continue
        symbol = session.get(Symbol, symbol_key) or Symbol(ticker=symbol_key)
        symbol.name = coin.get("name") or symbol.name
        symbol.display_symbol = display_symbol
        symbol.asset_class = AssetClass.CRYPTO
        symbol.coingecko_id = coin_id
        symbol.is_top100 = True
        symbol.top100_rank = rank
        session.add(symbol)
        seeded_keys.add(symbol_key)

    stale = session.exec(select(Symbol).where(Symbol.is_top100 == True)).all()  # noqa: E712
    for symbol in stale:
        if symbol.ticker not in seeded_keys:
            symbol.is_top100 = False
            symbol.top100_rank = None
            session.add(symbol)

    session.commit()
    activity_log.log_action_finish(session, log_id, "success", f"{len(seeded_keys)} coin")
    return {"count": len(seeded_keys)}
