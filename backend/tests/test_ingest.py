from datetime import date, timedelta

import pandas as pd
from sqlmodel import select

from app.crawler import binance_client, coingecko_client, geckoterminal_client, kucoin_client, mexc_client
from app.models import Candle, Symbol, Timeframe
from app.services import ingest


def _daily_df():
    return pd.DataFrame([
        {"time": "2025-06-30", "open": 100, "high": 105, "low": 99, "close": 104, "volume": 1_000_000},
        {"time": "2025-07-01", "open": 104, "high": 106, "low": 103, "close": 105, "volume": 1_200_000},
    ])


def test_ingest_daily_inserts(session, mocker):
    mocker.patch.object(ingest.vnstock_client, "fetch_daily", return_value=_daily_df())

    count = ingest.ingest_daily(session, "fpt")

    assert count == 2
    rows = session.exec(select(Candle).where(Candle.timeframe == Timeframe.DAILY)).all()
    assert len(rows) == 2
    assert all(r.ticker == "FPT" for r in rows)  # normalised upper-case
    last = [r for r in rows if r.close == 105][0]
    assert last.open == 104 and last.high == 106 and last.volume == 1_200_000


def test_ingest_daily_is_idempotent_and_updates(session, mocker):
    mocker.patch.object(ingest.vnstock_client, "fetch_daily", return_value=_daily_df())
    ingest.ingest_daily(session, "FPT")

    # Re-run with a corrected close for 2025-07-01; must update, not duplicate.
    updated = _daily_df()
    updated.loc[updated["time"] == "2025-07-01", "close"] = 108
    mocker.patch.object(ingest.vnstock_client, "fetch_daily", return_value=updated)
    ingest.ingest_daily(session, "FPT")

    rows = session.exec(select(Candle)).all()
    assert len(rows) == 2  # no duplicates
    jul1 = [r for r in rows if r.bucket_start == pd.Timestamp("2025-07-01").to_pydatetime()][0]
    assert jul1.close == 108


# --- delta-fetch: don't re-request the full lookback once history exists ---


def test_delta_start_date_uses_full_lookback_when_no_candles_exist(session):
    start = ingest._delta_start_date(session, "FPT", Timeframe.DAILY, lookback_days=730)

    assert start == (date.today() - timedelta(days=730)).isoformat()


def test_delta_start_date_uses_latest_candle_minus_overlap_when_candles_exist(session):
    lookback_days = 730
    full_start = date.today() - timedelta(days=lookback_days)
    latest = date.today() - timedelta(days=30)
    # Two rows: one confirming history already reaches back to the full
    # lookback window (so this exercises the delta path, not the "lookback
    # was just raised" backfill path below), one recent -- the date the
    # delta should actually resume from.
    session.add(Candle(
        ticker="FPT", timeframe=Timeframe.DAILY, bucket_start=full_start,
        open=100, high=101, low=99, close=100, volume=1,
    ))
    session.add(Candle(
        ticker="FPT", timeframe=Timeframe.DAILY, bucket_start=latest,
        open=100, high=101, low=99, close=100, volume=1,
    ))
    session.commit()

    start = ingest._delta_start_date(session, "FPT", Timeframe.DAILY, lookback_days=lookback_days)

    assert start == (latest - timedelta(days=ingest.DELTA_OVERLAP_DAYS)).isoformat()


def test_delta_start_date_backfills_when_lookback_increased_after_earlier_ingest(session):
    # Ticker was ingested under a SMALLER daily_lookback_days -- its earliest
    # stored candle doesn't reach back far enough to satisfy a since-INCREASED
    # setting (e.g. 200 -> 730 in Settings). Resuming from just the latest
    # candle would silently ignore that increase forever; must backfill.
    session.add(Candle(
        ticker="FPT", timeframe=Timeframe.DAILY, bucket_start=date.today() - timedelta(days=200),
        open=100, high=101, low=99, close=100, volume=1,
    ))
    session.add(Candle(
        ticker="FPT", timeframe=Timeframe.DAILY, bucket_start=date.today() - timedelta(days=1),
        open=100, high=101, low=99, close=100, volume=1,
    ))
    session.commit()

    start = ingest._delta_start_date(session, "FPT", Timeframe.DAILY, lookback_days=730)

    assert start == (date.today() - timedelta(days=730)).isoformat()


