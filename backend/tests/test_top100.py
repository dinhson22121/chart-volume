from sqlmodel import select

import pytest

from app.crawler import coingecko_client
from app.models import AssetClass, Symbol, SystemActionLog
from app.services import top100


def _coin(coin_id: str, symbol: str, name: str = "") -> dict:
    return {"id": coin_id, "symbol": symbol, "name": name or coin_id.title()}


def _mock_no_stablecoins(mocker):
    return mocker.patch.object(coingecko_client, "fetch_stablecoin_ids", return_value=set())


def test_seed_creates_symbols_ranked_by_market_cap_order(session, mocker):
    _mock_no_stablecoins(mocker)
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
    _mock_no_stablecoins(mocker)
    coins = [_coin(f"coin-{i}", f"c{i}") for i in range(150)]
    mocker.patch.object(coingecko_client, "fetch_markets_page", return_value=coins)

    result = top100.seed_top100(session)

    assert result == {"count": 100}
    assert session.get(Symbol, "COIN-99") is not None
    assert session.get(Symbol, "COIN-100") is None


def test_reseed_clears_flag_on_coins_that_dropped_out(session, mocker):
    _mock_no_stablecoins(mocker)
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
    _mock_no_stablecoins(mocker)
    mocker.patch.object(coingecko_client, "fetch_markets_page", return_value=[_coin("bitcoin", "btc")])

    top100.seed_top100(session)

    btc = session.get(Symbol, "BITCOIN")
    assert btc.is_watchlist is True
    assert btc.is_top100 is True


def test_seed_skips_coins_with_invalid_id_or_symbol(session, mocker):
    _mock_no_stablecoins(mocker)
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
    _mock_no_stablecoins(mocker)
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


def test_seed_excludes_stablecoins(session, mocker):
    mocker.patch.object(
        coingecko_client, "fetch_markets_page",
        return_value=[_coin("bitcoin", "btc"), _coin("tether", "usdt"), _coin("usd-coin", "usdc")],
    )
    mocker.patch.object(coingecko_client, "fetch_stablecoin_ids", return_value={"tether", "usd-coin"})

    result = top100.seed_top100(session)

    assert result == {"count": 1}
    assert session.get(Symbol, "BITCOIN") is not None
    assert session.get(Symbol, "TETHER") is None
    assert session.get(Symbol, "USD-COIN") is None


def test_seed_excludes_stablecoins_before_slicing_to_top_n(session, mocker):
    # A stablecoin sitting inside the raw top-100 window must not push a real
    # rank-100 asset out -- filter, then slice, not the other way around.
    coins = [_coin("tether", "usdt")] + [_coin(f"coin-{i}", f"c{i}") for i in range(150)]
    mocker.patch.object(coingecko_client, "fetch_markets_page", return_value=coins)
    mocker.patch.object(coingecko_client, "fetch_stablecoin_ids", return_value={"tether"})

    result = top100.seed_top100(session)

    assert result == {"count": 100}
    assert session.get(Symbol, "TETHER") is None
    assert session.get(Symbol, "COIN-99") is not None  # still makes the cut


def test_seed_degrades_gracefully_when_stablecoin_lookup_fails(session, mocker):
    mocker.patch.object(
        coingecko_client, "fetch_markets_page",
        return_value=[_coin("bitcoin", "btc"), _coin("tether", "usdt")],
    )
    mocker.patch.object(
        coingecko_client, "fetch_stablecoin_ids",
        side_effect=coingecko_client.CrawlError("rate limited"),
    )

    result = top100.seed_top100(session)

    # Filter lookup failed -- the refresh still completes rather than
    # aborting, just without filtering this run.
    assert result == {"count": 2}
    assert session.get(Symbol, "TETHER") is not None
