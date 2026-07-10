import pytest
from sqlmodel import select

from app.models import CryptoVolumeSnapshot, ScreenerCandidate
from app.services import crypto_screener


@pytest.fixture(autouse=True)
def _reset_cancel_flag():
    yield
    crypto_screener._cancel_requested.clear()


@pytest.fixture(autouse=True)
def _reset_progress_state():
    # scan()/_scan_dex_pools() write live progress straight into the
    # module-level _scan_state (see crypto_screener.py) -- reset so one
    # test's progress never leaks into the next.
    yield
    crypto_screener._scan_state.update({"phase": None, "current_page": None, "hits_so_far": None})


@pytest.fixture(autouse=True)
def _no_exchange_filter_by_default(mocker):
    # Most tests care about the mcap/volume logic, not exchange tradeability --
    # default to "couldn't determine, don't filter" so they don't need to know
    # about Binance/KuCoin. Tests that exercise the filter override this.
    mocker.patch.object(crypto_screener, "_tradeable_symbols", return_value=None)


@pytest.fixture(autouse=True)
def _no_dex_pools_by_default(mocker):
    # DEX discovery (GeckoTerminal) is included by CryptoExchange.ALL, the
    # default `exchanges` for scan() -- stub it to empty so unrelated tests
    # don't hit the real network (and get rate-limited/blocked). Tests that
    # exercise DEX discovery override these directly.
    mocker.patch.object(crypto_screener.geckoterminal_client, "fetch_new_pools", return_value=[])
    mocker.patch.object(crypto_screener.geckoterminal_client, "fetch_trending_pools", return_value=[])


def _coin(coin_id, symbol, mcap, volume):
    return {"id": coin_id, "symbol": symbol, "name": symbol.upper(), "market_cap": mcap, "total_volume": volume}


def test_first_scan_saves_snapshots_but_creates_no_candidates(session, mocker):
    mocker.patch("time.sleep")
    mocker.patch.object(
        crypto_screener.coingecko_client, "fetch_markets_page",
        side_effect=[[_coin("altcoin-x", "altx", 20_000_000, 1_000_000)], []],
    )

    hits = crypto_screener.scan(session, mcap_max=50_000_000, min_volume_change_pct=50)

    assert hits == 0
    snapshots = session.exec(select(CryptoVolumeSnapshot)).all()
    assert len(snapshots) == 1
    assert snapshots[0].coin_id == "altcoin-x"
    assert session.exec(select(ScreenerCandidate)).all() == []


def test_second_scan_creates_candidate_when_volume_rises_above_threshold(session, mocker):
    mocker.patch("time.sleep")
    mocker.patch.object(
        crypto_screener.coingecko_client, "fetch_markets_page",
        side_effect=[
            [_coin("altcoin-x", "altx", 20_000_000, 1_000_000)], [],  # scan 1
            [_coin("altcoin-x", "altx", 22_000_000, 2_000_000)], [],  # scan 2: volume +100%
        ],
    )

    crypto_screener.scan(session, mcap_max=50_000_000, min_volume_change_pct=50)
    hits = crypto_screener.scan(session, mcap_max=50_000_000, min_volume_change_pct=50)

    assert hits == 1
    candidate = session.get(ScreenerCandidate, "altcoin-x")
    assert candidate is not None
    assert candidate.volume_change_pct == 100.0
    assert candidate.market_cap == 22_000_000


def test_volume_rise_below_threshold_creates_no_candidate(session, mocker):
    mocker.patch("time.sleep")
    mocker.patch.object(
        crypto_screener.coingecko_client, "fetch_markets_page",
        side_effect=[
            [_coin("altcoin-x", "altx", 20_000_000, 1_000_000)], [],
            [_coin("altcoin-x", "altx", 20_000_000, 1_100_000)], [],  # only +10%
        ],
    )

    crypto_screener.scan(session, mcap_max=50_000_000, min_volume_change_pct=50)
    hits = crypto_screener.scan(session, mcap_max=50_000_000, min_volume_change_pct=50)

    assert hits == 0
    assert session.get(ScreenerCandidate, "altcoin-x") is None


def test_coins_outside_mcap_range_are_skipped(session, mocker):
    mocker.patch("time.sleep")
    mocker.patch.object(
        crypto_screener.coingecko_client, "fetch_markets_page",
        side_effect=[
            [
                _coin("bitcoin", "btc", 1_000_000_000_000, 20_000_000_000),  # way above range
                _coin("altcoin-x", "altx", 20_000_000, 1_000_000),  # in range
                _coin("dust-coin", "dust", 1_000, 5),  # below floor
            ],
            [],
        ],
    )

    crypto_screener.scan(session, mcap_max=50_000_000, min_volume_change_pct=50)

    snapshots = {s.coin_id for s in session.exec(select(CryptoVolumeSnapshot)).all()}
    assert snapshots == {"altcoin-x"}  # only the in-range coin gets a snapshot


