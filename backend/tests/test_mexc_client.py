import pandas as pd

from app.crawler import mexc_client


def _kline_row(open_ms, o, h, low, c, v):
    return [open_ms, str(o), str(h), str(low), str(c), str(v), open_ms + 3599999, "0"]


def test_to_pair_builds_usdt_pair():
    assert mexc_client.to_pair("btc") == "BTCUSDT"
    assert mexc_client.to_pair("ETH") == "ETHUSDT"
    assert mexc_client.to_pair("sol", quote="BUSD") == "SOLBUSD"


def test_fetch_klines_maps_1h_to_60m(mocker):
    mock_resp = mocker.Mock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = []
    mock_resp.raise_for_status.return_value = None
    get_spy = mocker.patch("httpx.get", return_value=mock_resp)

    mexc_client.fetch_klines("BTCUSDT", "1h")

    assert get_spy.call_args.kwargs["params"]["interval"] == "60m"


def test_fetch_klines_passes_4h_and_daily_through_unchanged(mocker):
    mock_resp = mocker.Mock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = []
    mock_resp.raise_for_status.return_value = None
    get_spy = mocker.patch("httpx.get", return_value=mock_resp)

    mexc_client.fetch_klines("BTCUSDT", "4h")
    assert get_spy.call_args.kwargs["params"]["interval"] == "4h"

    mexc_client.fetch_klines("BTCUSDT", "daily")
    assert get_spy.call_args.kwargs["params"]["interval"] == "1d"


def test_fetch_klines_parses_rows(mocker):
    rows = [_kline_row(1700000000000, 100, 105, 99, 103, 1234.5)]
    mock_resp = mocker.Mock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = rows
    mock_resp.raise_for_status.return_value = None
    mocker.patch("httpx.get", return_value=mock_resp)

    df = mexc_client.fetch_klines("BTCUSDT", "4h")

    assert list(df.columns) == ["time", "open", "high", "low", "close", "volume"]
    assert len(df) == 1
    assert df.iloc[0]["close"] == 103.0
    assert df.iloc[0]["volume"] == 1234.5


def test_fetch_klines_empty_rows_returns_empty_dataframe(mocker):
    mock_resp = mocker.Mock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = []
    mock_resp.raise_for_status.return_value = None
    mocker.patch("httpx.get", return_value=mock_resp)

    df = mexc_client.fetch_klines("BTCUSDT", "1h")

    assert df.empty
    assert list(df.columns) == ["time", "open", "high", "low", "close", "volume"]


def test_fetch_klines_raises_symbol_not_found_for_invalid_symbol(mocker):
    mock_resp = mocker.Mock()
    mock_resp.status_code = 400
    mock_resp.json.return_value = {"code": -1121, "msg": "Invalid symbol."}
    mocker.patch("httpx.get", return_value=mock_resp)

    try:
        mexc_client.fetch_klines("NOTREALUSDT", "1h")
        assert False, "expected SymbolNotFoundError"
    except mexc_client.SymbolNotFoundError:
        pass


def test_fetch_tradeable_symbols_filters_by_quote_and_spot_allowed(mocker):
    mock_resp = mocker.Mock()
    mock_resp.raise_for_status.return_value = None
    mock_resp.json.return_value = {
        "symbols": [
            {"baseAsset": "BTC", "quoteAsset": "USDT", "isSpotTradingAllowed": True},
            {"baseAsset": "ETH", "quoteAsset": "USDT", "isSpotTradingAllowed": True},
            {"baseAsset": "OLDCOIN", "quoteAsset": "USDT", "isSpotTradingAllowed": False},
            {"baseAsset": "SOL", "quoteAsset": "BTC", "isSpotTradingAllowed": True},  # different quote
        ]
    }
    mocker.patch("httpx.get", return_value=mock_resp)

    symbols = mexc_client.fetch_tradeable_symbols()

    assert symbols == {"BTC", "ETH"}


def test_fetch_klines_retries_transient_failure_then_succeeds(mocker):
    good_resp = mocker.Mock()
    good_resp.status_code = 200
    good_resp.json.return_value = [_kline_row(1700000000000, 1, 2, 0.5, 1.5, 10)]
    good_resp.raise_for_status.return_value = None

    mocker.patch("httpx.get", side_effect=[ConnectionError("boom"), good_resp])
    mocker.patch("time.sleep")

    df = mexc_client.fetch_klines("BTCUSDT", "1d")

    assert len(df) == 1