def test_delta_start_date_never_goes_earlier_than_the_full_lookback_window(session):
    # A stale/corrupted row from far in the past shouldn't push the delta
    # start EARLIER than a full-lookback fetch would have gone anyway.
    ancient = date.today() - timedelta(days=2000)
    session.add(Candle(
        ticker="FPT", timeframe=Timeframe.DAILY, bucket_start=ancient,
        open=100, high=101, low=99, close=100, volume=1,
    ))
    session.commit()

    start = ingest._delta_start_date(session, "FPT", Timeframe.DAILY, lookback_days=730)

    assert start == (date.today() - timedelta(days=730)).isoformat()


def test_ingest_daily_first_call_fetches_the_full_lookback_window(session, mocker):
    fetch = mocker.patch.object(ingest.vnstock_client, "fetch_daily", return_value=_daily_df())

    ingest.ingest_daily(session, "FPT")  # no history yet

    assert fetch.call_args.args[1] == (date.today() - timedelta(days=730)).isoformat()


def test_ingest_daily_uses_delta_range_when_history_already_covers_the_lookback(session, mocker):
    # Simulate a ticker that's already been fully backfilled: its earliest
    # stored candle already reaches back to the lookback window, so this
    # call should resume from the latest stored candle instead of
    # re-requesting the whole window (the ONLY realistic case the original,
    # now-removed two-call version of this test meant to exercise -- a
    # static mock returning the same 2 rows on both calls never actually
    # simulated "already has full history").
    session.add(Candle(
        ticker="FPT", timeframe=Timeframe.DAILY, bucket_start=date.today() - timedelta(days=730),
        open=100, high=101, low=99, close=100, volume=1,
    ))
    session.add(Candle(
        ticker="FPT", timeframe=Timeframe.DAILY, bucket_start=pd.Timestamp("2025-07-01").to_pydatetime(),
        open=104, high=106, low=103, close=105, volume=1_200_000,
    ))
    session.commit()
    fetch = mocker.patch.object(ingest.vnstock_client, "fetch_daily", return_value=_daily_df())

    ingest.ingest_daily(session, "FPT")

    expected = (date.fromisoformat("2025-07-01") - timedelta(days=ingest.DELTA_OVERLAP_DAYS)).isoformat()
    assert fetch.call_args.args[1] == expected


def test_upsert_candles_mixed_batch_updates_existing_and_inserts_new(session, mocker):
    # The realistic delta-fetch shape: a small batch where the overlap days
    # already exist (must update, not duplicate) alongside genuinely new
    # days (must insert) -- in the SAME call, unlike the separate-calls
    # idempotency test above.
    ingest._upsert_candles(session, "FPT", Timeframe.DAILY, [
        (pd.Timestamp("2025-07-01").to_pydatetime(),
         pd.Series({"open": 100, "high": 101, "low": 99, "close": 100, "volume": 1}), None),
    ])
    session.commit()

    mixed_rows = [
        (pd.Timestamp("2025-07-01").to_pydatetime(),  # overlap day -- correction
         pd.Series({"open": 100, "high": 101, "low": 99, "close": 108, "volume": 1}), None),
        (pd.Timestamp("2025-07-02").to_pydatetime(),  # genuinely new day
         pd.Series({"open": 108, "high": 110, "low": 107, "close": 109, "volume": 2}), None),
    ]
    ingest._upsert_candles(session, "FPT", Timeframe.DAILY, mixed_rows)
    session.commit()

    rows = session.exec(select(Candle).where(Candle.timeframe == Timeframe.DAILY)).all()
    assert len(rows) == 2  # no duplicate for the overlap day
    jul1 = [r for r in rows if r.bucket_start == pd.Timestamp("2025-07-01").to_pydatetime()][0]
    assert jul1.close == 108  # corrected, not left stale
    jul2 = [r for r in rows if r.bucket_start == pd.Timestamp("2025-07-02").to_pydatetime()][0]
    assert jul2.close == 109  # new row present


def test_ingest_half_session_from_hourly(session, mocker):
    hourly = pd.DataFrame([
        {"time": "2025-07-03 09:00:00", "open": 100, "high": 101, "low": 99, "close": 100.5, "volume": 1000},
        {"time": "2025-07-03 10:00:00", "open": 100.5, "high": 102, "low": 100, "close": 101.5, "volume": 2000},
        {"time": "2025-07-03 13:00:00", "open": 101.5, "high": 101.6, "low": 100, "close": 100.2, "volume": 1500},
    ])
    mocker.patch.object(ingest.vnstock_client, "fetch_hourly", return_value=hourly)

    count = ingest.ingest_half_session(session, "FPT")

    assert count == 2
    rows = session.exec(select(Candle).where(Candle.timeframe == Timeframe.HALF_SESSION)).all()
    parts = {r.session_part for r in rows}
    assert parts == {"morning", "afternoon"}


