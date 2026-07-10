"""_tradeable_symbols() itself -- kept in its own file so test_crypto_screener.py's
autouse fixture (which stubs _tradeable_symbols to keep the mcap/volume tests
from hitting the network) doesn't shadow the real function under test here."""

from app.services import crypto_screener


def test_tradeable_symbols_combines_enabled_exchanges(mocker):
    mocker.patch.object(crypto_screener.binance_client, "fetch_tradeable_symbols", return_value={"BTC", "ETH"})
    mocker.patch.object(crypto_screener.kucoin_client, "fetch_tradeable_symbols", return_value={"ETH", "ALTX"})

    result = crypto_screener._tradeable_symbols(("binance", "kucoin"))

    assert result == {"BTC", "ETH", "ALTX"}


def test_tradeable_symbols_only_queries_enabled_exchanges(mocker):
    binance_spy = mocker.patch.object(
        crypto_screener.binance_client, "fetch_tradeable_symbols", return_value={"BTC"}
    )
    kucoin_spy = mocker.patch.object(crypto_screener.kucoin_client, "fetch_tradeable_symbols")

    result = crypto_screener._tradeable_symbols(("binance",))

    assert result == {"BTC"}
    kucoin_spy.assert_not_called()
    binance_spy.assert_called_once()


def test_tradeable_symbols_returns_none_when_all_exchanges_fail(mocker):
    mocker.patch.object(
        crypto_screener.binance_client, "fetch_tradeable_symbols", side_effect=RuntimeError("boom")
    )
    mocker.patch.object(
        crypto_screener.kucoin_client, "fetch_tradeable_symbols", side_effect=RuntimeError("boom")
    )

    assert crypto_screener._tradeable_symbols(("binance", "kucoin")) is None
