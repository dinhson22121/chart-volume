"""Regime classification from recent Sonic R events.

Mirrors app.wyckoff.phase.classify_phase's shape: recent Dragon/Sonic-cross/
entry signals in a trailing window decide Uptrend vs Downtrend vs Ranging,
with an optional higher-timeframe daily_trend nudging confidence and
suppressing counter-trend "raw" signals from driving the call.
"""

from __future__ import annotations

import pandas as pd

from app.sonicr.events import (
    DRAGON_CROSS_DOWN,
    DRAGON_CROSS_UP,
    SONIC_CROSS_DOWN,
    SONIC_CROSS_UP,
    SONIC_ENTRY_LONG,
    SONIC_ENTRY_SHORT,
    SonicEvent,
)

RECENT_WINDOW = 10
_BASE_CONFIDENCE = 0.4
_PER_SIGNAL = 0.15
_MAX_CONFIDENCE = 0.9
_MIN_CONFIDENCE = 0.1
_MTF_BONUS = 0.1
_MTF_PENALTY = 0.15

PHASE_UPTREND = "Uptrend"
PHASE_DOWNTREND = "Downtrend"
PHASE_RANGING = "Ranging"

TREND_BULLISH = "bullish"
TREND_BEARISH = "bearish"
TREND_NEUTRAL = "neutral"

MTF_ALIGNED = "aligned"
MTF_CONFLICTING = "conflicting"

# SonicEntryLong/Short are the "optimized" confirmed entries and always count
# toward their side; DragonCross/SonicCross are raw momentum signals, weaker
# on their own -- same two-tier weighting as Wyckoff's SOS/SOW vs
# Spring/Upthrust/climax signals.
BULLISH_EVENTS = {DRAGON_CROSS_UP, SONIC_CROSS_UP, SONIC_ENTRY_LONG}
BEARISH_EVENTS = {DRAGON_CROSS_DOWN, SONIC_CROSS_DOWN, SONIC_ENTRY_SHORT}

_PHASE_TREND = {
    PHASE_UPTREND: TREND_BULLISH,
    PHASE_DOWNTREND: TREND_BEARISH,
    PHASE_RANGING: TREND_NEUTRAL,
}


def phase_trend(phase: str) -> str:
    return _PHASE_TREND.get(phase, TREND_NEUTRAL)


def classify_regime(
    df: pd.DataFrame,
    events: list[SonicEvent],
    daily_trend: str | None = None,
) -> tuple[str, float, list[str], str | None]:
    """Returns (phase, confidence, drivers, mtf_alignment)."""
    n = len(df)
    recent = [e for e in events if e.index >= n - RECENT_WINDOW]
    recent_types = [e.type for e in recent]

    filtered_types = recent_types
    if daily_trend == TREND_BEARISH:
        filtered_types = [t for t in recent_types if t not in BULLISH_EVENTS]
    elif daily_trend == TREND_BULLISH:
        filtered_types = [t for t in recent_types if t not in BEARISH_EVENTS]

    bullish_hits = [t for t in filtered_types if t in BULLISH_EVENTS]
    bearish_hits = [t for t in filtered_types if t in BEARISH_EVENTS]

    if bearish_hits and len(bearish_hits) >= len(bullish_hits):
        phase, drivers, count = PHASE_DOWNTREND, bearish_hits, len(bearish_hits)
    elif bullish_hits:
        phase, drivers, count = PHASE_UPTREND, bullish_hits, len(bullish_hits)
    else:
        return PHASE_RANGING, _BASE_CONFIDENCE, [], None

    confidence = _BASE_CONFIDENCE + _PER_SIGNAL * count

    mtf_alignment: str | None = None
    if daily_trend is not None and daily_trend != TREND_NEUTRAL:
        this_trend = phase_trend(phase)
        if this_trend == daily_trend:
            confidence += _MTF_BONUS
            mtf_alignment = MTF_ALIGNED
        elif this_trend != TREND_NEUTRAL:
            confidence -= _MTF_PENALTY
            mtf_alignment = MTF_CONFLICTING

    confidence = max(_MIN_CONFIDENCE, min(_MAX_CONFIDENCE, confidence))
    return phase, round(confidence, 2), drivers, mtf_alignment