def _crypto_df():
    return pd.DataFrame([
        {"time": pd.Timestamp("2025-07-01 00:00:00"), "open": 60000, "high": 61000, "low": 59500, "close": 60800, "volume": 1234},
        {"time": pd.Timestamp("2025-07-01 04:00:00"), "open": 60800, "high": 61200, "low": 60500, "close": 61000, "volume": 987},
    ])


def test_ingest_crypto_uses_binance_when_available(session, mocker):
    binance_spy = mocker.patch.object(binance_client, "fetch_klines", return_value=_crypto_df())
    kucoin_spy = mocker.patch.object(kucoin_client, "fetch_klines")

    count = ingest.ingest_crypto(session, "btc", Timeframe.HOUR_4)

    assert count == 2
    binance_spy.assert_called_once_with("BTCUSDT", "4h")
    kucoin_spy.assert_not_called()
    rows = session.exec(select(Candle).where(Candle.timeframe == Timeframe.HOUR_4)).all()
    assert all(r.ticker == "BTC" for r in rows)


def test_ingest_crypto_looks_up_exchange_symbol_but_stores_under_coin_symbol(session, mocker):
    # Screener-promoted coins store candles under a CoinGecko coin_id (unique)
    # but must still look the coin up on the exchange by its real trading
    # symbol -- these differ, unlike every other ingest_crypto test here.
    binance_spy = mocker.patch.object(binance_client, "fetch_klines", return_value=_crypto_df())

    count = ingest.ingest_crypto(session, "pepesol", Timeframe.HOUR_4, exchange_symbol="PEPE")

    assert count == 2
    binance_spy.assert_called_once_with("PEPEUSDT", "4h")
    rows = session.exec(select(Candle).where(Candle.timeframe == Timeframe.HOUR_4)).all()
    assert all(r.ticker == "PEPESOL" for r in rows)


def test_ingest_crypto_falls_back_to_kucoin_when_not_on_binance(session, mocker):
    mocker.patch.object(
        binance_client, "fetch_klines", side_effect=binance_client.SymbolNotFoundError("nope")
    )
    kucoin_spy = mocker.patch.object(kucoin_client, "fetch_klines", return_value=_crypto_df())

    count = ingest.ingest_crypto(session, "tinycoin", Timeframe.HOUR_1)

    assert count == 2
    kucoin_spy.assert_called_once_with("TINYCOIN-USDT", Timeframe.HOUR_1)


def test_ingest_crypto_falls_back_to_mexc_when_not_on_binance_or_kucoin(session, mocker):
    mocker.patch.object(binance_client, "fetch_klines", side_effect=binance_client.SymbolNotFoundError("nope"))
    mocker.patch.object(kucoin_client, "fetch_klines", side_effect=kucoin_client.SymbolNotFoundError("nope"))
    mexc_spy = mocker.patch.object(mexc_client, "fetch_klines", return_value=_crypto_df())

    count = ingest.ingest_crypto(session, "tinycoin", Timeframe.HOUR_1)

    assert count == 2
    mexc_spy.assert_called_once_with("TINYCOINUSDT", Timeframe.HOUR_1)


def test_ingest_crypto_returns_zero_when_no_source_has_it(session, mocker):
    mocker.patch.object(
        binance_client, "fetch_klines", side_effect=binance_client.SymbolNotFoundError("nope")
    )
    mocker.patch.object(
        kucoin_client, "fetch_klines", side_effect=kucoin_client.SymbolNotFoundError("nope either")
    )
    mocker.patch.object(
        mexc_client, "fetch_klines", side_effect=mexc_client.SymbolNotFoundError("nope either")
    )

    count = ingest.ingest_crypto(session, "ghostcoin", Timeframe.DAILY)

    assert count == 0
    assert session.exec(select(Candle)).all() == []


def test_ingest_crypto_falls_back_on_binance_crawl_error_too(session, mocker):
    mocker.patch.object(
        binance_client, "fetch_klines", side_effect=binance_client.CrawlError("network blip")
    )
    kucoin_spy = mocker.patch.object(kucoin_client, "fetch_klines", return_value=_crypto_df())

    count = ingest.ingest_crypto(session, "btc", Timeframe.DAILY)

    assert count == 2
    kucoin_spy.assert_called_once()


