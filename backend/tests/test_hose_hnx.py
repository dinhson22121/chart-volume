from sqlmodel import select

import pytest

from app.crawler import vnstock_client
from app.models import AssetClass, Exchange, Symbol, SystemActionLog
from app.services import hose_hnx, settings_service


def _stock(ticker: str, exchange: str, name: str = "") -> dict:
    return {"ticker": ticker, "exchange": exchange, "name": name or f"{ticker} Corp"}


def test_seed_creates_symbols_above_the_liquidity_bar(session, mocker):
    mocker.patch.object(
        vnstock_client, "fetch_hose_hnx_universe",
        return_value=[_stock("FPT", Exchange.HOSE), _stock("SHS", Exchange.HNX)],
    )
    mocker.patch.object(
        vnstock_client, "fetch_liquidity_snapshot",
        return_value={"FPT": 2_000_000_000.0, "SHS": 500_000_000.0},
    )
    settings_service.update(session, {"stock_min_avg_value_vnd": "1000000000"})

    result = hose_hnx.seed_hose_hnx(session)

    assert result == {"count": 1}
    fpt = session.get(Symbol, "FPT")
    assert fpt.is_hose_hnx is True
    assert fpt.exchange == Exchange.HOSE
    assert fpt.asset_class == AssetClass.STOCK
    assert fpt.display_symbol == "FPT"
    assert fpt.name == "FPT Corp"
    assert session.get(Symbol, "SHS") is None  # below the liquidity bar, never created


def test_seed_excludes_tickers_missing_from_the_liquidity_snapshot(session, mocker):
    # A ticker the universe fetch returned but the liquidity fetch has no data
    # for (e.g. didn't trade today) is treated as "not enough data", not a
    # free pass.
    mocker.patch.object(vnstock_client, "fetch_hose_hnx_universe", return_value=[_stock("FPT", Exchange.HOSE)])
    mocker.patch.object(vnstock_client, "fetch_liquidity_snapshot", return_value={})

    result = hose_hnx.seed_hose_hnx(session)

    assert result == {"count": 0}
    assert session.get(Symbol, "FPT") is None


def test_reseed_clears_flag_but_keeps_exchange_on_stocks_that_drop_below_bar(session, mocker):
    fetch_universe = mocker.patch.object(
        vnstock_client, "fetch_hose_hnx_universe",
        return_value=[_stock("FPT", Exchange.HOSE), _stock("SHS", Exchange.HNX)],
    )
    mocker.patch.object(
        vnstock_client, "fetch_liquidity_snapshot",
        return_value={"FPT": 2_000_000_000.0, "SHS": 2_000_000_000.0},
    )
    hose_hnx.seed_hose_hnx(session)

    fetch_universe.return_value = [_stock("FPT", Exchange.HOSE)]  # SHS dropped out
    hose_hnx.seed_hose_hnx(session)

    shs = session.get(Symbol, "SHS")
    assert shs is not None  # row kept
    assert shs.is_hose_hnx is False
    assert shs.exchange == Exchange.HNX  # exchange membership itself doesn't change


def test_seed_preserves_watchlist_and_vn30_flags_on_existing_symbol(session, mocker):
    session.add(Symbol(ticker="FPT", asset_class=AssetClass.STOCK, is_watchlist=True, is_vn30=True))
    session.commit()
    mocker.patch.object(vnstock_client, "fetch_hose_hnx_universe", return_value=[_stock("FPT", Exchange.HOSE)])
    mocker.patch.object(vnstock_client, "fetch_liquidity_snapshot", return_value={"FPT": 2_000_000_000.0})

    hose_hnx.seed_hose_hnx(session)

    fpt = session.get(Symbol, "FPT")
    assert fpt.is_watchlist is True
    assert fpt.is_vn30 is True
    assert fpt.is_hose_hnx is True


def test_seed_skips_tickers_with_invalid_symbol(session, mocker):
    mocker.patch.object(
        vnstock_client, "fetch_hose_hnx_universe",
        return_value=[_stock("FPT", Exchange.HOSE), {"ticker": "BAD SYMBOL", "exchange": Exchange.HOSE, "name": "x"}],
    )
    mocker.patch.object(
        vnstock_client, "fetch_liquidity_snapshot",
        return_value={"FPT": 2_000_000_000.0, "BAD SYMBOL": 2_000_000_000.0},
    )

    result = hose_hnx.seed_hose_hnx(session)

    assert result == {"count": 1}
    assert session.get(Symbol, "BAD SYMBOL") is None


def test_seed_writes_activity_log_with_trigger(session, mocker):
    mocker.patch.object(vnstock_client, "fetch_hose_hnx_universe", return_value=[_stock("FPT", Exchange.HOSE)])
    mocker.patch.object(vnstock_client, "fetch_liquidity_snapshot", return_value={"FPT": 2_000_000_000.0})

    hose_hnx.seed_hose_hnx(session, trigger="scheduled")

    entry = session.exec(select(SystemActionLog)).one()
    assert entry.action == "hose_hnx_seed"
    assert entry.trigger == "scheduled"
    assert entry.status == "success"
    assert entry.detail == "1 stock"


def test_seed_logs_error_and_reraises_on_universe_crawl_failure(session, mocker):
    mocker.patch.object(
        vnstock_client, "fetch_hose_hnx_universe", side_effect=vnstock_client.CrawlError("rate limited"),
    )

    with pytest.raises(vnstock_client.CrawlError):
        hose_hnx.seed_hose_hnx(session)

    entry = session.exec(select(SystemActionLog)).one()
    assert entry.status == "error"
    assert "rate limited" in entry.detail


def test_seed_logs_error_and_reraises_on_liquidity_crawl_failure(session, mocker):
    mocker.patch.object(vnstock_client, "fetch_hose_hnx_universe", return_value=[_stock("FPT", Exchange.HOSE)])
    mocker.patch.object(
        vnstock_client, "fetch_liquidity_snapshot", side_effect=vnstock_client.CrawlError("timeout"),
    )

    with pytest.raises(vnstock_client.CrawlError):
        hose_hnx.seed_hose_hnx(session)

    entry = session.exec(select(SystemActionLog)).one()
    assert entry.status == "error"
    assert "timeout" in entry.detail
    assert session.get(Symbol, "FPT") is None  # nothing committed on failure
