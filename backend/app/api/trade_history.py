"""Trade History: every TradeScenario ever created, across all tickers, plus
summary win-rate/avg P&L stats -- unlike app.services.trade_scenario.get_scenario
(scoped to one ticker/timeframe/strategy, active-or-latest only)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session

from app.auth import require_token
from app.db import get_session
from app.models import Timeframe, TradeScenario
from app.services import analysis as analysis_svc
from app.services import config_version as config_version_mod
from app.services import settings_service, trade_scenario
from app.strategies import registry as strategy_registry
from app.validation import is_valid_ticker

router = APIRouter(prefix="/trade-history", tags=["trade-history"], dependencies=[Depends(require_token)])

DEFAULT_PAGE_SIZE = 50
_SOURCE_DESCRIPTION = (
    "'live' (default, real-time-tracked only), 'backtest' (historical replay only), or 'all' (both pooled)"
)
_VALID_TIMEFRAMES = {Timeframe.DAILY, Timeframe.HALF_SESSION, Timeframe.HOUR_1, Timeframe.HOUR_4, Timeframe.WEEK}


def _resolve_source(source: str) -> str | None:
    return None if source == "all" else source


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
        "price_limit_caution": s.price_limit_caution,
        "source": s.source,
    }


@router.get("")
def get_trade_history(
    page: int = Query(1, ge=1),
    page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=200),
    ticker: str | None = None,
    status: str | None = None,
    strategy: str | None = None,
    asset_class: str | None = Query(default=None, description="Filter to 'stock' or 'crypto'"),
    source: str = Query(default="live", description=_SOURCE_DESCRIPTION),
    is_bullish: bool | None = Query(default=None, description="Filter to long (true) or short (false) scenarios"),
    session: Session = Depends(get_session),
) -> dict:
    items, total = trade_scenario.list_scenarios(
        session, page, page_size, ticker, status, strategy, asset_class, _resolve_source(source), is_bullish
    )
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
    asset_class: str | None = Query(default=None, description="Filter to 'stock' or 'crypto'"),
    source: str = Query(default="live", description=_SOURCE_DESCRIPTION),
    is_bullish: bool | None = Query(default=None, description="Filter to long (true) or short (false) scenarios"),
    session: Session = Depends(get_session),
) -> dict:
    # "Current config version" only means something for a single named
    # strategy -- with no strategy filter, scenarios from several strategies
    # (each with their own thresholds/version) are pooled together.
    current_config_version = None
    if strategy:
        current_config_version = config_version_mod.compute(
            strategy, settings_service.get_strategy_config(session, strategy)
        )
    return trade_scenario.get_scenario_stats(
        session, ticker, strategy, asset_class, current_config_version, _resolve_source(source), is_bullish
    )


@router.post("/backtest")
def trigger_backtest(
    ticker: str,
    timeframe: str = Query(Timeframe.DAILY),
    strategy: str | None = None,
    session: Session = Depends(get_session),
) -> dict:
    """Replays candle history already sitting in the DB for ``ticker``/
    ``timeframe`` (see app.services.analysis.run_scenario_backtest), so the
    Trust Layer stats don't have to wait weeks/months of live tracking to
    accumulate a large enough sample. Never ingests new candles -- 0 created
    means either no candles yet for this ticker/timeframe, or nothing in that
    history qualified as a tradeable event."""
    ticker = ticker.upper()
    if not is_valid_ticker(ticker):
        raise HTTPException(status_code=400, detail="invalid ticker")
    if timeframe not in _VALID_TIMEFRAMES:
        raise HTTPException(status_code=400, detail=f"invalid timeframe {timeframe}")
    if strategy is not None and not strategy_registry.is_known(strategy):
        raise HTTPException(status_code=400, detail=f"unknown strategy {strategy}")

    created = analysis_svc.run_scenario_backtest(session, ticker, timeframe, strategy)
    return {"ticker": ticker, "timeframe": timeframe, "created": created}
