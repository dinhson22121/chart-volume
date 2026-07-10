from app.crawler import coingecko_client


def test_fetch_coin_platforms_returns_platform_addresses(mocker):
    body = {"platforms": {"ethereum": "0xabc", "binance-smart-chain": "0xdef"}}
    mocker.patch(
        "httpx.get",
        return_value=mocker.Mock(status_code=200, json=lambda: body, raise_for_status=lambda: None),
    )

    platforms = coingecko_client.fetch_coin_platforms("pepe")

    assert platforms == {"ethereum": "0xabc", "binance-smart-chain": "0xdef"}


def test_fetch_coin_platforms_empty_when_missing(mocker):
    body = {"id": "some-coin"}
    mocker.patch(
        "httpx.get",
        return_value=mocker.Mock(status_code=200, json=lambda: body, raise_for_status=lambda: None),
    )

    assert coingecko_client.fetch_coin_platforms("some-coin") == {}
