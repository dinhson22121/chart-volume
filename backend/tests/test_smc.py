"""analyze()-level tests for the SMC (Smart Money Concept) engine: phase
classification and multi-timeframe alignment."""

import types
from datetime import datetime

import pandas as pd

from app.smc import analyze
from app.smc.config import SMCConfig
from app.smc.events import BOS_BULL, CHOCH_BULL, SMCEvent, detect_events
from app.smc.indicators import compute_features
from app.smc.phase import (
    MTF_ALIGNED,
    PHASE_BEARISH,
    PHASE_BULLISH,
    PHASE_RANGING,
    TREND_BEARISH,
    TREND_BULLISH,
    classify_structure,
)

CFG = SMCConfig(swing_lookback=2, ob_lookback_bars=10, fvg_min_gap_mult=0.3)

# Establishes a swing low/high/higher-low then breaks the swing high --
# CHoCH_Bull, same scenario proven at the detector level in test_smc_events.py.
BULLISH_VALUES = [
    110, 108, 106, 104, 102, 100,
    102, 104, 106, 108, 110, 112,
    110, 108, 106, 104, 103,
    105, 108, 111, 113,
]

BEARISH_VALUES = [
    90, 92, 94, 96, 98, 100,
    98, 96, 94, 92, 90, 88,
    90, 92, 94, 96, 97,
    95, 92, 89, 87,
]

FLAT_VALUES = [100.0] * 20


def _to_df(values):
    n = len(values)
    return pd.DataFrame(
        {
            "time": pd.date_range("2025-01-01", periods=n, freq="D"),
            "open": [v - 0.3 for v in values],
            "high": [v + 0.5 for v in values],
            "low": [v - 0.5 for v in values],
            "close": values,
            "volume": [1000.0] * n,
        }
    )


def _to_candles(values):
    t0 = pd.Timestamp("2025-01-01")
    return [
        types.SimpleNamespace(
            bucket_start=(t0 + pd.Timedelta(days=i)).to_pydatetime(),
            open=v - 0.3,
            high=v + 0.5,
            low=v - 0.5,
            close=v,
            volume=1000.0,
        )
        for i, v in enumerate(values)
    ]


def test_insufficient_data_below_min_bars():
    result = analyze(_to_candles(FLAT_VALUES[:10]), CFG)
    assert result.phase == "Insufficient data"
    assert result.confidence == 0.0


def test_phase_bullish_structure_from_choch_bull():
    result = analyze(_to_candles(BULLISH_VALUES), CFG)
    assert result.phase == PHASE_BULLISH
    assert result.confidence > 0.4


def test_phase_bearish_structure_from_choch_bear():
    result = analyze(_to_candles(BEARISH_VALUES), CFG)
    assert result.phase == PHASE_BEARISH


def test_phase_ranging_on_a_flat_series():
    result = analyze(_to_candles(FLAT_VALUES), CFG)
    assert result.phase == PHASE_RANGING


def test_levels_reflect_recent_swing_range():
    result = analyze(_to_candles(BULLISH_VALUES), CFG)
    assert result.levels.support <= 103.0
    assert result.levels.resistance >= 112.0


def test_bearish_daily_trend_suppresses_a_lone_bos_bull():
    # BOS (unlike CHoCH) is a "support" tier signal -- suppressible by a
    # conflicting daily trend, same as Wyckoff's Spring/SC/NoSupply. An
    # isolated single-event list keeps the case unambiguous.
    df = _to_df(FLAT_VALUES)
    events = [SMCEvent(BOS_BULL, 15, None, 100.0, "")]

    without_context, *_ = classify_structure(df, events)
    with_bearish_context, _, _, alignment = classify_structure(df, events, daily_trend=TREND_BEARISH)

    assert without_context == PHASE_BULLISH
    assert with_bearish_context == PHASE_RANGING  # the only driver was suppressed
    assert alignment is None


def test_choch_is_never_suppressed_by_daily_trend_but_confidence_is_penalized():
    # Mirrors app.wyckoff.phase's SOS/SOW precedent: a real reversal signal
    # shouldn't be silently discarded just because it conflicts with the
    # higher-timeframe trend -- only its confidence takes a hit.
    df = _to_df(BULLISH_VALUES)
    feat = compute_features(df, CFG)
    events = detect_events(feat, CFG)

    baseline_phase, baseline_conf, _, _ = classify_structure(feat, events)
    phase, penalized_conf, _, alignment = classify_structure(feat, events, daily_trend=TREND_BEARISH)

    assert baseline_phase == PHASE_BULLISH
    assert phase == PHASE_BULLISH  # CHoCH still wins
    assert alignment == "conflicting"
    assert penalized_conf < baseline_conf


def test_aligned_daily_trend_boosts_confidence():
    # A single-driver event list (bypassing full detection, whose synthetic
    # zigzag test data incidentally produces several extra FVGs) isolates the
    # classifier so the baseline confidence isn't already at the 0.9 cap.
    df = _to_df(FLAT_VALUES)
    events = [SMCEvent(CHOCH_BULL, 15, datetime(2025, 1, 16), 100.0, "")]

    _, base_conf, _, _ = classify_structure(df, events)
    phase, boosted_conf, _, alignment = classify_structure(df, events, daily_trend=TREND_BULLISH)

    assert phase == PHASE_BULLISH
    assert alignment == MTF_ALIGNED
    assert boosted_conf > base_conf


def test_analyze_translates_event_notes_to_english():
    result = analyze(_to_candles(BULLISH_VALUES), CFG, language="en")
    choch_event = next(e for e in result.events if e.type == "CHoCH_Bull")
    assert "đổi chiều" not in choch_event.note
    assert "reversal" in choch_event.note
