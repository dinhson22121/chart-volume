"""Run the Wyckoff engine over stored candles + optional Claude narrative.

Caches by (ticker, timeframe, as_of): once a candle bucket has been analysed we
don't re-invoke the LLM unless ``force=True``. This keeps Claude cost bounded to
"one call per new candle".
"""

from __future__ import annotations

import json
import logging

from sqlmodel import Session, select

from app import wyckoff
from app.ai import narrative as narrative_mod
from app.models import Analysis, Candle
from app.wyckoff import AnalysisResult

logger = logging.getLogger("chart_volume.analysis")

_RECENT_FOR_PROMPT = 12
_NO_AI_PHASES = {"Insufficient data"}


def _load_candles(session: Session, ticker: str, timeframe: str) -> list[Candle]:
    return session.exec(
        select(Candle)
        .where(Candle.ticker == ticker, Candle.timeframe == timeframe)
        .order_by(Candle.bucket_start)
    ).all()


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

    result = wyckoff.analyze(candles)
    as_of = result.as_of

    existing = session.exec(
        select(Analysis).where(
            Analysis.ticker == ticker,
            Analysis.timeframe == timeframe,
            Analysis.as_of == as_of,
        )
    ).first()
    if existing and not force:
        return existing  # cached: same candle bucket already analysed

    narrative_text: str | None = None
    advice_text: str | None = None
    if use_ai and result.phase not in _NO_AI_PHASES and narrative_mod.is_available():
        try:
            narrative_text, advice_text = narrative_mod.generate(
                ticker, timeframe, result, candles[-_RECENT_FOR_PROMPT:]
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
        session.add(existing)
        session.commit()
        session.refresh(existing)
        return existing

    analysis = Analysis(
        ticker=ticker,
        timeframe=timeframe,
        as_of=as_of,
        phase=result.phase,
        confidence=result.confidence,
        signals_json=signals_json,
        levels_json=levels_json,
        narrative=narrative_text,
        advice=advice_text,
    )
    session.add(analysis)
    session.commit()
    session.refresh(analysis)
    return analysis
