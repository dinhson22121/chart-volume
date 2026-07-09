"""Wyckoff analysis retrieval + on-demand refresh (ingest -> analyse)."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session, select

from app.auth import require_token
from app.db import get_session
from app.models import Analysis, Symbol, Timeframe
from app.services import ingest
from app.services.analysis import run_analysis

router = APIRouter(prefix="/analysis", tags=["analysis"], dependencies=[Depends(require_token)])

_VALID_TIMEFRAMES = {Timeframe.DAILY, Timeframe.HALF_SESSION}


def _analysis_out(a: Analysis) -> dict:
    return {
        "ticker": a.ticker,
        "timeframe": a.timeframe,
        "as_of": a.as_of,
        "phase": a.phase,
        "confidence": a.confidence,
        "signals": json.loads(a.signals_json),
        "levels": json.loads(a.levels_json),
        "narrative": a.narrative,
        "advice": a.advice,
        "created_at": a.created_at,
    }


def _validate_tf(timeframe: str) -> None:
    if timeframe not in _VALID_TIMEFRAMES:
        raise HTTPException(status_code=400, detail=f"invalid timeframe: {timeframe}")


@router.get("/{ticker}")
def get_analysis(
    ticker: str,
    timeframe: str = Query(Timeframe.DAILY),
    session: Session = Depends(get_session),
) -> dict:
    _validate_tf(timeframe)
    row = session.exec(
        select(Analysis)
        .where(Analysis.ticker == ticker.upper(), Analysis.timeframe == timeframe)
        .order_by(Analysis.as_of.desc())
    ).first()
    if not row:
        raise HTTPException(status_code=404, detail="no analysis yet; call refresh first")
    return _analysis_out(row)


@router.post("/{ticker}/refresh")
def refresh_analysis(
    ticker: str,
    timeframe: str = Query(Timeframe.DAILY),
    force: bool = Query(False),
    session: Session = Depends(get_session),
) -> dict:
    _validate_tf(timeframe)
    ticker = ticker.upper()

    # Ensure the symbol is tracked (as watchlist) so it shows up in the sidebar.
    if not session.get(Symbol, ticker):
        session.add(Symbol(ticker=ticker, is_watchlist=True))
        session.commit()

    if timeframe == Timeframe.DAILY:
        ingest.ingest_daily(session, ticker)
    else:
        ingest.ingest_half_session(session, ticker)

    analysis = run_analysis(session, ticker, timeframe, force=force)
    if analysis is None:
        raise HTTPException(status_code=502, detail="could not fetch candles for analysis")
    return _analysis_out(analysis)
