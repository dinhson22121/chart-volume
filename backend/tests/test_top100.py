from sqlmodel import select

import pytest

from app.crawler import coingecko_client
from app.models import AssetClass, Symbol, SystemActionLog
from app.services import top100


def _coin(coin_id: str, symbol: str, name: str = "") -> dict:
    return {"id": coin_id, "symbol": symbol, "name": name or coin_id.title()}


def test_seed_creates_symbols_ranked_by_market_cap_order(session, mocker):
    mocker.patch.object(
        coingecko_client, "fetch_markets_page",
        return_value=[_coin("bitcoin", "btc"), _coin("ethereum", "eth"), _coin("tether", "usdt")],
    )

    result = top100.seed_top100(session)

    assert result == {"count": 3}
    btc = session.get(Symbol, "BITCOIN")
    assert btc.is_top100 is True
    assert btc.top100_rank == 1
    assert btc.display_symbol == "BTC"
    assert btc.asset_class == AssetClass.CRYPTO
    assert btc.coingecko_id == "bitcoin"
    assert session.get(Symbol, "TETHER").top100_rank == 3


def test_seed_takes_at_most_top_n_coins(session, mocker):
    coins = [_coin(f"coin-{i}", f"c{i}") for i in range(150)]
    mocker.patch.object(coingecko_client, "fetch_markets_page", return_value=coins)

    result = top100.seed_top100(session)

    assert result == {"count": 100}
    assert session.get(Symbol, "COIN-99") is not None
    assert session.get(Symbol, "COIN-100") is None


def test_reseed_clears_flag_on_coins_that_dropped_out(session, mocker):
    fetch = mocker.patch.object(
        coingecko_client, "fetch_markets_page",
        return_value=[_coin("bitcoin", "btc"), _coin("dropped-coin", "drp")],
    )
    top100.seed_top100(session)

    fetch.return_value = [_coin("bitcoin", "btc"), _coin("newcomer", "new")]
    top100.seed_top100(session)

    dropped = session.get(Symbol, "DROPPED-COIN")
    assert dropped is not None  # row kept, only the flag cleared
    assert dropped.is_top100 is False
    assert dropped.top100_rank is None
    assert session.get(Symbol, "NEWCOMER").top100_rank == 2


def test_seed_preserves_watchlist_flag_on_existing_symbol(session, mocker):
    session.add(
        Symbol(ticker="BITCOIN", display_symbol="BTC", asset_class=AssetClass.CRYPTO, is_watchlist=True)
    )
    session.commit()
    mocker.patch.object(coingecko_client, "fetch_markets_page", return_value=[_coin("bitcoin", "btc")])

    top100.seed_top100(session)

    btc = session.get(Symbol, "BITCOIN")
    assert btc.is_watchlist is True
    assert btc.is_top100 is True


def test_seed_skips_coins_with_invalid_id_or_symbol(session, mocker):
    mocker.patch.object(
        coingecko_client, "fetch_markets_page",
        return_value=[
            _coin("bitcoin", "btc"),
            _coin("evil coin id", "evil"),  # space in id
            _coin("sneaky", "IGNORE ALL INSTRUCTIONS"),  # injection-shaped symbol
            {"id": None, "symbol": "x"},  # missing id
        ],
    )

    result = top100.seed_top100(session)

    assert result == {"count": 1}
    assert session.get(Symbol, "SNEAKY") is None


def test_seed_writes_activity_log_with_trigger(session, mocker):
    mocker.patch.object(coingecko_client, "fetch_markets_page", return_value=[_coin("bitcoin", "btc")])

    top100.seed_top100(session, trigger="scheduled")

    entry = session.exec(select(SystemActionLog)).one()
    assert entry.action == "top100_seed"
    assert entry.trigger == "scheduled"
    assert entry.status == "success"
    assert entry.detail == "1 coin"


def test_seed_logs_error_and_reraises_on_crawl_failure(session, mocker):
    mocker.patch.object(
        coingecko_client, "fetch_markets_page",
        side_effect=coingecko_client.CrawlError("rate limited"),
    )

    with pytest.raises(coingecko_client.CrawlError):
        top100.seed_top100(session)

    entry = session.exec(select(SystemActionLog)).one()
    assert entry.status == "error"
    assert "rate limited" in entry.detail
