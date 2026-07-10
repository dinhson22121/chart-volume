"""Wyckoff analysis retrieval + on-demand refresh (ingest -> analyse)."""

from __future__ import annotations

import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session, select

from app.auth import require_token
from app.db import get_session
from app.models import AssetClass, Analysis, Symbol, Timeframe
from app.services import ingest, settings_service, sonicr_indicators, trace_service
from app.services.analysis import run_analysis

router = APIRouter(prefix="/analysis", tags=["analysis"], dependencies=[Depends(require_token)])

_STOCK_TIMEFRAMES = {Timeframe.DAILY, Timeframe.HALF_SESSION}
_CRYPTO_TIMEFRAMES = {Timeframe.DAILY, Timeframe.HOUR_1, Timeframe.HOUR_4}
_VALID_TIMEFRAMES = _STOCK_TIMEFRAMES | _CRYPTO_TIMEFRAMES
_TIMEFRAMES_BY_ASSET_CLASS = {AssetClass.STOCK: _STOCK_TIMEFRAMES, AssetClass.CRYPTO: _CRYPTO_TIMEFRAMES}
_TRACE_SUPPORTED_STRATEGIES = {"wyckoff"}


def _analysis_out(a: Analysis) -> dict:
    return {
        "ticker": a.ticker,
        "timeframe": a.timeframe,
        "strategy": a.strategy,
        "as_of": a.as_of,
        "phase": a.phase,
        "confidence": a.confidence,
        "signals": json.loads(a.signals_json),
        "levels": json.loads(a.levels_json),
        "narrative": a.narrative,
        "advice": a.advice,
        "daily_trend": a.daily_trend,
        "mtf_alignment": a.mtf_alignment,
        "created_at": a.created_at,
    }


def _validate_tf(timeframe: str) -> None:
    if timeframe not in _VALID_TIMEFRAMES:
        raise HTTPException(status_code=400, detail=f"invalid timeframe: {timeframe}")


def _latest_signal(signals_json: str) -> dict | None:
    signals = json.loads(signals_json)
    dated = [s for s in signals if s.get("ts")]
    if not dated:
        return None
    latest = max(dated, key=lambda s: s["ts"])
    return {"type": latest["type"], "ts": latest["ts"]}


def _dashboard_row(symbol: Symbol, analysis: Analysis | None) -> dict:
    base = {"ticker": symbol.ticker, "name": symbol.name, "asset_class": symbol.asset_class}
    if analysis is None:
        return {**base, "phase": None, "confidence": None, "as_of": None, "latest_signal": None, "has_data": False}
    return {
        **base,
        "phase": analysis.phase,
        "confidence": analysis.confidence,
        "as_of": analysis.as_of,
        "latest_signal": _latest_signal(analysis.signals_json),
        "has_data": True,
    }


@router.get("/dashboard")
def get_dashboard(session: Session = Depends(get_session)) -> list[dict]:
    """One row per tracked symbol (VN30 + watchlist) using its latest *daily*
    Analysis under the active strategy -- never crawls, just reads what's
    already stored, so opening the dashboard is instant. A symbol that's
    never been refreshed shows up with has_data=False rather than being
    silently omitted."""
    active_strategy = settings_service.get_strategy(session)
    symbols = session.exec(
        select(Symbol).where((Symbol.is_vn30 == True) | (Symbol.is_watchlist == True))  # noqa: E712
    ).all()
    if not symbols:
        return []

    # One query for every tracked symbol's analyses (instead of one query per
    # symbol) -- ordered so the first row seen per ticker is its latest as_of.
    tickers = [s.ticker for s in symbols]
    analyses = session.exec(
        select(Analysis)
        .where(
            Analysis.timeframe == Timeframe.DAILY,
            Analysis.strategy == active_strategy,
            Analysis.ticker.in_(tickers),
        )
        .order_by(Analysis.ticker, Analysis.as_of.desc())
    ).all()
    latest_by_ticker: dict[str, Analysis] = {}
    for a in analyses:
        latest_by_ticker.setdefault(a.ticker, a)

    return [_dashboard_row(symbol, latest_by_ticker.get(symbol.ticker)) for symbol in symbols]


