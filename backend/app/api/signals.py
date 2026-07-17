"""Aggregate signal-quality stats (win rate / avg forward return per event type)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlmodel import Session

from app.auth import require_token
from app.db import get_session
from app.services import settings_service, signal_outcomes

router = APIRouter(prefix="/signals", tags=["signals"], dependencies=[Depends(require_token)])


@router.get("/stats")
def get_signal_stats(
    ticker: str | None = Query(default=None),
    timeframe: str | None = Query(default=None),
    strategy: str | None = Query(default=None, description="Defaults to the currently active strategy"),
    aligned_only: bool = Query(default=False, description="Only signals aligned with the engine's trend"),
    asset_class: str | None = Query(default=None, description="Filter to 'stock' or 'crypto'"),
    session: Session = Depends(get_session),
) -> list[dict]:
    active_strategy = strategy or settings_service.get_strategy(session)
    return signal_outcomes.get_stats(
        session, ticker, timeframe, active_strategy, aligned_only=aligned_only, asset_class=asset_class
    )
