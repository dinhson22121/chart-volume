"""Buy-and-hold return: the reference point trade-scenario P&L actually needs
to mean something. R-multiple/expectancy against a random-entry baseline
(see app.services.baseline) answers "does this signal beat noise" -- it does
NOT answer "would I have done better just buying and holding the same
ticker over the same period", which is the real opportunity cost a trader
who could otherwise do nothing is giving up.
"""

from __future__ import annotations

from sqlmodel import Session, select

from app.models import Candle


def buy_hold_return(
    session: Session, ticker: str, timeframe: str, start_ts, end_ts
) -> float | None:
    """Return of holding ``ticker`` from the first candle at/after
    ``start_ts`` to the last candle at/before ``end_ts``. None if there's no
    candle on either side (missing data) or the window collapses to a single
    bar (nothing to measure)."""
    start_candle = session.exec(
        select(Candle)
        .where(Candle.ticker == ticker, Candle.timeframe == timeframe, Candle.bucket_start >= start_ts)
        .order_by(Candle.bucket_start)
    ).first()
    end_candle = session.exec(
        select(Candle)
        .where(Candle.ticker == ticker, Candle.timeframe == timeframe, Candle.bucket_start <= end_ts)
        .order_by(Candle.bucket_start.desc())
    ).first()
    if start_candle is None or end_candle is None or start_candle.bucket_start >= end_candle.bucket_start:
        return None
    if not start_candle.close:
        return None
    return (end_candle.close - start_candle.close) / start_candle.close
