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

# A Liquidity Sweep confirmation gate (require a same-direction sweep within
# N bars before a structure/OB event) was tried and removed --
# scripts/backtest_liquidity_sweep_gate.py showed every tested lookback
# window (5/10/15/20 bars) underperformed taking every qualifying event
# ungated, on VN30 holdout. LiquiditySweep_Bull/Bear (app.smc.events) are
# still detected and recorded via signal_outcomes for reference; see git
# history for the removed LIQUIDITY_SWEEP_CONFIRMATION mapping.

# --- Entry-quality filters (SMC only) ------------------------------------
# Two rules from an SMC execution spec, read by
# app.services.trade_scenario._build_scenario_candidate via getattr on this
# strategy module -- deliberately NOT global constants there. They were
# measured against SMC's own baseline only; Wyckoff and Sonic R have quite
# different entry semantics (a Spring is by construction near the bottom of
# its range, so a discount gate means something else entirely for it), and
# nothing tested says these transfer. Those two strategies get the neutral
# defaults and behave exactly as they did before this existed.
#
# Validated on VN30 (scripts/backtest_smc_entry_filters.py, 14 variants over
# 2 rounds) before being enabled: all 13 filtered variants beat the
# unfiltered baseline, and the improvement was monotonic in filter strength
# on holdout MEDIAN r-multiple, not just mean (-0.05 unfiltered -> +1.21 at
# these settings) -- a median that moves like that can't be one lucky
# outlier, which is what separates this from the liquidity-sweep gate above.
# Measured here: n=65 mean_r=+3.82 median_r=+1.21 win_rate=73.8%
# bootstrap_ci=[0.328, 0.651] walk_forward=1.00, vs baseline n=201
# mean_r=+1.20 median_r=-0.05 win_rate=47.8% ci=[0.158, 0.321]. The tradeoff
# is real: roughly a third as many setups survive. Not yet re-confirmed on
# the full HOSE/HNX universe.
#
# Deliberately NOT the best-scoring variant (a 0.33 threshold scored a
# little higher). Both numbers are doctrine, not fitted: 0.5 is the textbook
# discount/premium split and 3.0 is the spec's own stated minimum, so
# neither was tuned against the data judging it -- only the SCOPE below was,
# and 0.33-vs-0.5 sat well inside the noise at n<70.

# Classic "don't buy in premium, don't sell in discount": the entry must sit
# in the favorable fraction of the current [support, resistance] range.
POI_ZONE_THRESHOLD_PCT = 0.5
# Which event types the filter applies to; empty = every qualifying type.
# The spec names Order Blocks and Fair Value Gaps specifically, but applying
# it to every entry type measured strictly better (holdout mean_r +3.82 vs
# +2.72, median +1.21 vs +0.43) -- "don't buy at the top of the range" holds
# for breakout entries too, not just pullbacks into a zone.
POI_ZONE_FILTER_TYPES: frozenset[str] = frozenset()
# Reject a setup whose measured move isn't at least this many times its own
# stop distance.
MIN_RR_RATIO = 3.0

# The spec's third rule -- "place the stop at the refined Order Block" --
# needs no flag: an Order Block event's index IS its anchor candle (see
# app.smc.events._detect_order_blocks_tier, which builds
# SMCEvent(bullish_ob, ob_idx, ..., zone_low=df.low[ob_idx],
# zone_high=df.high[ob_idx])), and trade_scenario takes the stop from
# candles[event.index].low/high -- the same bar, so the same prices. A flag
# for it was implemented and backtested anyway: byte-identical to baseline
# on every VN30 metric, then dropped as redundant.

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
