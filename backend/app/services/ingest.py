"""Crawl -> upsert candles. Idempotent: re-running never duplicates rows."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

import pandas as pd
from sqlmodel import Session, func, select

from app.crawler import binance_client, coingecko_client, geckoterminal_client, kucoin_client, mexc_client, vnstock_client
from app.crawler.resample import resample_half_session, resample_weekly
from app.models import Candle, CryptoExchange, Symbol, Timeframe
from app.services import settings_service

logger = logging.getLogger("chart_volume.ingest")

# Fallback defaults if settings_service has no stored value (kept in sync with
# app.services.settings_service.DEFAULTS).
DAILY_LOOKBACK_DAYS = 730
HALF_SESSION_LOOKBACK_DAYS = 60

# How many calendar days of overlap a delta-fetch re-requests before the
# latest candle already stored, to catch a source-side correction to a very
# recently published bar (e.g. a same-day close vs. a next-day settled
# figure). Deliberately small relative to the lookback windows above -- see
# _delta_start_date's own note on what this does and doesn't protect against.
DELTA_OVERLAP_DAYS = 10

# Our internal timeframe names -> Binance's own interval strings.
_BINANCE_INTERVAL = {Timeframe.HOUR_1: "1h", Timeframe.HOUR_4: "4h", Timeframe.DAILY: "1d"}


def _date_range(lookback_days: int, start: str | None, end: str | None) -> tuple[str, str]:
    end_d = end or date.today().isoformat()
    start_d = start or (date.today() - timedelta(days=lookback_days)).isoformat()
    return start_d, end_d


def _upsert_candles(
    session: Session,
    ticker: str,
    timeframe: str,
    rows: list[tuple[datetime, pd.Series, str | None]],
) -> None:
    """Same idempotent upsert semantics as before, but ONE query for the
    whole batch instead of a per-row SELECT: ingest_daily's lookback window
    is ~523 trading days, so upserting one candle at a time meant 523
    individual round-trips on every single run, nearly all of them just
    confirming a row that hadn't changed. IN() over ~500-700 bucket_starts is
    well within SQLite's parameter limit (999+), so this stays one query
    even on a first-time ticker's full-history fetch."""
    if not rows:
        return
    starts = [bucket_start for bucket_start, _, _ in rows]
    existing = {
        c.bucket_start: c
        for c in session.exec(
            select(Candle).where(
                Candle.ticker == ticker,
                Candle.timeframe == timeframe,
                Candle.bucket_start.in_(starts),
            )
        ).all()
    }
    for bucket_start, row, session_part in rows:
        values = dict(
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            volume=float(row["volume"]),
            session_part=session_part,
        )
        existing_row = existing.get(bucket_start)
        if existing_row:
            for key, val in values.items():
                setattr(existing_row, key, val)
            session.add(existing_row)
        else:
            session.add(
                Candle(
                    ticker=ticker,
                    timeframe=timeframe,
                    bucket_start=bucket_start,
                    **values,
                )
            )


def _delta_start_date(session: Session, ticker: str, timeframe: str, lookback_days: int) -> str:
    """Fetching the full lookback window on every run is wasteful once a
    ticker already has history stored: the vast majority of that window
    hasn't changed since the last ingest. Starts from the latest stored
    candle's date minus DELTA_OVERLAP_DAYS (to catch a source-side
    correction to a recently published bar), falling back to the full
    lookback window when nothing is stored yet -- e.g. a newly seeded
    ticker's very first fetch.

    Also falls back to the full window when the EARLIEST stored candle
    doesn't already reach back that far: analysis reads every stored candle
    for the ticker (app.services.analysis._load_candles has no date filter),
    so a narrow forward-only delta never loses context for a ticker that's
    always been ingested under the same lookback -- but if the user just
    raised daily_lookback_days (e.g. 730 -> 1500) in Settings, the extra
    history further back was never fetched at all, and only checking the
    latest candle would keep silently ignoring that increase forever.

    ASSUMPTION worth flagging explicitly: this does not protect against the
    source silently rewriting bars far outside the overlap window (e.g. a
    stock-split/dividend back-adjustment touching the whole historical
    series) -- a bounded overlap can't catch that regardless of size. Revisit
    if vnstock turns out to do this for VN equities; nothing in this
    codebase currently confirms or rules it out."""
    earliest, latest = session.exec(
        select(func.min(Candle.bucket_start), func.max(Candle.bucket_start)).where(
            Candle.ticker == ticker, Candle.timeframe == timeframe
        )
    ).first()
    full_start = date.today() - timedelta(days=lookback_days)
    if latest is None or earliest.date() > full_start:
        return full_start.isoformat()
    delta_start = latest.date() - timedelta(days=DELTA_OVERLAP_DAYS)
    return max(delta_start, full_start).isoformat()