def test_scan_stops_paging_once_page_entirely_below_floor(session, mocker):
    mocker.patch("time.sleep")
    fetch = mocker.patch.object(
        crypto_screener.coingecko_client, "fetch_markets_page",
        side_effect=[
            [_coin("altcoin-x", "altx", 20_000_000, 1_000_000)],
            [_coin("dust-1", "d1", 1_000, 5), _coin("dust-2", "d2", 2_000, 8)],  # all below floor
        ],
    )

    crypto_screener.scan(session, mcap_max=50_000_000, min_volume_change_pct=50)

    assert fetch.call_count == 2  # stopped after page 2, never requested page 3


def test_run_scan_guarded_reports_status(session, mocker):
    mocker.patch("time.sleep")
    mocker.patch.object(
        crypto_screener.coingecko_client, "fetch_markets_page",
        side_effect=[[_coin("altcoin-x", "altx", 20_000_000, 1_000_000)], []],
    )

    status = crypto_screener.run_scan_guarded(session, mcap_max=50_000_000, min_volume_change_pct=50)

    assert status["running"] is False
    assert status["last_hits"] == 0
    assert status["last_error"] is None
    assert status["last_completed_at"] is not None


def test_run_scan_guarded_skips_when_already_running(session, mocker):
    mocker.patch("time.sleep")
    crypto_screener._scan_lock.acquire()  # simulate a scan already in progress
    try:
        fetch = mocker.patch.object(crypto_screener.coingecko_client, "fetch_markets_page")
        status = crypto_screener.run_scan_guarded(session, mcap_max=50_000_000, min_volume_change_pct=50)
        fetch.assert_not_called()
        assert status["running"] is False  # reflects prior state, unaffected by the skipped call
    finally:
        crypto_screener._scan_lock.release()


def test_run_scan_guarded_records_error_without_raising(session, mocker):
    mocker.patch("time.sleep")
    mocker.patch.object(
        crypto_screener.coingecko_client, "fetch_markets_page", side_effect=RuntimeError("boom")
    )

    status = crypto_screener.run_scan_guarded(session, mcap_max=50_000_000, min_volume_change_pct=50)

    assert status["running"] is False
    assert "boom" in status["last_error"]


def test_scan_stops_early_when_cancel_requested(session, mocker):
    mocker.patch("time.sleep")
    fetch = mocker.patch.object(
        crypto_screener.coingecko_client, "fetch_markets_page",
        side_effect=[
            [_coin("altcoin-x", "altx", 20_000_000, 1_000_000)],
            [_coin("altcoin-y", "alty", 20_000_000, 1_000_000)],
        ],
    )
    crypto_screener._cancel_requested.set()

    hits = crypto_screener.scan(session, mcap_max=50_000_000, min_volume_change_pct=50)

    assert hits == 0
    fetch.assert_not_called()  # cancelled before even the first page


def test_run_scan_guarded_reports_cancelled_status(session, mocker):
    mocker.patch("time.sleep")

    def _cancel_after_first_page(page):
        if page == 1:
            crypto_screener._cancel_requested.set()
            return [_coin("altcoin-x", "altx", 20_000_000, 1_000_000)]
        return [_coin("altcoin-y", "alty", 20_000_000, 1_000_000)]

    fetch = mocker.patch.object(
        crypto_screener.coingecko_client, "fetch_markets_page", side_effect=_cancel_after_first_page
    )

    status = crypto_screener.run_scan_guarded(session, mcap_max=50_000_000, min_volume_change_pct=50)

    assert status["last_cancelled"] is True
    assert status["running"] is False
    fetch.assert_called_once()  # never reached page 2


def test_request_cancel_is_noop_when_nothing_running():
    status = crypto_screener.request_cancel()

    assert status["running"] is False
    assert not crypto_screener._cancel_requested.is_set()


def test_request_cancel_sets_flag_while_running():
    crypto_screener._scan_state["running"] = True
    try:
        crypto_screener.request_cancel()
        assert crypto_screener._cancel_requested.is_set()
    finally:
        crypto_screener._scan_state["running"] = False


