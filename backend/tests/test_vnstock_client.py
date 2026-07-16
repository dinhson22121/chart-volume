from app.crawler import vnstock_client
from app.crawler.vnstock_client import VN30_FALLBACK, CrawlError, fetch_vn30


def test_fetch_vn30_live_normalizes_and_drops_blanks(mocker):
    # The real VCI endpoint returns a list of {"symbol": ...} dicts (verified
    # against the live endpoint directly) -- confirms _fetch_vn30_live's own
    # uppercase/blank-dropping, independent of the requests.get() call.
    fake_response = mocker.Mock()
    fake_response.json.return_value = [{"symbol": "fpt"}, {"symbol": "hpg"}, {"symbol": ""}]
    mocker.patch("app.crawler.vnstock_client.requests.get", return_value=fake_response)

    assert vnstock_client._fetch_vn30_live() == ["FPT", "HPG"]


def test_fetch_vn30_live_raises_crawl_error_on_empty_data(mocker):
    fake_response = mocker.Mock()
    fake_response.json.return_value = []
    mocker.patch("app.crawler.vnstock_client.requests.get", return_value=fake_response)

    try:
        vnstock_client._fetch_vn30_live()
        assert False, "expected CrawlError"
    except CrawlError:
        pass


def test_fetch_vn30_returns_live_tickers_and_source(mocker):
    mocker.patch("time.sleep")
    mocker.patch.object(vnstock_client, "_fetch_vn30_live", return_value=["FPT", "HPG"])

    tickers, source = fetch_vn30()

    assert tickers == ["FPT", "HPG"]
    assert source == "live"


def test_fetch_vn30_falls_back_when_live_fetch_raises(mocker):
    mocker.patch("time.sleep")
    mocker.patch.object(vnstock_client, "_fetch_vn30_live", side_effect=RuntimeError("boom"))

    tickers, source = fetch_vn30()

    assert tickers == list(VN30_FALLBACK)
    assert source == "fallback"


def test_fetch_vn30_falls_back_when_live_fetch_returns_empty(mocker):
    mocker.patch("time.sleep")
    mocker.patch.object(vnstock_client, "_fetch_vn30_live", return_value=[])

    tickers, source = fetch_vn30()

    assert tickers == list(VN30_FALLBACK)
    assert source == "fallback"
