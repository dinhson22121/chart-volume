"""Deterministic tests for the Wyckoff engine.

Each detector test builds a flat 25-bar base (establishing support=99,
resistance=101, vol_ma≈1000, narrow spread) then appends a single crafted event
bar. Thresholds account for the trailing rolling averages including the event
bar itself.
"""

import types

import pandas as pd

from app.wyckoff import analyze
from app.wyckoff.events import (
    BUYING_CLIMAX,
    NO_DEMAND,
    NO_SUPPLY,
    SELLING_CLIMAX,
    SOS,
    SOW,
    SPRING,
    UPTHRUST,
    detect_events,
)
from app.wyckoff.indicators import compute_features
from app.wyckoff.phase import (
    PHASE_ACCUMULATION,
    PHASE_DISTRIBUTION,
    PHASE_MARKDOWN,
    PHASE_MARKUP,
    PHASE_RANGING,
)

BASE = dict(open=100.0, high=101.0, low=99.0, close=100.0, volume=1000.0)

SPRING_BAR = dict(open=98.0, high=99.8, low=97.0, close=99.3, volume=1500.0)
SC_BAR = dict(open=99.0, high=100.0, low=95.0, close=97.5, volume=2600.0)
UPTHRUST_BAR = dict(open=101.0, high=103.0, low=100.2, close=100.5, volume=1500.0)
BC_BAR = dict(open=101.0, high=106.0, low=101.0, close=102.5, volume=2600.0)
SOS_BAR = dict(open=101.2, high=103.0, low=101.0, close=102.8, volume=1800.0)
SOW_BAR = dict(open=98.9, high=99.0, low=97.0, close=97.3, volume=1800.0)
NODEMAND_BAR = dict(open=100.2, high=100.6, low=100.1, close=100.5, volume=500.0)
NOSUPPLY_BAR = dict(open=99.8, high=99.9, low=99.4, close=99.5, volume=500.0)


def base_bars(n=25):
    return [dict(BASE) for _ in range(n)]


def _to_df(bars):
    t0 = pd.Timestamp("2025-01-01")
    rows = [{"time": t0 + pd.Timedelta(days=i), **b} for i, b in enumerate(bars)]
    return pd.DataFrame(rows)


def _to_candles(bars):
    t0 = pd.Timestamp("2025-01-01")
    return [
        types.SimpleNamespace(bucket_start=(t0 + pd.Timedelta(days=i)).to_pydatetime(), **b)
        for i, b in enumerate(bars)
    ]


def last_event(bars):
    events = detect_events(compute_features(_to_df(bars)))
    return events[-1] if events else None


# --- Detector tests: one crafted event bar appended to a flat base ---

def test_detects_spring():
    ev = last_event(base_bars() + [SPRING_BAR])
    assert ev is not None and ev.type == SPRING
    assert ev.index == 25  # the appended bar


def test_detects_selling_climax():
    ev = last_event(base_bars() + [SC_BAR])
    assert ev is not None and ev.type == SELLING_CLIMAX


def test_detects_upthrust():
    ev = last_event(base_bars() + [UPTHRUST_BAR])
    assert ev is not None and ev.type == UPTHRUST


def test_detects_buying_climax():
    ev = last_event(base_bars() + [BC_BAR])
    assert ev is not None and ev.type == BUYING_CLIMAX


def test_detects_sos():
    ev = last_event(base_bars() + [SOS_BAR])
    assert ev is not None and ev.type == SOS


def test_detects_sow():
    ev = last_event(base_bars() + [SOW_BAR])
    assert ev is not None and ev.type == SOW


def test_detects_no_demand():
    ev = last_event(base_bars() + [NODEMAND_BAR])
    assert ev is not None and ev.type == NO_DEMAND


def test_detects_no_supply():
    ev = last_event(base_bars() + [NOSUPPLY_BAR])
    assert ev is not None and ev.type == NO_SUPPLY


def test_flat_base_emits_no_events():
    assert last_event(base_bars(25)) is None


# --- Phase classification via the analyze() entrypoint ---

def test_phase_accumulation_from_spring():
    result = analyze(_to_candles(base_bars() + [SPRING_BAR]))
    assert result.phase == PHASE_ACCUMULATION
    assert result.confidence > 0.4


def test_phase_markup_from_sos():
    result = analyze(_to_candles(base_bars() + [SOS_BAR]))
    assert result.phase == PHASE_MARKUP


def test_phase_distribution_from_upthrust():
    result = analyze(_to_candles(base_bars() + [UPTHRUST_BAR]))
    assert result.phase == PHASE_DISTRIBUTION


def test_phase_markdown_from_sow():
    result = analyze(_to_candles(base_bars() + [SOW_BAR]))
    assert result.phase == PHASE_MARKDOWN


def test_phase_ranging_when_quiet():
    result = analyze(_to_candles(base_bars(20)))
    assert result.phase == PHASE_RANGING


def test_insufficient_data():
    result = analyze(_to_candles(base_bars(10)))
    assert result.phase == "Insufficient data"
    assert result.confidence == 0.0


def test_levels_reflect_range():
    result = analyze(_to_candles(base_bars() + [SPRING_BAR]))
    # Spring bar pierced to 97; support should capture the recent low.
    assert result.levels.support <= 99.0
    assert result.levels.resistance >= 101.0
