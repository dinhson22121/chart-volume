"""_tradeable_symbol_exchanges() itself -- kept in its own file so
test_crypto_screener.py's autouse fixture (which stubs the same function to
keep the mcap/volume tests from hitting the network) doesn't shadow the real
function under test here."""

from app.services import crypto_screener


def test_tradeable_symbol_exchanges_combines_enabled_exchanges(mocker):
    mocker.patch.object(crypto_screener.binance_client, "fetch_tradeable_symbols", return_value={"BTC", "ETH"})
    mocker.patch.object(crypto_screener.kucoin_client, "fetch_tradeable_symbols", return_value={"ETH", "ALTX"})
    mocker.patch.object(crypto_screener.mexc_client, "fetch_tradeable_symbols", return_value=set())

    result = crypto_screener._tradeable_symbol_exchanges(("binance", "kucoin"))

    assert result == {"BTC": "binance", "ETH": "binance", "ALTX": "kucoin"}


def test_tradeable_symbol_exchanges_only_queries_enabled_exchanges(mocker):
    binance_spy = mocker.patch.object(
        crypto_screener.binance_client, "fetch_tradeable_symbols", return_value={"BTC"}
    )
    kucoin_spy = mocker.patch.object(crypto_screener.kucoin_client, "fetch_tradeable_symbols")
    mexc_spy = mocker.patch.object(crypto_screener.mexc_client, "fetch_tradeable_symbols")

    result = crypto_screener._tradeable_symbol_exchanges(("binance",))

    assert result == {"BTC": "binance"}
    kucoin_spy.assert_not_called()
    mexc_spy.assert_not_called()
    binance_spy.assert_called_once()


def test_tradeable_symbol_exchanges_prefers_binance_over_kucoin_and_mexc(mocker):
    # A symbol listed on multiple exchanges must resolve to whichever one
    # ingest_crypto would actually try first (Binance > KuCoin > MEXC).
    mocker.patch.object(crypto_screener.binance_client, "fetch_tradeable_symbols", return_value={"BTC"})
    mocker.patch.object(crypto_screener.kucoin_client, "fetch_tradeable_symbols", return_value={"BTC"})
    mocker.patch.object(crypto_screener.mexc_client, "fetch_tradeable_symbols", return_value={"BTC"})

    result = crypto_screener._tradeable_symbol_exchanges(("binance", "kucoin", "mexc"))

    assert result == {"BTC": "binance"}


def test_tradeable_symbol_exchanges_returns_none_when_all_exchanges_fail(mocker):
    mocker.patch.object(
        crypto_screener.binance_client, "fetch_tradeable_symbols", side_effect=RuntimeError("boom")
    )
    mocker.patch.object(
        crypto_screener.kucoin_client, "fetch_tradeable_symbols", side_effect=RuntimeError("boom")
    )

    assert crypto_screener._tradeable_symbol_exchanges(("binance", "kucoin")) is None