def test_require_volume_rising_false_lists_all_coins_in_range(session, mocker):
    mocker.patch("time.sleep")
    mocker.patch.object(
        crypto_screener.coingecko_client, "fetch_markets_page",
        side_effect=[
            [
                _coin("altcoin-x", "altx", 20_000_000, 1_000_000),  # no history yet -> change_pct None
                _coin("altcoin-y", "alty", 25_000_000, 500_000),
            ],
            [],
        ],
    )

    hits = crypto_screener.scan(
        session, mcap_max=50_000_000, min_volume_change_pct=50, require_volume_rising=False
    )

    assert hits == 2
    candidate = session.get(ScreenerCandidate, "altcoin-x")
    assert candidate is not None
    assert candidate.volume_change_pct is None  # first scan, nothing to compare yet


def test_run_scan_guarded_forwards_require_volume_rising(session, mocker):
    mocker.patch("time.sleep")
    mocker.patch.object(
        crypto_screener.coingecko_client, "fetch_markets_page",
        side_effect=[[_coin("altcoin-x", "altx", 20_000_000, 1_000_000)], []],
    )

    status = crypto_screener.run_scan_guarded(
        session, mcap_max=50_000_000, min_volume_change_pct=50, require_volume_rising=False
    )

    assert status["last_hits"] == 1  # would be 0 under the default (rising-required) behaviour


def test_scan_skips_coins_not_tradeable_on_any_enabled_exchange(session, mocker):
    mocker.patch("time.sleep")
    mocker.patch.object(crypto_screener, "_tradeable_symbols", return_value={"ALTX"})
    mocker.patch.object(
        crypto_screener.coingecko_client, "fetch_markets_page",
        side_effect=[
            [
                _coin("altcoin-x", "altx", 20_000_000, 1_000_000),  # tradeable
                _coin("dex-only", "dexo", 20_000_000, 1_000_000),  # not on any exchange
            ],
            [],
        ],
    )

    hits = crypto_screener.scan(
        session, mcap_max=50_000_000, min_volume_change_pct=50, require_volume_rising=False
    )

    assert hits == 1
    assert session.get(ScreenerCandidate, "altcoin-x") is not None
    assert session.get(ScreenerCandidate, "dex-only") is None
    # untradeable coins are skipped before even snapshotting
    snapshots = {s.coin_id for s in session.exec(select(CryptoVolumeSnapshot)).all()}
    assert snapshots == {"altcoin-x"}


def test_scan_does_not_filter_when_exchange_lookup_unavailable(session, mocker):
    mocker.patch("time.sleep")
    mocker.patch.object(crypto_screener, "_tradeable_symbols", return_value=None)
    mocker.patch.object(
        crypto_screener.coingecko_client, "fetch_markets_page",
        side_effect=[[_coin("altcoin-x", "altx", 20_000_000, 1_000_000)], []],
    )

    hits = crypto_screener.scan(
        session, mcap_max=50_000_000, min_volume_change_pct=50, require_volume_rising=False
    )

    assert hits == 1  # no filter applied when tradeable set couldn't be determined


def _dex_pool(network, pool_address, symbol, mcap, volume_24h, coingecko_coin_id=None):
    return {
        "network": network,
        "pool_address": pool_address,
        "symbol": symbol,
        "name": symbol.upper(),
        "coingecko_coin_id": coingecko_coin_id,
        "market_cap": mcap,
        "volume_24h": volume_24h,
    }


def test_scan_includes_dex_pools_when_geckoterminal_enabled(session, mocker):
    mocker.patch("time.sleep")
    mocker.patch.object(crypto_screener.coingecko_client, "fetch_markets_page", return_value=[])
    mocker.patch.object(
        crypto_screener.geckoterminal_client, "fetch_new_pools",
        side_effect=[[_dex_pool("eth", "0xpool1", "dexcoin", 5_000_000, 1000)], []],
    )
    mocker.patch.object(crypto_screener.geckoterminal_client, "fetch_trending_pools", return_value=[])

    hits = crypto_screener.scan(
        session, mcap_max=50_000_000, min_volume_change_pct=50, require_volume_rising=False,
        exchanges=("binance", "kucoin", "geckoterminal"),
    )

    assert hits == 1
    candidate = session.get(ScreenerCandidate, "gt:eth:0xpool1")
    assert candidate is not None
    assert candidate.source == "geckoterminal"
    assert candidate.network == "eth"
    assert candidate.pool_address == "0xpool1"
    assert candidate.symbol == "dexcoin"


