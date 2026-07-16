"""Run the Wyckoff engine over stored candles + optional Claude narrative.

Caches by (ticker, timeframe, as_of): once a candle bucket has been analysed we
don't re-invoke the LLM unless ``force=True``. This keeps Claude cost bounded to
"one call per new candle". Signal-outcome recording (see signal_outcomes.py)
and multi-timeframe context, however, run on every call since they're cheap
deterministic computation, not an LLM call.
"""

from __future__ import annotations

import json
import logging

from sqlmodel import Session, select

from app.ai import narrative as narrative_mod
from app.models import Analysis, Candle, Timeframe
from app.services import settings_service, signal_outcomes
from app.strategies import registry as strategy_registry
from app.wyckoff import AnalysisResult

logger = logging.getLogger("chart_volume.analysis")

_RECENT_FOR_PROMPT = 12
_NO_AI_PHASES = {"Insufficient data"}
# Intraday timeframes that use the same ticker's daily phase as MTF context:
# half_session for stocks, 1h/4h for crypto.
_INTRADAY_TIMEFRAMES = {Timeframe.HALF_SESSION, Timeframe.HOUR_1, Timeframe.HOUR_4}


def _load_candles(session: Session, ticker: str, timeframe: str) -> list[Candle]:
    return session.exec(
        select(Candle)
        .where(Candle.ticker == ticker, Candle.timeframe == timeframe)
        .order_by(Candle.bucket_start)
    ).all()


def _get_daily_trend(session: Session, ticker: str, strategy: str, strategy_module) -> str | None:
    """Latest daily-timeframe phase for this ticker (same strategy), as a
    bullish/bearish/neutral trend, for use as multi-timeframe context on
    intraday analysis (half_session for stocks, 1h/4h for crypto). None if
    daily hasn't been analysed yet.

    ``strategy_module.phase_trend`` (not a single hardcoded Wyckoff import) is
    used because each strategy owns its own phase vocabulary -- Sonic R's
    "Uptrend"/"Downtrend"/"Ranging" would silently map to "neutral" under
    Wyckoff's phase_trend, defeating any MTF-alignment gate that relies on it.
    """
    latest_daily = session.exec(
        select(Analysis)
        .where(
            Analysis.ticker == ticker,
            Analysis.timeframe == Timeframe.DAILY,
            Analysis.strategy == strategy,
        )
        .order_by(Analysis.as_of.desc())
    ).first()
    if not latest_daily:
        return None
    return strategy_module.phase_trend(latest_daily.phase)


def _serialize(result: AnalysisResult) -> tuple[str, str]:
    signals_json = json.dumps(result.events_as_dicts(), ensure_ascii=False)
    levels_json = json.dumps(
        {"support": result.levels.support, "resistance": result.levels.resistance},
        ensure_ascii=False,
    )
    return signals_json, levels_json


def run_analysis(
    session: Session,
    ticker: str,
    timeframe: str,
    use_ai: bool = True,
    force: bool = False,
) -> Analysis | None:
    ticker = ticker.upper()
    candles = _load_candles(session, ticker, timeframe)
    if not candles:
        logger.info("no candles for %s/%s, skipping analysis", ticker, timeframe)
        return None

    strategy = settings_service.get_strategy(session)
    strategy_module = strategy_registry.get_strategy(strategy)
    strategy_cfg = settings_service.get_strategy_config(session, strategy)
    daily_trend = (
        _get_daily_trend(session, ticker, strategy, strategy_module)
        if timeframe in _INTRADAY_TIMEFRAMES
        else None
    )
    language = settings_service.get_language(session)
    result = strategy_module.analyze(candles, strategy_cfg, daily_trend, language)
    as_of = result.as_of

    # Cheap deterministic bookkeeping: run on every call, independent of the
    # narrative cache below, so forward returns keep backfilling as new
    # candles arrive even when the LLM step is skipped (cached or no AI).
    signal_outcomes.record_outcomes(
        session, ticker, timeframe, strategy, candles, result.events, strategy_module.BULLISH_EVENTS,
        phase_trend=strategy_module.phase_trend(result.phase),
    )

    existing = session.exec(
        select(Analysis).where(
            Analysis.ticker == ticker,
            Analysis.timeframe == timeframe,
            Analysis.strategy == strategy,
            Analysis.as_of == as_of,
        )
    ).first()
    if existing and not force:
        return existing  # cached: same candle bucket already analysed

    narrative_text: str | None = None
    advice_text: str | None = None
    sub_agents_json: str | None = None
    provider_cfg = settings_service.get_narrative_config(session)
    if use_ai and result.phase not in _NO_AI_PHASES and narrative_mod.is_available(provider_cfg):
        try:
            strategy_label = strategy_registry.LABELS.get(strategy, strategy)
            narrative_text, advice_text, sub_agents_json = narrative_mod.generate(
                ticker, timeframe, result, candles[-_RECENT_FOR_PROMPT:], provider_cfg, strategy_label
            )
        except Exception as exc:  # noqa: BLE001 - never let LLM failure break analysis
            logger.warning("narrative generation failed for %s/%s: %s", ticker, timeframe, exc)

    signals_json, levels_json = _serialize(result)

    if existing:  # force re-run: update in place
        existing.phase = result.phase
        existing.confidence = result.confidence
        existing.signals_json = signals_json
        existing.levels_json = levels_json
        existing.narrative = narrative_text
        existing.advice = advice_text
        existing.sub_agents_json = sub_agents_json
        existing.daily_trend = result.daily_trend
        existing.mtf_alignment = result.mtf_alignment
        session.add(existing)
        session.commit()
        session.refresh(existing)
        return existing

    analysis = Analysis(
        ticker=ticker,
        timeframe=timeframe,
        strategy=strategy,
        as_of=as_of,
        phase=result.phase,
        confidence=result.confidence,
        signals_json=signals_json,
        levels_json=levels_json,
        narrative=narrative_text,
        advice=advice_text,
        sub_agents_json=sub_agents_json,
        daily_trend=result.daily_trend,
        mtf_alignment=result.mtf_alignment,
    )
    session.add(analysis)
    session.commit()
    session.refresh(analysis)
    return analysis