def test_ingest_crypto_skips_disabled_exchange(session, mocker):
    # Binance would have it, but the user only enabled KuCoin -- must not even try Binance.
    binance_spy = mocker.patch.object(binance_client, "fetch_klines", return_value=_crypto_df())
    kucoin_spy = mocker.patch.object(kucoin_client, "fetch_klines", return_value=_crypto_df())

    count = ingest.ingest_crypto(session, "btc", Timeframe.DAILY, exchanges=(ingest.EXCHANGE_KUCOIN,))

    assert count == 2
    binance_spy.assert_not_called()
    kucoin_spy.assert_called_once()


def test_ingest_crypto_returns_zero_when_only_enabled_exchange_lacks_symbol(session, mocker):
    binance_spy = mocker.patch.object(binance_client, "fetch_klines", return_value=_crypto_df())
    mocker.patch.object(
        kucoin_client, "fetch_klines", side_effect=kucoin_client.SymbolNotFoundError("nope")
    )

    count = ingest.ingest_crypto(session, "btc", Timeframe.DAILY, exchanges=(ingest.EXCHANGE_KUCOIN,))

    assert count == 0
    binance_spy.assert_not_called()  # disabled, must never be tried as a fallback


def test_ingest_crypto_falls_back_to_geckoterminal_with_cached_pool(session, mocker):
    mocker.patch.object(binance_client, "fetch_klines", side_effect=binance_client.SymbolNotFoundError("nope"))
    mocker.patch.object(kucoin_client, "fetch_klines", side_effect=kucoin_client.SymbolNotFoundError("nope"))
    mocker.patch.object(mexc_client, "fetch_klines", side_effect=mexc_client.SymbolNotFoundError("nope"))
    gt_ohlcv = mocker.patch.object(geckoterminal_client, "fetch_ohlcv", return_value=_crypto_df())
    resolve_spy = mocker.patch.object(coingecko_client, "fetch_coin_platforms")

    symbol = Symbol(ticker="DEXCOIN", dex_network="eth", dex_pool_address="0xpool1")

    count = ingest.ingest_crypto(session, "dexcoin", Timeframe.HOUR_4, symbol=symbol)

    assert count == 2
    gt_ohlcv.assert_called_once_with("eth", "0xpool1", Timeframe.HOUR_4)
    resolve_spy.assert_not_called()  # already cached, no need to re-resolve


def test_ingest_crypto_resolves_and_caches_dex_pool_via_coingecko(session, mocker):
    mocker.patch.object(binance_client, "fetch_klines", side_effect=binance_client.SymbolNotFoundError("nope"))
    mocker.patch.object(kucoin_client, "fetch_klines", side_effect=kucoin_client.SymbolNotFoundError("nope"))
    mocker.patch.object(mexc_client, "fetch_klines", side_effect=mexc_client.SymbolNotFoundError("nope"))
    mocker.patch.object(
        coingecko_client, "fetch_coin_platforms",
        return_value={"ethereum": "0xtokenaddr", "some-unmapped-chain": "0xother"},
    )
    pools_spy = mocker.patch.object(geckoterminal_client, "fetch_token_pools", return_value="0xresolvedpool")
    gt_ohlcv = mocker.patch.object(geckoterminal_client, "fetch_ohlcv", return_value=_crypto_df())

    session.add(Symbol(ticker="PEPE", coingecko_id="pepe-coin"))
    session.commit()
    symbol = session.get(Symbol, "PEPE")

    count = ingest.ingest_crypto(session, "pepe", Timeframe.DAILY, symbol=symbol)

    assert count == 2
    pools_spy.assert_called_once_with("eth", "0xtokenaddr")  # unmapped chain skipped
    gt_ohlcv.assert_called_once_with("eth", "0xresolvedpool", Timeframe.DAILY)
    # resolved pool is cached onto the symbol for next time
    refreshed = session.get(Symbol, "PEPE")
    assert refreshed.dex_network == "eth"
    assert refreshed.dex_pool_address == "0xresolvedpool"


def test_ingest_crypto_geckoterminal_skipped_without_symbol(session, mocker):
    mocker.patch.object(binance_client, "fetch_klines", side_effect=binance_client.SymbolNotFoundError("nope"))
    mocker.patch.object(kucoin_client, "fetch_klines", side_effect=kucoin_client.SymbolNotFoundError("nope"))
    mocker.patch.object(mexc_client, "fetch_klines", side_effect=mexc_client.SymbolNotFoundError("nope"))
    gt_ohlcv = mocker.patch.object(geckoterminal_client, "fetch_ohlcv")

    count = ingest.ingest_crypto(session, "dexcoin", Timeframe.DAILY, symbol=None)

    assert count == 0
    gt_ohlcv.assert_not_called()


