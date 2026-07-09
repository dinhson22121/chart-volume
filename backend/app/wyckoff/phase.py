"""Phase classification from recent events + price position.

Simplified Wyckoff schematic: recent bullish absorption signals near a base ->
Accumulation; a confirmed strength breakout -> Markup; topping/weakness signals
near a ceiling -> Distribution; a confirmed breakdown -> Markdown. Anything else
-> Ranging. Output is a heuristic guess with a confidence score, meant as
structured input for the LLM narrative.
"""

from __future__ import annotations

import pandas as pd

from app.wyckoff.events import (
    BUYING_CLIMAX,
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

PHASE_ACCUMULATION = "Accumulation"
PHASE_MARKUP = "Markup"
PHASE_DISTRIBUTION = "Distribution"
PHASE_MARKDOWN = "Markdown"
PHASE_RANGING = "Ranging"

_ACCUMULATION_SIGNALS = {SPRING, SELLING_CLIMAX, NO_SUPPLY}
_DISTRIBUTION_SIGNALS = {UPTHRUST, BUYING_CLIMAX, NO_DEMAND}


def classify_phase(
    df: pd.DataFrame, events: list[WyckoffEvent]
) -> tuple[str, float, list[str]]:
    n = len(df)
    recent = [e for e in events if e.index >= n - RECENT_WINDOW]
    recent_types = [e.type for e in recent]

    has_sos = SOS in recent_types
    has_sow = SOW in recent_types
    acc_hits = [t for t in recent_types if t in _ACCUMULATION_SIGNALS]
    dist_hits = [t for t in recent_types if t in _DISTRIBUTION_SIGNALS]

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
        return PHASE_RANGING, _BASE_CONFIDENCE, []

    confidence = min(_MAX_CONFIDENCE, _BASE_CONFIDENCE + _PER_SIGNAL * count)
    return phase, round(confidence, 2), drivers