def test_scan_uses_real_coingecko_id_for_dex_pool_when_present(session, mocker):
    mocker.patch("time.sleep")
    mocker.patch.object(crypto_screener.coingecko_client, "fetch_markets_page", return_value=[])
    mocker.patch.object(
        crypto_screener.geckoterminal_client, "fetch_new_pools",
        side_effect=[[_dex_pool("eth", "0xpool2", "pepe", 5_000_000, 1000, coingecko_coin_id="pepe-coin")], []],
    )
    mocker.patch.object(crypto_screener.geckoterminal_client, "fetch_trending_pools", return_value=[])

    crypto_screener.scan(
        session, mcap_max=50_000_000, min_volume_change_pct=50, require_volume_rising=False,
        exchanges=("geckoterminal",),
    )

    assert session.get(ScreenerCandidate, "pepe-coin") is not None
    assert session.get(ScreenerCandidate, "gt:eth:0xpool2") is None


def test_scan_dex_pools_respects_mcap_range(session, mocker):
    mocker.patch("time.sleep")
    mocker.patch.object(crypto_screener.coingecko_client, "fetch_markets_page", return_value=[])
    mocker.patch.object(
        crypto_screener.geckoterminal_client, "fetch_new_pools",
        side_effect=[[_dex_pool("eth", "0xtoosmall", "tiny", 1_000, 10)], []],  # below MIN_MARKET_CAP_FLOOR
    )
    mocker.patch.object(crypto_screener.geckoterminal_client, "fetch_trending_pools", return_value=[])

    hits = crypto_screener.scan(
        session, mcap_max=50_000_000, min_volume_change_pct=50, require_volume_rising=False,
        exchanges=("geckoterminal",),
    )

    assert hits == 0


def test_scan_excludes_dex_pools_when_geckoterminal_disabled(session, mocker):
    mocker.patch("time.sleep")
    mocker.patch.object(crypto_screener.coingecko_client, "fetch_markets_page", return_value=[])
    new_pools = mocker.patch.object(crypto_screener.geckoterminal_client, "fetch_new_pools")
    trending = mocker.patch.object(crypto_screener.geckoterminal_client, "fetch_trending_pools")

    crypto_screener.scan(
        session, mcap_max=50_000_000, min_volume_change_pct=50, require_volume_rising=False,
        exchanges=("binance", "kucoin"),
    )

    new_pools.assert_not_called()
    trending.assert_not_called()


def test_scan_dex_pools_checks_both_new_and_trending(session, mocker):
    mocker.patch("time.sleep")
    mocker.patch.object(crypto_screener.coingecko_client, "fetch_markets_page", return_value=[])
    new_pools = mocker.patch.object(crypto_screener.geckoterminal_client, "fetch_new_pools", return_value=[])
    trending = mocker.patch.object(
        crypto_screener.geckoterminal_client, "fetch_trending_pools",
        side_effect=[[_dex_pool("eth", "0xtrend", "trendcoin", 5_000_000, 1000)], []],
    )

    hits = crypto_screener.scan(
        session, mcap_max=50_000_000, min_volume_change_pct=50, require_volume_rising=False,
        exchanges=("geckoterminal",),
    )

    assert hits == 1
    new_pools.assert_called()
    trending.assert_called()


def test_list_candidates_sorted_by_volume_change_desc(session, mocker):
    mocker.patch("time.sleep")
    mocker.patch.object(
        crypto_screener.coingecko_client, "fetch_markets_page",
        side_effect=[
            [_coin("low-riser", "low", 10_000_000, 1_000_000), _coin("high-riser", "high", 10_000_000, 1_000_000)], [],
            [_coin("low-riser", "low", 10_000_000, 1_200_000), _coin("high-riser", "high", 10_000_000, 3_000_000)], [],
        ],
    )

    crypto_screener.scan(session, mcap_max=50_000_000, min_volume_change_pct=10)
    crypto_screener.scan(session, mcap_max=50_000_000, min_volume_change_pct=10)

    candidates, total = crypto_screener.list_candidates(session)
    assert total == 2
    assert [c.coin_id for c in candidates] == ["high-riser", "low-riser"]


def test_list_candidates_sorts_by_market_cap(session):
    session.add(ScreenerCandidate(coin_id="small", symbol="s", name="Small", market_cap=1_000_000, volume_24h=1))
    session.add(ScreenerCandidate(coin_id="big", symbol="b", name="Big", market_cap=20_000_000, volume_24h=1))
    session.commit()

    candidates, total = crypto_screener.list_candidates(session, sort="market_cap")

    assert total == 2
    assert [c.coin_id for c in candidates] == ["big", "small"]


