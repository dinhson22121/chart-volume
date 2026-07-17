"""Trade History: every TradeScenario ever created, across all tickers, plus
summary win-rate/avg P&L stats -- unlike app.services.trade_scenario.get_scenario
(scoped to one ticker/timeframe/strategy, active-or-latest only)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlmodel import Session

from app.auth import require_token
from app.db import get_session
from app.models import TradeScenario
from app.services import trade_scenario

router = APIRouter(prefix="/trade-history", tags=["trade-history"], dependencies=[Depends(require_token)])

DEFAULT_PAGE_SIZE = 50


def _scenario_out(s: TradeScenario) -> dict:
    return {
        "id": s.id,
        "ticker": s.ticker,
        "timeframe": s.timeframe,
        "strategy": s.strategy,
        "event_type": s.event_type,
        "event_ts": s.event_ts,
        "is_bullish": s.is_bullish,
        "entry": s.entry,
        "stop_loss": s.stop_loss,
        "take_profit": s.take_profit,
        "max_bars": s.max_bars,
        "status": s.status,
        "close_reason": s.close_reason,
        "closed_at": s.closed_at,
    }


@router.get("")
def get_trade_history(
    page: int = Query(1, ge=1),
    page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=200),
    ticker: str | None = None,
    status: str | None = None,
    strategy: str | None = None,
    session: Session = Depends(get_session),
) -> dict:
    items, total = trade_scenario.list_scenarios(session, page, page_size, ticker, status, strategy)
    return {
        "items": [_scenario_out(s) for s in items],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/stats")
def get_trade_history_stats(
    ticker: str | None = None,
    strategy: str | None = None,
    session: Session = Depends(get_session),
) -> dict:
    return trade_scenario.get_scenario_stats(session, ticker, strategy)
