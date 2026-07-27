"""Structure classification from recent SMC events.

Mirrors app.wyckoff.phase.classify_phase's priority shape: CHoCH (the
character-change/reversal signal) always drives the call when present in the
recent window -- like Wyckoff's SOS/SOW -- while BOS/Order-Block/FVG act as
weaker "supporting" drivers, only deciding the phase on their own when no
CHoCH has fired recently.
"""

from __future__ import annotations

import pandas as pd

from app.smc.events import (
    BEARISH_FVG,
    BEARISH_OB,
    BOS_BEAR,
    BOS_BULL,
    CHOCH_BEAR,
    CHOCH_BULL,
    BULLISH_FVG,
    BULLISH_OB,
    SMCEvent,
    SWING_BEARISH_OB,
    SWING_BOS_BEAR,
    SWING_BOS_BULL,
    SWING_BULLISH_OB,
    SWING_CHOCH_BEAR,
    SWING_CHOCH_BULL,
)

RECENT_WINDOW = 10
_BASE_CONFIDENCE = 0.4
_PER_SIGNAL = 0.15
_MAX_CONFIDENCE = 0.9
_MIN_CONFIDENCE = 0.1
_MTF_BONUS = 0.1
_MTF_PENALTY = 0.15

PHASE_BULLISH = "Bullish Structure"
PHASE_BEARISH = "Bearish Structure"
PHASE_RANGING = "Ranging"

TREND_BULLISH = "bullish"
TREND_BEARISH = "bearish"
TREND_NEUTRAL = "neutral"

MTF_ALIGNED = "aligned"
MTF_CONFLICTING = "conflicting"

_BULLISH_SUPPORT = {BOS_BULL, BULLISH_OB, BULLISH_FVG, SWING_BOS_BULL, SWING_BULLISH_OB}
_BEARISH_SUPPORT = {BOS_BEAR, BEARISH_OB, BEARISH_FVG, SWING_BOS_BEAR, SWING_BEARISH_OB}
# Swing (major) CHoCH is a "driving" reversal signal exactly like the
# internal tier's CHoCH -- see classify_structure's has_choch_bull/bear,
# which check BOTH tiers' CHoCH types the same way.
BULLISH_EVENTS = _BULLISH_SUPPORT | {CHOCH_BULL, SWING_CHOCH_BULL}
BEARISH_EVENTS = _BEARISH_SUPPORT | {CHOCH_BEAR, SWING_CHOCH_BEAR}

# See app.wyckoff.phase.RANGING_PHASES for why this exists: a "measured move"
# only makes sense as a range breakout, not once structure is already
# trending (Bullish/Bearish Structure).
RANGING_PHASES = {PHASE_RANGING}

_PHASE_TREND = {
    PHASE_BULLISH: TREND_BULLISH,
    PHASE_BEARISH: TREND_BEARISH,
    PHASE_RANGING: TREND_NEUTRAL,
}


def phase_trend(phase: str) -> str:
    return _PHASE_TREND.get(phase, TREND_NEUTRAL)


def classify_structure(
    df: pd.DataFrame,
    events: list[SMCEvent],
    daily_trend: str | None = None,
) -> tuple[str, float, list[str], str | None]:
    """Returns (phase, confidence, drivers, mtf_alignment)."""
    n = len(df)
    recent = [e for e in events if e.index >= n - RECENT_WINDOW]
    recent_types = [e.type for e in recent]

    # Collect the actual CHoCH type(s) present rather than assuming which
    # tier fired -- both the internal and swing/major tier can each have
    # their own CHoCH in the same recent window.
    choch_bull_drivers = [t for t in recent_types if t in (CHOCH_BULL, SWING_CHOCH_BULL)]
    choch_bear_drivers = [t for t in recent_types if t in (CHOCH_BEAR, SWING_CHOCH_BEAR)]
    has_choch_bull = bool(choch_bull_drivers)
    has_choch_bear = bool(choch_bear_drivers)

    filtered_types = recent_types
    if daily_trend == TREND_BEARISH:
        filtered_types = [t for t in recent_types if t not in BULLISH_EVENTS]
    elif daily_trend == TREND_BULLISH:
        filtered_types = [t for t in recent_types if t not in BEARISH_EVENTS]

    bull_support = [t for t in filtered_types if t in _BULLISH_SUPPORT]
    bear_support = [t for t in filtered_types if t in _BEARISH_SUPPORT]

    if has_choch_bear:
        drivers = choch_bear_drivers + bear_support
        phase, count = PHASE_BEARISH, len(choch_bear_drivers) + len(bear_support)
    elif has_choch_bull:
        drivers = choch_bull_drivers + bull_support
        phase, count = PHASE_BULLISH, len(choch_bull_drivers) + len(bull_support)
    elif bear_support and len(bear_support) >= len(bull_support):
        drivers = bear_support
        phase, count = PHASE_BEARISH, len(bear_support)
    elif bull_support:
        drivers = bull_support
        phase, count = PHASE_BULLISH, len(bull_support)
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