def test_list_candidates_paginates(session):
    for i in range(5):
        session.add(
            ScreenerCandidate(
                coin_id=f"coin-{i}", symbol=f"c{i}", name=f"Coin {i}",
                market_cap=10_000_000, volume_24h=1, volume_change_pct=float(i),
            )
        )
    session.commit()

    page1, total = crypto_screener.list_candidates(session, page=1, page_size=2)
    page2, _ = crypto_screener.list_candidates(session, page=2, page_size=2)

    assert total == 5
    assert [c.coin_id for c in page1] == ["coin-4", "coin-3"]
    assert [c.coin_id for c in page2] == ["coin-2", "coin-1"]


def test_list_candidates_filters_by_symbol_case_insensitive(session):
    session.add(ScreenerCandidate(coin_id="pepe-coin", symbol="PEPE", name="Pepe", market_cap=1, volume_24h=1))
    session.add(ScreenerCandidate(coin_id="doge-coin", symbol="DOGE", name="Dogecoin", market_cap=1, volume_24h=1))
    session.commit()

    items, total = crypto_screener.list_candidates(session, query="pep")

    assert total == 1
    assert [c.coin_id for c in items] == ["pepe-coin"]


def test_list_candidates_filters_by_name_substring(session):
    session.add(ScreenerCandidate(coin_id="pepe-coin", symbol="PEPE", name="Pepe Classic", market_cap=1, volume_24h=1))
    session.add(ScreenerCandidate(coin_id="doge-coin", symbol="DOGE", name="Dogecoin", market_cap=1, volume_24h=1))
    session.commit()

    items, total = crypto_screener.list_candidates(session, query="classic")

    assert total == 1
    assert [c.coin_id for c in items] == ["pepe-coin"]


def test_list_candidates_query_no_match_returns_empty(session):
    session.add(ScreenerCandidate(coin_id="pepe-coin", symbol="PEPE", name="Pepe", market_cap=1, volume_24h=1))
    session.commit()

    items, total = crypto_screener.list_candidates(session, query="zzz-nope")

    assert total == 0
    assert items == []


def test_list_candidates_query_escapes_like_wildcards(session):
    # "%"/"_" in the search text must be treated literally, not as SQL LIKE
    # wildcards -- otherwise a search for "50%" would match anything.
    session.add(ScreenerCandidate(coin_id="a", symbol="A", name="50% Gains", market_cap=1, volume_24h=1))
    session.add(ScreenerCandidate(coin_id="b", symbol="B", name="Fifty X Gains", market_cap=1, volume_24h=1))
    session.commit()

    items, total = crypto_screener.list_candidates(session, query="50%")

    assert total == 1
    assert [c.coin_id for c in items] == ["a"]


def test_scan_reports_live_progress_during_coingecko_pages(session, mocker):
    mocker.patch("time.sleep")
    mocker.patch.object(
        crypto_screener.coingecko_client, "fetch_markets_page",
        side_effect=[
            [_coin("altcoin-x", "altx", 20_000_000, 1_000_000)],
            [_coin("altcoin-y", "alty", 20_000_000, 1_000_000)],
            [],
        ],
    )

    crypto_screener.scan(
        session, mcap_max=50_000_000, min_volume_change_pct=0, require_volume_rising=False,
        exchanges=("binance", "kucoin", "mexc"),  # exclude geckoterminal so phase stays "coingecko"
    )

    assert crypto_screener._scan_state["phase"] == "coingecko"
    assert crypto_screener._scan_state["current_page"] == 2
    assert crypto_screener._scan_state["hits_so_far"] == 2


def test_scan_reports_live_progress_during_dex_pools_phase(session, mocker):
    mocker.patch("time.sleep")
    mocker.patch.object(crypto_screener.coingecko_client, "fetch_markets_page", return_value=[])
    mocker.patch.object(
        crypto_screener.geckoterminal_client, "fetch_new_pools",
        side_effect=[[_dex_pool("eth", "0xpool9", "dexcoin", 5_000_000, 1000)], []],
    )
    mocker.patch.object(crypto_screener.geckoterminal_client, "fetch_trending_pools", return_value=[])

    crypto_screener.scan(
        session, mcap_max=50_000_000, min_volume_change_pct=0, require_volume_rising=False,
        exchanges=("geckoterminal",),
    )

    assert crypto_screener._scan_state["phase"] == "dex_pools"
    assert crypto_screener._scan_state["hits_so_far"] == 1
