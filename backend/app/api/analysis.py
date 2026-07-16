"""Wyckoff analysis retrieval + on-demand refresh (ingest -> analyse)."""

from __future__ import annotations

import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session, select

from app.auth import require_token
from app.db import get_session
from app.models import AssetClass, Analysis, Symbol, Timeframe
from app.services import ingest, settings_service, signal_outcomes, sonicr_indicators, trace_service
from app.strategies import registry as strategy_registry
from app.services.analysis import run_analysis

router = APIRouter(prefix="/analysis", tags=["analysis"], dependencies=[Depends(require_token)])

_STOCK_TIMEFRAMES = {Timeframe.DAILY, Timeframe.HALF_SESSION}
_CRYPTO_TIMEFRAMES = {Timeframe.DAILY, Timeframe.HOUR_1, Timeframe.HOUR_4}
_VALID_TIMEFRAMES = _STOCK_TIMEFRAMES | _CRYPTO_TIMEFRAMES
_TIMEFRAMES_BY_ASSET_CLASS = {AssetClass.STOCK: _STOCK_TIMEFRAMES, AssetClass.CRYPTO: _CRYPTO_TIMEFRAMES}
_TRACE_SUPPORTED_STRATEGIES = {"wyckoff"}

# Pegged assets whose price never trends: an "Accumulation"/"Bullish
# Structure" phase on a stablecoin is statistical noise around the peg, not
# an opportunity, so they're excluded from the dashboard's bullish ranking
# (still listed and analyzable -- only is_bullish is forced off). Matched by
# display_symbol; a symbol-name list beats a coingecko-id list here because
# the top100 seed and manual adds both normalize display_symbol the same way.
_STABLECOIN_SYMBOLS = {
    "USDT", "USDC", "DAI", "USDE", "FDUSD", "PYUSD", "USDD", "USDS", "TUSD", "BUSD",
    "FRAX", "GUSD", "USDP", "EURC", "EURT", "USD1", "USDT0", "USDF", "RLUSD", "USDG", "USDX",
}


def _is_stablecoin(symbol: Symbol) -> bool:
    return (symbol.display_symbol or symbol.ticker).upper() in _STABLECOIN_SYMBOLS


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


# A signal type needs at least this many settled 10-bar outcomes before its
# historical win rate is trusted enough to influence the opportunity score --
# below that, one lucky/unlucky trade would swing the rate wildly.
_MIN_OUTCOMES_FOR_WIN_RATE = 5


# Historical expectancy (avg 10-bar return) that maps to a full-marks edge
# score of 1.0. A signal type averaging +4% over 10 bars is treated as a
# maximally strong historical edge; expectancy scales linearly up to it and
# clamps. Expectancy beats win rate for ranking because a trend-following
# signal can win <50% of the time yet still have strong positive expectancy
# (big wins, small losses) -- win rate alone would unfairly bury it.
_EXPECTANCY_FULL_MARKS = 0.04


def _edge_from_expectancy(avg_return_10: float) -> float:
    return max(0.0, min(1.0, avg_return_10 / _EXPECTANCY_FULL_MARKS))


def _opportunity_score(
    analysis: Analysis, latest_signal: dict | None, edge_by_type: dict[str, float]
) -> float:
    """Confidence, tie-broken by historical expectancy: when the row's latest
    signal type has a trustworthy positive-expectancy track record, blend its
    edge score in 50/50 so two coins with the same phase confidence rank by
    how well that signal has actually paid off before."""
    edge = edge_by_type.get(latest_signal["type"]) if latest_signal else None
    if edge is None:
        return round(analysis.confidence, 3)
    return round(0.5 * analysis.confidence + 0.5 * edge, 3)


def _dashboard_row(
    symbol: Symbol, analysis: Analysis | None, strategy_module, edge_by_type: dict[str, float]
) -> dict:
    base = {
        "ticker": symbol.ticker,
        "display_symbol": symbol.display_symbol or symbol.ticker,
        "name": symbol.name,
        "asset_class": symbol.asset_class,
    }
    if analysis is None:
        return {
            **base, "phase": None, "confidence": None, "as_of": None, "latest_signal": None,
            "has_data": False, "is_bullish": None, "opportunity_score": None,
        }
    latest_signal = _latest_signal(analysis.signals_json)
    return {
        **base,
        "phase": analysis.phase,
        "confidence": analysis.confidence,
        "as_of": analysis.as_of,
        "latest_signal": latest_signal,
        "has_data": True,
        "is_bullish": (
            strategy_module.phase_trend(analysis.phase) == "bullish" and not _is_stablecoin(symbol)
        ),
        "opportunity_score": _opportunity_score(analysis, latest_signal, edge_by_type),
    }


@router.get("/dashboard")
def get_dashboard(session: Session = Depends(get_session)) -> list[dict]:
    """One row per tracked symbol (VN30 + watchlist + Top100 crypto) using its
    latest *daily* Analysis under the active strategy -- never crawls, just
    reads what's already stored, so opening the dashboard is instant. A
    symbol that's never been refreshed shows up with has_data=False rather
    than being silently omitted."""
    active_strategy = settings_service.get_strategy(session)
    strategy_module = strategy_registry.REGISTRY.get(
        active_strategy, strategy_registry.REGISTRY[strategy_registry.DEFAULT_STRATEGY]
    )
    symbols = session.exec(
        select(Symbol).where(
            (Symbol.is_vn30 == True) | (Symbol.is_watchlist == True) | (Symbol.is_top100 == True)  # noqa: E712
        )
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

    # Historical per-signal-type expectancy edge (10-bar horizon, daily,
    # active strategy, aligned signals only, aggregated across all tickers) --
    # computed once for the whole dashboard, used to tie-break rows that share
    # the same confidence.
    edge_by_type = {
        s["type"]: _edge_from_expectancy(s["avg_return_10"])
        for s in signal_outcomes.get_stats(
            session, timeframe=Timeframe.DAILY, strategy=active_strategy, aligned_only=True
        )
        if s["avg_return_10"] is not None and s["n_10"] >= _MIN_OUTCOMES_FOR_WIN_RATE
    }

    return [
        _dashboard_row(symbol, latest_by_ticker.get(symbol.ticker), strategy_module, edge_by_type)
        for symbol in symbols
    ]


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
        symbol = Symbol(ticker=ticker, display_symbol=ticker, is_watchlist=True)
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
        ingest.ingest_crypto(
            session, ticker, timeframe, exchange_symbol=symbol.display_symbol, exchanges=exchanges, symbol=symbol
        )
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
