"""Read stored candles for charting."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session, select

from app.auth import require_token
from app.db import get_session
from app.models import Candle, Timeframe
from app.validation import is_valid_ticker

router = APIRouter(prefix="/candles", tags=["candles"], dependencies=[Depends(require_token)])


@router.get("/{ticker}")
def get_candles(
    ticker: str,
    timeframe: str = Query(Timeframe.DAILY),
    limit: int = Query(500, ge=1, le=2000),
    session: Session = Depends(get_session),
) -> list[Candle]:
    ticker = ticker.upper()
    if not is_valid_ticker(ticker):
        raise HTTPException(status_code=400, detail="invalid ticker")
    rows = session.exec(
        select(Candle)
        .where(Candle.ticker == ticker, Candle.timeframe == timeframe)
        .order_by(Candle.bucket_start.desc())
        .limit(limit)
    ).all()
    return list(reversed(rows))  # chronological for the chart
