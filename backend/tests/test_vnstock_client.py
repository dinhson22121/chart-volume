from app.crawler import vnstock_client
from app.crawler.vnstock_client import VN30_FALLBACK, fetch_vn30


def test_fetch_vn30_returns_live_tickers_and_source(mocker):
    mocker.patch("time.sleep")
    mocker.patch.object(
        vnstock_client.Listing, "symbols_by_group", return_value=["fpt", "hpg", ""]
    )

    tickers, source = fetch_vn30()

    assert tickers == ["FPT", "HPG"]  # uppercased, blanks dropped
    assert source == "live"


def test_fetch_vn30_falls_back_when_live_fetch_raises(mocker):
    mocker.patch("time.sleep")
    mocker.patch.object(
        vnstock_client.Listing, "symbols_by_group", side_effect=RuntimeError("boom")
    )

    tickers, source = fetch_vn30()

    assert tickers == list(VN30_FALLBACK)
    assert source == "fallback"


def test_fetch_vn30_falls_back_when_live_fetch_returns_empty(mocker):
    mocker.patch("time.sleep")
    mocker.patch.object(vnstock_client.Listing, "symbols_by_group", return_value=[])

    tickers, source = fetch_vn30()

    assert tickers == list(VN30_FALLBACK)
    assert source == "fallback"