def test_ingest_crypto_geckoterminal_gives_up_without_coingecko_id_or_cached_pool(session, mocker):
    mocker.patch.object(binance_client, "fetch_klines", side_effect=binance_client.SymbolNotFoundError("nope"))
    mocker.patch.object(kucoin_client, "fetch_klines", side_effect=kucoin_client.SymbolNotFoundError("nope"))
    mocker.patch.object(mexc_client, "fetch_klines", side_effect=mexc_client.SymbolNotFoundError("nope"))
    platforms_spy = mocker.patch.object(coingecko_client, "fetch_coin_platforms")

    symbol = Symbol(ticker="MYSTERY")  # no coingecko_id, no cached dex pool

    count = ingest.ingest_crypto(session, "mystery", Timeframe.DAILY, symbol=symbol)

    assert count == 0
    platforms_spy.assert_not_called()


def test_ingest_crypto_skips_geckoterminal_when_disabled(session, mocker):
    mocker.patch.object(binance_client, "fetch_klines", side_effect=binance_client.SymbolNotFoundError("nope"))
    mocker.patch.object(kucoin_client, "fetch_klines", side_effect=kucoin_client.SymbolNotFoundError("nope"))
    gt_ohlcv = mocker.patch.object(geckoterminal_client, "fetch_ohlcv")

    symbol = Symbol(ticker="DEXCOIN", dex_network="eth", dex_pool_address="0xpool1")

    count = ingest.ingest_crypto(
        session, "dexcoin", Timeframe.DAILY, exchanges=(ingest.EXCHANGE_BINANCE, ingest.EXCHANGE_KUCOIN), symbol=symbol
    )

    assert count == 0
    gt_ohlcv.assert_not_called()


def _seed_daily_candles(session, ticker: str, rows: list[dict]):
    for row in rows:
        session.add(Candle(ticker=ticker, timeframe=Timeframe.DAILY, **row))
    session.commit()


def test_ingest_weekly_resamples_from_stored_daily_candles(session, mocker):
    # No crawler client should ever be touched -- weekly is a pure resample
    # of daily candles already in the DB.
    daily_spy = mocker.patch.object(ingest.vnstock_client, "fetch_daily")
    _seed_daily_candles(session, "FPT", [
        dict(bucket_start=pd.Timestamp("2025-06-30").to_pydatetime(), open=100, high=101, low=99, close=100.5, volume=1000),
        dict(bucket_start=pd.Timestamp("2025-07-01").to_pydatetime(), open=100.5, high=103, low=100, close=102, volume=1500),
    ])

    count = ingest.ingest_weekly(session, "fpt")

    assert count == 1
    daily_spy.assert_not_called()
    row = session.exec(select(Candle).where(Candle.timeframe == Timeframe.WEEK)).one()
    assert row.ticker == "FPT"
    assert row.bucket_start == pd.Timestamp("2025-06-30").to_pydatetime()
    assert row.open == 100 and row.close == 102 and row.high == 103 and row.volume == 2500


def test_ingest_weekly_is_idempotent_and_updates(session):
    _seed_daily_candles(session, "FPT", [
        dict(bucket_start=pd.Timestamp("2025-06-30").to_pydatetime(), open=100, high=101, low=99, close=100.5, volume=1000),
    ])
    ingest.ingest_weekly(session, "FPT")

    # A new daily bar lands in the same week -- re-running must update the
    # existing weekly row in place, not duplicate it.
    session.add(Candle(
        ticker="FPT", timeframe=Timeframe.DAILY,
        bucket_start=pd.Timestamp("2025-07-01").to_pydatetime(), open=100.5, high=105, low=100, close=104, volume=800,
    ))
    session.commit()
    ingest.ingest_weekly(session, "FPT")

    rows = session.exec(select(Candle).where(Candle.timeframe == Timeframe.WEEK)).all()
    assert len(rows) == 1
    assert rows[0].close == 104
    assert rows[0].high == 105
    assert rows[0].volume == 1800


def test_ingest_weekly_returns_zero_without_daily_history(session):
    assert ingest.ingest_weekly(session, "NOPE") == 0