def ingest_daily(
    session: Session, ticker: str, start: str | None = None, end: str | None = None
) -> int:
    ticker = ticker.upper()
    daily_lookback, _ = settings_service.get_lookbacks(session)
    if start is None:
        start = _delta_start_date(session, ticker, Timeframe.DAILY, daily_lookback)
    start_d, end_d = _date_range(daily_lookback, start, end)
    df = vnstock_client.fetch_daily(ticker, start_d, end_d)
    if df is None or df.empty:
        return 0
    df = df.copy()
    df["time"] = pd.to_datetime(df["time"])
    rows = [(row["time"].to_pydatetime(), row, None) for _, row in df.iterrows()]
    _upsert_candles(session, ticker, Timeframe.DAILY, rows)
    session.commit()
    logger.info("ingested %d daily candles for %s", len(df), ticker)
    return len(df)


def ingest_half_session(
    session: Session, ticker: str, start: str | None = None, end: str | None = None
) -> int:
    ticker = ticker.upper()
    _, half_lookback = settings_service.get_lookbacks(session)
    start_d, end_d = _date_range(half_lookback, start, end)
    df = vnstock_client.fetch_hourly(ticker, start_d, end_d)
    resampled = resample_half_session(df)
    if resampled.empty:
        return 0
    rows = [
        (row["bucket_start"].to_pydatetime(), row, row["session_part"])
        for _, row in resampled.iterrows()
    ]
    _upsert_candles(session, ticker, Timeframe.HALF_SESSION, rows)
    session.commit()
    logger.info("ingested %d half-session candles for %s", len(resampled), ticker)
    return len(resampled)


def ingest_weekly(session: Session, ticker: str) -> int:
    """Resampled from already-ingested daily candles -- a week is just an
    aggregation of days already in the DB, so this never crawls an external
    source (works identically for stock and crypto tickers)."""
    ticker = ticker.upper()
    daily = session.exec(
        select(Candle)
        .where(Candle.ticker == ticker, Candle.timeframe == Timeframe.DAILY)
        .order_by(Candle.bucket_start)
    ).all()
    if not daily:
        return 0
    df = pd.DataFrame(
        [
            dict(bucket_start=c.bucket_start, open=c.open, high=c.high, low=c.low, close=c.close, volume=c.volume)
            for c in daily
        ]
    )
    resampled = resample_weekly(df)
    if resampled.empty:
        return 0
    rows = [(row["bucket_start"].to_pydatetime(), row, None) for _, row in resampled.iterrows()]
    _upsert_candles(session, ticker, Timeframe.WEEK, rows)
    session.commit()
    logger.info("ingested %d weekly candles for %s", len(resampled), ticker)
    return len(resampled)


EXCHANGE_BINANCE = CryptoExchange.BINANCE
EXCHANGE_KUCOIN = CryptoExchange.KUCOIN
EXCHANGE_MEXC = CryptoExchange.MEXC
EXCHANGE_GECKOTERMINAL = CryptoExchange.GECKOTERMINAL
ALL_CRYPTO_EXCHANGES = CryptoExchange.ALL


def _resolve_dex_pool(session: Session, coin_symbol: str, symbol: Symbol) -> tuple[str, str] | None:
    """Finds (and caches onto ``symbol``) a GeckoTerminal pool for a coin that
    has a CoinGecko id but no known DEX pool yet: CoinGecko platforms -> map
    to a GeckoTerminal network -> most-liquid pool for that token there.
    Cached after the first successful resolve so later refreshes skip
    straight to fetch_ohlcv() instead of repeating this multi-call, heavily
    rate-limited lookup."""
    if not symbol.coingecko_id:
        return None
    try:
        platforms = coingecko_client.fetch_coin_platforms(symbol.coingecko_id)
    except coingecko_client.CrawlError as exc:
        logger.warning("could not fetch platforms for %s: %s", symbol.coingecko_id, exc)
        return None

    for platform_id, address in platforms.items():
        network = geckoterminal_client.NETWORK_ID_MAP.get(platform_id)
        if not network:
            continue
        try:
            pool_address = geckoterminal_client.fetch_token_pools(network, address)
        except geckoterminal_client.CrawlError as exc:
            logger.warning("geckoterminal pool lookup failed for %s/%s: %s", network, address, exc)
            continue
        if pool_address:
            symbol.dex_network = network
            symbol.dex_pool_address = pool_address
            session.add(symbol)
            session.commit()
            return network, pool_address

    logger.info("%s has no resolvable DEX pool on any mapped network", coin_symbol)
    return None


