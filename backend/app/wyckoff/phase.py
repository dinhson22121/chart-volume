"""Phase classification from recent events + price position.

Simplified Wyckoff schematic: recent bullish absorption signals near a base ->
Accumulation; a confirmed strength breakout -> Markup; topping/weakness signals
near a ceiling -> Distribution; a confirmed breakdown -> Markdown. Anything else
-> Ranging. Output is a heuristic guess with a confidence score, meant as
structured input for the LLM narrative.

Optional multi-timeframe context (``daily_trend``): a higher-timeframe trend
passed in by the caller (see app.services.analysis) that (1) excludes signals
pointing the "wrong way" from driving the phase call, and (2) nudges confidence
up when aligned / down when conflicting.
"""

from __future__ import annotations

import pandas as pd

from app.wyckoff.events import (
    BUYING_CLIMAX,
    LPS,
    LPSY,
    NO_DEMAND,
    NO_SUPPLY,
    SELLING_CLIMAX,
    SOS,
    SOW,
    SPRING,
    UPTHRUST,
    WyckoffEvent,
)

RECENT_WINDOW = 10
_BASE_CONFIDENCE = 0.4
_PER_SIGNAL = 0.15
_MAX_CONFIDENCE = 0.9
_MIN_CONFIDENCE = 0.1
_MTF_BONUS = 0.1
_MTF_PENALTY = 0.15
_VP_BONUS = 0.1
_VP_PENALTY = 0.15

VP_CONFIRMED = "confirmed"
VP_UNCONFIRMED = "unconfirmed"

PHASE_ACCUMULATION = "Accumulation"
PHASE_MARKUP = "Markup"
PHASE_DISTRIBUTION = "Distribution"
PHASE_MARKDOWN = "Markdown"
PHASE_RANGING = "Ranging"

TREND_BULLISH = "bullish"
TREND_BEARISH = "bearish"
TREND_NEUTRAL = "neutral"

MTF_ALIGNED = "aligned"
MTF_CONFLICTING = "conflicting"

_ACCUMULATION_SIGNALS = {SPRING, SELLING_CLIMAX, NO_SUPPLY}
_DISTRIBUTION_SIGNALS = {UPTHRUST, BUYING_CLIMAX, NO_DEMAND}
# "Bullish-oriented" events drive Accumulation/Markup/SOS (and, in
# signal_outcomes.py, count as a "win" on a positive forward return);
# "bearish-oriented" drive Distribution/Markdown/SOW (win on negative return).
# Used here to suppress counter-trend drivers when a daily_trend is supplied.
# LPS/LPSY (entry-confirmation pullbacks) never drive phase on their own -- they
# just inherit the bullish/bearish polarity for signal_outcomes win/loss scoring.
BULLISH_EVENTS = _ACCUMULATION_SIGNALS | {SOS, LPS}
BEARISH_EVENTS = _DISTRIBUTION_SIGNALS | {SOW, LPSY}

# Event types app.wyckoff.volume_profile.annotate_volume_confirmation actually
# scores (see that module for why the rest are left unconfirmed in v1).
_VP_CHECKABLE = {SOS, SOW, SPRING, UPTHRUST}

# Phases where a "measured move" (trading-range height) is a coherent concept
# -- a genuine range being built or broken out of. Markup/Markdown are already
# trending, so there's no range left to measure a breakout against. Used by
# app.services.trade_scenario to gate scenario creation on the phase as of
# just before the triggering event (not the phase after it, which the event
# itself often just flipped to Markup/Markdown).
RANGING_PHASES = {PHASE_ACCUMULATION, PHASE_DISTRIBUTION, PHASE_RANGING}

_PHASE_TREND = {
    PHASE_ACCUMULATION: TREND_BULLISH,
    PHASE_MARKUP: TREND_BULLISH,
    PHASE_DISTRIBUTION: TREND_BEARISH,
    PHASE_MARKDOWN: TREND_BEARISH,
    PHASE_RANGING: TREND_NEUTRAL,
}


def phase_trend(phase: str) -> str:
    return _PHASE_TREND.get(phase, TREND_NEUTRAL)


def classify_phase(
    df: pd.DataFrame,
    events: list[WyckoffEvent],
    daily_trend: str | None = None,
) -> tuple[str, float, list[str], str | None, str | None]:
    """Returns (phase, confidence, drivers, mtf_alignment, vp_alignment).

    ``mtf_alignment`` is None when no daily_trend context was supplied or the
    resulting phase is Ranging/Insufficient (nothing directional to compare).
    ``vp_alignment`` is None when none of the driving events are a Volume
    Profile-checkable type (see app.wyckoff.volume_profile) -- there's nothing
    to confirm or penalize against.
    """
    n = len(df)
    recent = [e for e in events if e.index >= n - RECENT_WINDOW]
    recent_types = [e.type for e in recent]

    # SOS/SOW are confirmed-breakout signals and always drive Markup/Markdown
    # regardless of daily context (a real breakout shouldn't be silently
    # discarded) -- the confidence step below penalizes it if it conflicts.
    # The weaker "supporting" signals (Spring/SC/NoSupply, Upthrust/BC/NoDemand)
    # only drive the fallback Accumulation/Distribution branches, and those ARE
    # suppressed when they point against the daily trend so a lone Spring
    # doesn't call Accumulation against a strongly bearish daily backdrop.
    has_sos = SOS in recent_types
    has_sow = SOW in recent_types

    filtered_types = recent_types
    if daily_trend == TREND_BEARISH:
        filtered_types = [t for t in recent_types if t not in BULLISH_EVENTS]
    elif daily_trend == TREND_BULLISH:
        filtered_types = [t for t in recent_types if t not in BEARISH_EVENTS]

    acc_hits = [t for t in filtered_types if t in _ACCUMULATION_SIGNALS]
    dist_hits = [t for t in filtered_types if t in _DISTRIBUTION_SIGNALS]

    if has_sow:
        drivers = ["SOW"] + acc_hits
        phase, count = PHASE_MARKDOWN, 1 + len(dist_hits)
    elif has_sos:
        drivers = ["SOS"] + [t for t in recent_types if t == SPRING]
        phase, count = PHASE_MARKUP, 1 + sum(1 for t in recent_types if t == SPRING)
    elif dist_hits and len(dist_hits) >= len(acc_hits):
        drivers = dist_hits
        phase, count = PHASE_DISTRIBUTION, len(dist_hits)
    elif acc_hits:
        drivers = acc_hits
        phase, count = PHASE_ACCUMULATION, len(acc_hits)
    else:
        return PHASE_RANGING, _BASE_CONFIDENCE, [], None, None

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

    # Volume Profile confirmation, same bonus/penalty shape as MTF above: only
    # applies when at least one of the events that actually drove this phase
    # call is a VP-checkable type (see app.wyckoff.volume_profile) -- with none
    # present there's nothing to confirm or penalize.
    driver_types = set(drivers)
    vp_checkable_drivers = [
        e for e in recent
        if e.type in driver_types and e.type in _VP_CHECKABLE and e.volume_confirmed is not None
    ]
    vp_alignment: str | None = None
    if vp_checkable_drivers:
        if any(e.volume_confirmed for e in vp_checkable_drivers):
            confidence += _VP_BONUS
            vp_alignment = VP_CONFIRMED
        else:
            confidence -= _VP_PENALTY
            vp_alignment = VP_UNCONFIRMED

    confidence = max(_MIN_CONFIDENCE, min(_MAX_CONFIDENCE, confidence))
    return phase, round(confidence, 2), drivers, mtf_alignment, vp_alignment
