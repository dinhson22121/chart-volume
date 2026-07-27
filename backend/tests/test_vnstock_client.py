import pandas as pd
import pytest

from app.crawler import vnstock_client
from app.crawler.vnstock_client import VN30_FALLBACK, CrawlError, fetch_vn30


# --- _with_retry: vnai's own quota guardian raises SystemExit (not a normal
# Exception) when vnstock's free-tier rate limit (20 req/min) is hit -- a
# plain `except Exception` never catches it, so every rate-limit hit used to
# propagate uncaught and crash the caller instead of being retried. ---

def _rate_limit_system_exit() -> SystemExit:
    return SystemExit("Rate limit exceeded. \n============\nGIỚI HẠN API...")


def test_with_retry_waits_the_quota_window_on_rate_limit_then_succeeds(mocker):
    sleep_spy = mocker.patch("app.crawler.vnstock_client.time.sleep")
    calls = [_rate_limit_system_exit(), "ok"]

    def fn():
        result = calls.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result

    result = vnstock_client._with_retry(fn, "daily FPT")

    assert result == "ok"
    sleep_spy.assert_called_once_with(vnstock_client._RATE_LIMIT_WAIT_SECONDS)


def test_with_retry_uses_short_backoff_for_a_non_rate_limit_error(mocker):
    sleep_spy = mocker.patch("app.crawler.vnstock_client.time.sleep")
    calls = [RuntimeError("network blip"), "ok"]

    def fn():
        result = calls.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result

    result = vnstock_client._with_retry(fn, "daily FPT")

    assert result == "ok"
    sleep_spy.assert_called_once_with(vnstock_client._RETRY_BASE_DELAY * 1)


def test_with_retry_raises_crawl_error_after_exhausting_retries_on_rate_limit(mocker):
    mocker.patch("app.crawler.vnstock_client.time.sleep")

    def fn():
        raise _rate_limit_system_exit()

    with pytest.raises(CrawlError):
        vnstock_client._with_retry(fn, "daily FPT")


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


# --- HOSE/HNX universe ---

def test_fetch_hose_hnx_universe_live_filters_board_and_type(mocker):
    # The real getAll endpoint returns every listed instrument (stocks, covered
    # warrants, ETFs, futures, REITs, UPCOM, bonds, delisted) -- only common
    # stocks on the two listed exchanges belong in the tracked universe.
    fake_response = mocker.Mock()
    fake_response.json.return_value = [
        {"symbol": "fpt", "board": "HSX", "type": "STOCK", "organName": "Công ty FPT"},
        {"symbol": "shs", "board": "HNX", "type": "STOCK", "organName": "Công ty SHS"},
        {"symbol": "cvre2605", "board": "HSX", "type": "CW"},
        {"symbol": "fuevn50g", "board": "HSX", "type": "ETF"},
        {"symbol": "ytc", "board": "UPCOM", "type": "STOCK"},
        {"symbol": "", "board": "HSX", "type": "STOCK"},
    ]
    mocker.patch("app.crawler.vnstock_client.requests.get", return_value=fake_response)

    result = vnstock_client._fetch_hose_hnx_universe_live()

    assert result == [
        {"ticker": "FPT", "exchange": "HOSE", "name": "Công ty FPT"},
        {"ticker": "SHS", "exchange": "HNX", "name": "Công ty SHS"},
    ]


def test_fetch_hose_hnx_universe_live_raises_on_empty_data(mocker):
    fake_response = mocker.Mock()
    fake_response.json.return_value = []
    mocker.patch("app.crawler.vnstock_client.requests.get", return_value=fake_response)

    try:
        vnstock_client._fetch_hose_hnx_universe_live()
        assert False, "expected CrawlError"
    except CrawlError:
        pass


def test_fetch_hose_hnx_universe_has_no_static_fallback(mocker):
    # Unlike VN30 (a small, stable index worth hardcoding a fallback for), the
    # full HOSE/HNX universe changes daily (new listings/delistings) -- no
    # fallback list is worth maintaining, so a persistent failure should
    # surface as CrawlError rather than silently degrade to stale data.
    mocker.patch("time.sleep")
    mocker.patch.object(vnstock_client, "_fetch_hose_hnx_universe_live", side_effect=RuntimeError("boom"))

    try:
        vnstock_client.fetch_hose_hnx_universe()
        assert False, "expected CrawlError"
    except CrawlError:
        pass


# --- liquidity snapshot ---

def test_fetch_liquidity_snapshot_converts_million_vnd_to_vnd(mocker):
    # VCI's price-board endpoint reports accumulated_value in millions of VND
    # (empirically confirmed against known daily traded values for large,
    # liquid tickers -- not documented anywhere in the API itself). Normalized
    # to plain VND here so it shares a unit with settings_service's
    # stock_min_avg_value_vnd, rather than leaking an undocumented scale
    # factor into every caller.
    df = pd.DataFrame([{("listing", "symbol"): "fpt", ("match", "accumulated_value"): 269698.67}])
    mock_trading_cls = mocker.patch("app.crawler.vnstock_client.Trading")
    mock_trading_cls.return_value.price_board.return_value = df

    result = vnstock_client.fetch_liquidity_snapshot(["FPT"])

    assert result == {"FPT": 269698.67 * 1_000_000}


def test_fetch_liquidity_snapshot_batches_large_ticker_lists(mocker):
    df_batch1 = pd.DataFrame([{("listing", "symbol"): "a", ("match", "accumulated_value"): 1.0}])
    df_batch2 = pd.DataFrame([{("listing", "symbol"): "b", ("match", "accumulated_value"): 2.0}])
    mock_trading_cls = mocker.patch("app.crawler.vnstock_client.Trading")
    mock_trading_cls.return_value.price_board.side_effect = [df_batch1, df_batch2]

    result = vnstock_client.fetch_liquidity_snapshot(["A", "B"], batch_size=1)

    assert result == {"A": 1_000_000.0, "B": 2_000_000.0}
    assert mock_trading_cls.return_value.price_board.call_count == 2


def test_fetch_liquidity_snapshot_isolates_a_failing_batch_from_the_rest(mocker):
    # A single batch's transient failure (network timeout, unofficial API
    # hiccup) previously aborted the WHOLE snapshot -- losing liquidity data
    # for every other batch too, not just the failed one. That in turn made
    # hose_hnx.seed_hose_hnx (and anything that calls it, e.g.
    # stock_batch_analysis.run_full_universe_analysis) fail entirely before
    # analysing a single ticker. One bad batch should only cost that batch's
    # own tickers (same "missing from snapshot -> not enough data to
    # qualify" semantics seed_hose_hnx already applies via its 0.0 fallback).
    mocker.patch("app.crawler.vnstock_client.time.sleep")
    df_batch2 = pd.DataFrame([{("listing", "symbol"): "b", ("match", "accumulated_value"): 2.0}])
    mock_trading_cls = mocker.patch("app.crawler.vnstock_client.Trading")
    mock_trading_cls.return_value.price_board.side_effect = [RuntimeError("timed out")] * 3 + [df_batch2]

    result = vnstock_client.fetch_liquidity_snapshot(["A", "B"], batch_size=1)

    assert result == {"B": 2_000_000.0}  # A's batch failed and was skipped, B's still made it in


def test_fetch_liquidity_snapshot_empty_tickers_returns_empty_dict():
    assert vnstock_client.fetch_liquidity_snapshot([]) == {}
