"""HOSE+HNX common-stock universe -- the VN-listed-stock analog of crypto
Top100 (app.services.top100).

Unlike VN30 (a small, quarterly-rebalanced index) or Top100 (ranked straight
off one CoinGecko snapshot), the full listed universe is ~700 stocks before
any filtering -- most too thinly traded for Wyckoff's volume-based signals to
mean anything. seed_hose_hnx narrows that down with a liquidity threshold
(settings_service.stock_min_avg_value_vnd) before anything gets tracked.
"""

from __future__ import annotations

import logging

from sqlmodel import Session, select

from app.crawler import vnstock_client
from app.models import AssetClass, Symbol
from app.services import activity_log, settings_service
from app.validation import is_valid_ticker

logger = logging.getLogger("chart_volume.hose_hnx")


def seed_hose_hnx(session: Session, trigger: str = "manual") -> dict:
    """Seeds/refreshes HOSE+HNX membership above the liquidity bar. A stock
    that drops below the bar keeps its Symbol row (it may be on the
    watchlist) but loses the is_hose_hnx flag, mirroring how VN30/Top100
    members are never deleted -- its `exchange` is left as-is, since dropping
    below a liquidity bar isn't the same as delisting."""
    log_id = activity_log.log_action_start(session, "hose_hnx_seed", trigger)
    try:
        universe = vnstock_client.fetch_hose_hnx_universe()
    except vnstock_client.CrawlError as exc:
        activity_log.log_action_finish(session, log_id, "error", str(exc))
        raise

    try:
        liquidity = vnstock_client.fetch_liquidity_snapshot([item["ticker"] for item in universe])
    except vnstock_client.CrawlError as exc:
        activity_log.log_action_finish(session, log_id, "error", str(exc))
        raise

    min_value = settings_service.get_risk_config(session)["stock_min_avg_value_vnd"]

    seeded_keys: set[str] = set()
    for item in universe:
        ticker = item["ticker"]
        # Same bar as top100.seed_top100: third-party-sourced strings that
        # end up in UI/LLM-prompt paths shouldn't be persisted unchecked.
        if not is_valid_ticker(ticker):
            logger.warning("skipping hose/hnx ticker with invalid symbol: %r", ticker)
            continue
        # Missing from the snapshot (e.g. didn't trade today) is "not enough
        # data to qualify", not a free pass -- default to 0 rather than skip
        # the liquidity check entirely.
        if liquidity.get(ticker, 0.0) < min_value:
            continue
        symbol = session.get(Symbol, ticker) or Symbol(ticker=ticker)
        symbol.name = item.get("name") or symbol.name
        symbol.display_symbol = ticker
        symbol.asset_class = AssetClass.STOCK
        symbol.exchange = item["exchange"]
        symbol.is_hose_hnx = True
        session.add(symbol)
        seeded_keys.add(ticker)

    stale = session.exec(select(Symbol).where(Symbol.is_hose_hnx == True)).all()  # noqa: E712
    for symbol in stale:
        if symbol.ticker not in seeded_keys:
            symbol.is_hose_hnx = False
            session.add(symbol)

    session.commit()
    activity_log.log_action_finish(session, log_id, "success", f"{len(seeded_keys)} stock")
    return {"count": len(seeded_keys)}
