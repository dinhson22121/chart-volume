"""User-drawn trend lines on a ticker's chart -- freeform annotations the
user adds themselves (not a strategy signal), persisted per (ticker,
timeframe) so they're still there next time that chart is opened."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlmodel import Session, select

from app.auth import require_token
from app.db import get_session
from app.models import UserDrawing

router = APIRouter(prefix="/drawings", tags=["drawings"], dependencies=[Depends(require_token)])


class DrawingPoint(BaseModel):
    time: str  # candle bucket_start ISO string -- matches Candle.bucket_start, not a raw chart pixel
    price: float


class DrawingShape(BaseModel):
    points: list[DrawingPoint]
    color: str = "#4fc3f7"


class DrawingsIn(BaseModel):
    shapes: list[DrawingShape]


def _row(session: Session, ticker: str, timeframe: str) -> UserDrawing | None:
    return session.exec(
        select(UserDrawing).where(UserDrawing.ticker == ticker, UserDrawing.timeframe == timeframe)
    ).first()


@router.get("/{ticker}")
def get_drawings(ticker: str, timeframe: str = Query(...), session: Session = Depends(get_session)) -> dict:
    row = _row(session, ticker.upper(), timeframe)
    return {"shapes": json.loads(row.shapes_json) if row else []}


@router.put("/{ticker}")
def save_drawings(
    ticker: str, body: DrawingsIn, timeframe: str = Query(...), session: Session = Depends(get_session)
) -> dict:
    ticker = ticker.upper()
    shapes_json = json.dumps([s.model_dump() for s in body.shapes])
    row = _row(session, ticker, timeframe)
    if row:
        row.shapes_json = shapes_json
        row.updated_at = datetime.now(timezone.utc)
    else:
        row = UserDrawing(ticker=ticker, timeframe=timeframe, shapes_json=shapes_json)
    session.add(row)
    session.commit()
    return {"shapes": json.loads(shapes_json)}
