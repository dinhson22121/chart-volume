from app.crawler import geckoterminal_client


def _pool(address, network, base_token_id, market_cap_usd=None, fdv_usd=None, volume_h24="1000"):
    return {
        "attributes": {
            "address": address,
            "market_cap_usd": market_cap_usd,
            "fdv_usd": fdv_usd,
            "volume_usd": {"h24": volume_h24},
        },
        "relationships": {
            "network": {"data": {"id": network}},
            "base_token": {"data": {"id": base_token_id}},
        },
    }


def _token(token_id, address, symbol, name, coingecko_coin_id=None):
    return {
        "id": token_id,
        "type": "token",
        "attributes": {
            "address": address,
            "symbol": symbol,
            "name": name,
            "coingecko_coin_id": coingecko_coin_id,
        },
    }


def test_fetch_new_pools_normalizes_and_uses_market_cap(mocker):
    body = {
        "data": [_pool("0xpool1", "eth", "eth_0xtoken1", market_cap_usd="5000000")],
        "included": [_token("eth_0xtoken1", "0xtoken1", "altx", "Alt X", "altcoin-x")],
    }
    mocker.patch("httpx.get", return_value=mocker.Mock(status_code=200, json=lambda: body, raise_for_status=lambda: None))

    result = geckoterminal_client.fetch_new_pools(page=1)

    assert result == [
        {
            "network": "eth",
            "pool_address": "0xpool1",
            "symbol": "altx",
            "name": "Alt X",
            "coingecko_coin_id": "altcoin-x",
            "market_cap": 5000000.0,
            "volume_24h": 1000.0,
        }
    ]


def test_fetch_new_pools_falls_back_to_fdv_when_market_cap_missing(mocker):
    body = {
        "data": [_pool("0xpool2", "solana", "solana_addr2", market_cap_usd=None, fdv_usd="3294.84")],
        "included": [_token("solana_addr2", "addr2", "newcoin", "New Coin")],
    }
    mocker.patch("httpx.get", return_value=mocker.Mock(status_code=200, json=lambda: body, raise_for_status=lambda: None))

    result = geckoterminal_client.fetch_trending_pools(page=1)

    assert result[0]["market_cap"] == 3294.84


def test_fetch_new_pools_skips_pools_with_no_market_cap_or_fdv(mocker):
    body = {
        "data": [_pool("0xpool3", "eth", "eth_addr3", market_cap_usd=None, fdv_usd=None)],
        "included": [_token("eth_addr3", "addr3", "junk", "Junk")],
    }
    mocker.patch("httpx.get", return_value=mocker.Mock(status_code=200, json=lambda: body, raise_for_status=lambda: None))

    result = geckoterminal_client.fetch_new_pools(page=1)

    assert result == []


def test_fetch_new_pools_skips_pools_missing_token_info(mocker):
    body = {
        "data": [_pool("0xpool4", "eth", "eth_unknown", market_cap_usd="1000")],
        "included": [],  # base_token not in included -> can't resolve address
    }
    mocker.patch("httpx.get", return_value=mocker.Mock(status_code=200, json=lambda: body, raise_for_status=lambda: None))

    result = geckoterminal_client.fetch_new_pools(page=1)

    assert result == []


def test_fetch_token_pools_picks_highest_reserve(mocker):
    body = {
        "data": [
            {"attributes": {"address": "0xlow", "reserve_in_usd": "1000"}},
            {"attributes": {"address": "0xhigh", "reserve_in_usd": "50000"}},
        ]
    }
    mocker.patch("httpx.get", return_value=mocker.Mock(status_code=200, json=lambda: body, raise_for_status=lambda: None))

    result = geckoterminal_client.fetch_token_pools("eth", "0x" + "a" * 40)

    assert result == "0xhigh"


def test_fetch_token_pools_returns_none_when_no_pools(mocker):
    body = {"data": []}
    mocker.patch("httpx.get", return_value=mocker.Mock(status_code=200, json=lambda: body, raise_for_status=lambda: None))

    result = geckoterminal_client.fetch_token_pools("eth", "0x" + "a" * 40)

    assert result is None


def test_fetch_token_pools_rejects_malformed_evm_address(mocker):
    get_spy = mocker.patch("httpx.get")

    result = geckoterminal_client.fetch_token_pools("eth", "not-an-address")

    assert result is None
    get_spy.assert_not_called()  # rejected before any request was made


def test_fetch_token_pools_rejects_evm_address_on_solana_network(mocker):
    get_spy = mocker.patch("httpx.get")

    result = geckoterminal_client.fetch_token_pools("solana", "0x" + "a" * 40)

    assert result is None
    get_spy.assert_not_called()


def test_fetch_token_pools_accepts_valid_solana_address(mocker):
    body = {"data": [{"attributes": {"address": "0xpool", "reserve_in_usd": "1000"}}]}
    mocker.patch("httpx.get", return_value=mocker.Mock(status_code=200, json=lambda: body, raise_for_status=lambda: None))

    result = geckoterminal_client.fetch_token_pools("solana", "So11111111111111111111111111111111111111112")

    assert result == "0xpool"


def test_fetch_ohlcv_parses_and_sorts_ascending(mocker):
    body = {
        "data": {
            "attributes": {
                "ohlcv_list": [
                    [1700010000, 101, 104, 100, 103, 50],  # newest first, as GeckoTerminal returns
                    [1700000000, 100, 102, 99, 101, 40],
                ]
            }
        }
    }
    mocker.patch("httpx.get", return_value=mocker.Mock(status_code=200, json=lambda: body, raise_for_status=lambda: None))

    df = geckoterminal_client.fetch_ohlcv("eth", "0xpool", "4h")

    assert list(df.columns) == ["time", "open", "high", "low", "close", "volume"]
    assert df.iloc[0]["open"] == 100.0  # oldest first after sort
    assert df.iloc[-1]["open"] == 101.0


def test_fetch_ohlcv_maps_timeframe_to_unit_and_aggregate(mocker):
    body = {"data": {"attributes": {"ohlcv_list": []}}}
    get_spy = mocker.patch(
        "httpx.get", return_value=mocker.Mock(status_code=200, json=lambda: body, raise_for_status=lambda: None)
    )

    geckoterminal_client.fetch_ohlcv("eth", "0xpool", "4h")

    called_url = get_spy.call_args.args[0]
    assert "/ohlcv/hour" in called_url
    assert get_spy.call_args.kwargs["params"]["aggregate"] == 4


def test_get_retries_on_429_then_succeeds(mocker):
    body = {"data": []}
    rate_limited = mocker.Mock(status_code=429)
    ok = mocker.Mock(status_code=200, json=lambda: body, raise_for_status=lambda: None)
    mocker.patch("httpx.get", side_effect=[rate_limited, ok])
    mocker.patch("time.sleep")

    result = geckoterminal_client.fetch_new_pools(page=1)

    assert result == []