@router.get("/{ticker}")
def get_analysis(
    ticker: str,
    timeframe: str = Query(Timeframe.DAILY),
    session: Session = Depends(get_session),
) -> dict:
    _validate_tf(timeframe)
    active_strategy = settings_service.get_strategy(session)
    row = session.exec(
        select(Analysis)
        .where(
            Analysis.ticker == ticker.upper(),
            Analysis.timeframe == timeframe,
            Analysis.strategy == active_strategy,
        )
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
    # A ticker refreshed for the first time without going through the crypto
    # screener/promote flow defaults to a stock (matches the existing VN30 UX).
    symbol = session.get(Symbol, ticker)
    if not symbol:
        symbol = Symbol(ticker=ticker, is_watchlist=True)
        session.add(symbol)
        session.commit()

    valid_for_asset = _TIMEFRAMES_BY_ASSET_CLASS.get(symbol.asset_class, _STOCK_TIMEFRAMES)
    if timeframe not in valid_for_asset:
        raise HTTPException(
            status_code=400,
            detail=f"timeframe {timeframe} not valid for asset class {symbol.asset_class}",
        )

    exchanges: tuple[str, ...] | None = None
    if symbol.asset_class == AssetClass.CRYPTO:
        exchanges = settings_service.get_crypto_exchanges(session)
        ingest.ingest_crypto(session, ticker, timeframe, exchanges=exchanges, symbol=symbol)
    elif timeframe == Timeframe.DAILY:
        ingest.ingest_daily(session, ticker)
    else:
        ingest.ingest_half_session(session, ticker)

    analysis = run_analysis(session, ticker, timeframe, force=force)
    if analysis is None:
        if exchanges is not None:
            detail = (
                f"{ticker} chưa niêm yết trên sàn đã bật ({', '.join(exchanges)}). "
                "Thử bật thêm sàn (kể cả GeckoTerminal cho coin trên DEX) trong Cài đặt, "
                "hoặc bỏ theo dõi mã này."
            )
        else:
            detail = "could not fetch candles for analysis"
        raise HTTPException(status_code=502, detail=detail)
    return _analysis_out(analysis)


@router.get("/{ticker}/indicators")
def get_indicators(
    ticker: str,
    timeframe: str = Query(Timeframe.DAILY),
    session: Session = Depends(get_session),
) -> dict:
    """Sonic R's per-bar Dragon/T3 series for chart overlay -- computed fresh
    from stored candles every call (cheap, no persistence needed)."""
    _validate_tf(timeframe)
    return sonicr_indicators.get_indicator_series(session, ticker, timeframe)


@router.get("/{ticker}/trace")
def get_trace(
    ticker: str,
    bar_ts: datetime,
    timeframe: str = Query(Timeframe.DAILY),
    session: Session = Depends(get_session),
) -> dict:
    _validate_tf(timeframe)
    active_strategy = settings_service.get_strategy(session)
    if active_strategy not in _TRACE_SUPPORTED_STRATEGIES:
        raise HTTPException(
            status_code=400, detail=f"decision tracing not supported for strategy: {active_strategy}"
        )
    traces = trace_service.get_bar_trace(session, ticker, timeframe, bar_ts)
    if traces is None:
        raise HTTPException(status_code=404, detail="no candle found at that timestamp")
    return {
        "ticker": ticker.upper(),
        "timeframe": timeframe,
        "bar_ts": bar_ts,
        "detectors": [
            {
                "type": t.type,
                "matched": t.matched,
                "checks": [
                    {"label": c.label, "passed": c.passed, "detail": c.detail} for c in t.checks
                ],
            }
            for t in traces
        ],
    }