def _fetch_geckoterminal_candles(
    session: Session, coin_symbol: str, timeframe: str, symbol: Symbol | None
) -> pd.DataFrame | None:
    if symbol is None:
        return None
    network, pool_address = symbol.dex_network, symbol.dex_pool_address
    if not (network and pool_address):
        resolved = _resolve_dex_pool(session, coin_symbol, symbol)
        if not resolved:
            return None
        network, pool_address = resolved
    try:
        return geckoterminal_client.fetch_ohlcv(network, pool_address, timeframe)
    except geckoterminal_client.CrawlError as exc:
        logger.warning("geckoterminal ohlcv fetch failed for %s: %s", coin_symbol, exc)
        return None


def ingest_crypto(
    session: Session,
    coin_symbol: str,
    timeframe: str,
    exchange_symbol: str | None = None,
    exchanges: tuple[str, ...] = ALL_CRYPTO_EXCHANGES,
    symbol: Symbol | None = None,
) -> int:
    """OHLCV for a crypto ticker (1h/4h/daily), trying each user-enabled
    exchange in order: Binance, then KuCoin (lists many small/new coins
    Binance doesn't carry), then MEXC (broader still, ~1668 USDT pairs), then
    GeckoTerminal (DEX pools, for coins on none of the centralized exchanges).
    ``symbol`` is the tracked Symbol row, used to read/cache the resolved
    GeckoTerminal pool -- omit only in contexts where GeckoTerminal fallback
    isn't needed (e.g. a symbol not yet tracked).

    ``coin_symbol`` is the storage key (candles are saved under it) and
    ``exchange_symbol`` (defaults to ``coin_symbol`` when omitted) is what's
    actually looked up on an exchange. These differ for crypto promoted from
    the screener, where the storage key is a CoinGecko coin_id (unique) but
    the exchange only knows the human trading symbol (e.g. "PEPE") -- see
    Symbol.display_symbol.
    """
    coin_symbol = coin_symbol.upper()
    lookup_symbol = (exchange_symbol or coin_symbol).upper()
    df = None

    if EXCHANGE_BINANCE in exchanges:
        try:
            df = binance_client.fetch_klines(binance_client.to_pair(lookup_symbol), _BINANCE_INTERVAL[timeframe])
        except binance_client.SymbolNotFoundError:
            logger.info("%s not on Binance, trying next exchange", lookup_symbol)
        except binance_client.CrawlError as exc:
            logger.warning("binance fetch failed for %s, trying next exchange: %s", lookup_symbol, exc)

    if (df is None or df.empty) and EXCHANGE_KUCOIN in exchanges:
        try:
            df = kucoin_client.fetch_klines(kucoin_client.to_pair(lookup_symbol), timeframe)
        except kucoin_client.SymbolNotFoundError:
            logger.info("%s not on KuCoin either, trying next exchange", lookup_symbol)
        except kucoin_client.CrawlError as exc:
            logger.warning("kucoin fetch also failed for %s, trying next exchange: %s", lookup_symbol, exc)

    if (df is None or df.empty) and EXCHANGE_MEXC in exchanges:
        try:
            df = mexc_client.fetch_klines(mexc_client.to_pair(lookup_symbol), timeframe)
        except mexc_client.SymbolNotFoundError:
            logger.info("%s not on MEXC either", lookup_symbol)
        except mexc_client.CrawlError as exc:
            logger.warning("mexc fetch also failed for %s: %s", lookup_symbol, exc)

    if (df is None or df.empty) and EXCHANGE_GECKOTERMINAL in exchanges:
        df = _fetch_geckoterminal_candles(session, lookup_symbol, timeframe, symbol)

    if df is None or df.empty:
        logger.warning("%s has no candle source among enabled exchanges %s", lookup_symbol, exchanges)
        return 0

    rows = [(row["time"].to_pydatetime(), row, None) for _, row in df.iterrows()]
    _upsert_candles(session, coin_symbol, timeframe, rows)
    session.commit()
    logger.info("ingested %d %s candles for %s", len(df), timeframe, coin_symbol)
    return len(df)
