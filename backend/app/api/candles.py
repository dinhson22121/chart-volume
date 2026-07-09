"""Read stored candles for charting."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlmodel import Session, select

from app.auth import require_token
from app.db import get_session
from app.models import Candle, Timeframe

router = APIRouter(prefix="/candles", tags=["candles"], dependencies=[Depends(require_token)])


@router.get("/{ticker}")
def get_candles(
    ticker: str,
    timeframe: str = Query(Timeframe.DAILY),
    limit: int = Query(500, ge=1, le=2000),
    session: Session = Depends(get_session),
) -> list[Candle]:
    rows = session.exec(
        select(Candle)
        .where(Candle.ticker == ticker.upper(), Candle.timeframe == timeframe)
        .order_by(Candle.bucket_start.desc())
        .limit(limit)
    ).all()
    return list(reversed(rows))  # chronological for the chart
