"""Crawl -> upsert candles. Idempotent: re-running never duplicates rows."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

import pandas as pd
from sqlmodel import Session, select

from app.crawler import vnstock_client
from app.crawler.resample import resample_half_session
from app.models import Candle, Timeframe

logger = logging.getLogger("chart_volume.ingest")

# Daily needs enough history for Wyckoff context; intraday depth is limited.
DAILY_LOOKBACK_DAYS = 730
HALF_SESSION_LOOKBACK_DAYS = 60


def _date_range(lookback_days: int, start: str | None, end: str | None) -> tuple[str, str]:
    end_d = end or date.today().isoformat()
    start_d = start or (date.today() - timedelta(days=lookback_days)).isoformat()
    return start_d, end_d


def _upsert_candle(
    session: Session,
    ticker: str,
    timeframe: str,
    bucket_start: datetime,
    row: pd.Series,
    session_part: str | None,
) -> None:
    existing = session.exec(
        select(Candle).where(
            Candle.ticker == ticker,
            Candle.timeframe == timeframe,
            Candle.bucket_start == bucket_start,
        )
    ).first()

    values = dict(
        open=float(row["open"]),
        high=float(row["high"]),
        low=float(row["low"]),
        close=float(row["close"]),
        volume=float(row["volume"]),
        session_part=session_part,
    )
    if existing:
        for key, val in values.items():
            setattr(existing, key, val)
        session.add(existing)
    else:
        session.add(
            Candle(
                ticker=ticker,
                timeframe=timeframe,
                bucket_start=bucket_start,
                **values,
            )
        )


def ingest_daily(
    session: Session, ticker: str, start: str | None = None, end: str | None = None
) -> int:
    ticker = ticker.upper()
    start_d, end_d = _date_range(DAILY_LOOKBACK_DAYS, start, end)
    df = vnstock_client.fetch_daily(ticker, start_d, end_d)
    if df is None or df.empty:
        return 0
    df = df.copy()
    df["time"] = pd.to_datetime(df["time"])
    for _, row in df.iterrows():
        _upsert_candle(
            session, ticker, Timeframe.DAILY, row["time"].to_pydatetime(), row, None
        )
    session.commit()
    logger.info("ingested %d daily candles for %s", len(df), ticker)
    return len(df)


def ingest_half_session(
    session: Session, ticker: str, start: str | None = None, end: str | None = None
) -> int:
    ticker = ticker.upper()
    start_d, end_d = _date_range(HALF_SESSION_LOOKBACK_DAYS, start, end)
    df = vnstock_client.fetch_hourly(ticker, start_d, end_d)
    resampled = resample_half_session(df)
    if resampled.empty:
        return 0
    for _, row in resampled.iterrows():
        _upsert_candle(
            session,
            ticker,
            Timeframe.HALF_SESSION,
            row["bucket_start"].to_pydatetime(),
            row,
            row["session_part"],
        )
    session.commit()
    logger.info("ingested %d half-session candles for %s", len(resampled), ticker)
    return len(resampled)
