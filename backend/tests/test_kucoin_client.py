from app.crawler import kucoin_client


def _row(time_s, open_, close, high, low, vol):
    return [str(time_s), str(open_), str(close), str(high), str(low), str(vol), "0"]


def test_to_pair_builds_dash_usdt_pair():
    assert kucoin_client.to_pair("btc") == "BTC-USDT"
    assert kucoin_client.to_pair("eth") == "ETH-USDT"


def test_fetch_klines_parses_and_reorders_columns(mocker):
    # KuCoin returns [time, open, close, high, low, volume, turnover], most-recent-first.
    rows = [
        _row(1700010000, 101, 103, 104, 100, 50),
        _row(1700000000, 100, 101, 102, 99, 40),
    ]
    mock_resp = mocker.Mock()
    mock_resp.raise_for_status.return_value = None
    mock_resp.json.return_value = {"code": "200000", "data": rows}
    mocker.patch("httpx.get", return_value=mock_resp)

    df = kucoin_client.fetch_klines("BTC-USDT", "1h")

    assert list(df.columns) == ["time", "open", "high", "low", "close", "volume"]
    # oldest first after our own sort, regardless of KuCoin's descending order
    assert df.iloc[0]["open"] == 100.0
    assert df.iloc[0]["close"] == 101.0
    assert df.iloc[0]["high"] == 102.0
    assert df.iloc[0]["low"] == 99.0
    assert df.iloc[-1]["open"] == 101.0


def test_fetch_klines_raises_symbol_not_found_on_error_code(mocker):
    mock_resp = mocker.Mock()
    mock_resp.raise_for_status.return_value = None
    mock_resp.json.return_value = {"msg": "Unsupported trading pair.", "code": "400100"}
    mocker.patch("httpx.get", return_value=mock_resp)

    try:
        kucoin_client.fetch_klines("NOTREAL-USDT", "1h")
        assert False, "expected SymbolNotFoundError"
    except kucoin_client.SymbolNotFoundError:
        pass


def test_fetch_klines_maps_internal_interval_names(mocker):
    mock_resp = mocker.Mock()
    mock_resp.raise_for_status.return_value = None
    mock_resp.json.return_value = {"code": "200000", "data": []}
    get_spy = mocker.patch("httpx.get", return_value=mock_resp)

    kucoin_client.fetch_klines("BTC-USDT", "daily")

    assert get_spy.call_args.kwargs["params"]["type"] == "1day"


def test_fetch_tradeable_symbols_filters_by_quote_and_enabled(mocker):
    mock_resp = mocker.Mock()
    mock_resp.raise_for_status.return_value = None
    mock_resp.json.return_value = {
        "code": "200000",
        "data": [
            {"baseCurrency": "BTC", "quoteCurrency": "USDT", "enableTrading": True},
            {"baseCurrency": "ETH", "quoteCurrency": "USDT", "enableTrading": True},
            {"baseCurrency": "DEAD", "quoteCurrency": "USDT", "enableTrading": False},
            {"baseCurrency": "SOL", "quoteCurrency": "BTC", "enableTrading": True},
        ],
    }
    mocker.patch("httpx.get", return_value=mock_resp)

    symbols = kucoin_client.fetch_tradeable_symbols()

    assert symbols == {"BTC", "ETH"}


def test_fetch_tradeable_symbols_raises_on_error_code(mocker):
    mock_resp = mocker.Mock()
    mock_resp.raise_for_status.return_value = None
    mock_resp.json.return_value = {"code": "400001", "msg": "boom"}
    mocker.patch("httpx.get", return_value=mock_resp)

    try:
        kucoin_client.fetch_tradeable_symbols()
        assert False, "expected CrawlError"
    except kucoin_client.CrawlError:
        pass


def test_fetch_klines_retries_transient_failure_then_succeeds(mocker):
    good_resp = mocker.Mock()
    good_resp.raise_for_status.return_value = None
    good_resp.json.return_value = {"code": "200000", "data": [_row(1700000000, 1, 1.5, 2, 0.5, 10)]}

    mocker.patch("httpx.get", side_effect=[ConnectionError("boom"), good_resp])
    mocker.patch("time.sleep")

    df = kucoin_client.fetch_klines("BTC-USDT", "4h")

    assert len(df) == 1
