"""Deterministic tests for the Wyckoff engine.

Each detector test builds a flat 25-bar base (establishing support=99,
resistance=101, vol_ma≈1000, narrow spread) then appends a single crafted event
bar. Thresholds account for the trailing rolling averages including the event
bar itself.
"""

import types

import pandas as pd

from app.wyckoff import analyze
from app.wyckoff.config import DEFAULT_CONFIG
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
    detect_events,
    trace_bar,
)
from app.wyckoff.indicators import compute_features
from app.wyckoff.phase import (
    BEARISH_EVENTS,
    BULLISH_EVENTS,
    MTF_ALIGNED,
    MTF_CONFLICTING,
    PHASE_ACCUMULATION,
    PHASE_DISTRIBUTION,
    PHASE_MARKDOWN,
    PHASE_MARKUP,
    PHASE_RANGING,
    TREND_BEARISH,
    TREND_BULLISH,
    classify_phase,
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

# Quiet pullback bars re-testing the level just broken by SOS_BAR/SOW_BAR (~101/~99).
LPS_PULLBACK_BAR = dict(open=102.0, high=102.3, low=101.5, close=101.8, volume=700.0)
LPSY_PULLBACK_BAR = dict(open=98.0, high=98.5, low=97.7, close=98.2, volume=700.0)


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


# --- Decision tracing: full per-detector explanation of one bar ---

def test_trace_bar_flags_matched_detector():
    feat = compute_features(_to_df(base_bars() + [SPRING_BAR]))
    traces = trace_bar(feat.iloc[-1], DEFAULT_CONFIG)
    spring = next(t for t in traces if t.type == SPRING)
    assert spring.matched is True
    assert all(c.passed for c in spring.checks)


def test_trace_bar_explains_non_match():
    feat = compute_features(_to_df(base_bars() + [SPRING_BAR]))
    traces = trace_bar(feat.iloc[-1], DEFAULT_CONFIG)
    upthrust = next(t for t in traces if t.type == UPTHRUST)
    assert upthrust.matched is False
    assert any(not c.passed for c in upthrust.checks)


def test_trace_bar_covers_all_eight_detector_types():
    feat = compute_features(_to_df(base_bars() + [SPRING_BAR]))
    traces = trace_bar(feat.iloc[-1], DEFAULT_CONFIG)
    assert {t.type for t in traces} == {
        SELLING_CLIMAX, BUYING_CLIMAX, SPRING, UPTHRUST, SOS, SOW, NO_DEMAND, NO_SUPPLY,
    }


def test_trace_bar_reports_insufficient_data_for_early_bars():
    feat = compute_features(_to_df(base_bars(5)))
    traces = trace_bar(feat.iloc[0], DEFAULT_CONFIG)
    assert all(t.matched is False for t in traces)
    assert all(not t.checks[0].passed for t in traces)


# --- Multi-timeframe alignment: daily trend informs half-session phase ---

def test_bearish_daily_trend_suppresses_bullish_driven_phase():
    df = _to_df(base_bars() + [SPRING_BAR])
    feat = compute_features(df)
    events = detect_events(feat)

    without_context, *_ = classify_phase(feat, events)
    with_bearish_context, _, _, alignment = classify_phase(feat, events, daily_trend=TREND_BEARISH)

    assert without_context == PHASE_ACCUMULATION  # baseline: Spring alone drives Accumulation
    assert with_bearish_context == PHASE_RANGING  # suppressed: no bullish driver left
    assert alignment is None  # Ranging has no trend to compare


def test_aligned_daily_trend_boosts_confidence():
    df = _to_df(base_bars() + [SOS_BAR])
    feat = compute_features(df)
    events = detect_events(feat)

    _, base_conf, _, _ = classify_phase(feat, events)
    phase, boosted_conf, _, alignment = classify_phase(feat, events, daily_trend=TREND_BULLISH)

    assert phase == PHASE_MARKUP
    assert alignment == MTF_ALIGNED
    assert boosted_conf > base_conf


def test_conflicting_daily_trend_penalizes_confidence():
    df = _to_df(base_bars() + [SOS_BAR])
    feat = compute_features(df)
    events = detect_events(feat)

    _, base_conf, _, _ = classify_phase(feat, events)
    phase, penalized_conf, _, alignment = classify_phase(feat, events, daily_trend=TREND_BEARISH)

    assert phase == PHASE_MARKUP
    assert alignment == MTF_CONFLICTING
    assert penalized_conf < base_conf


# --- LPS/LPSY: the entry-confirmation pullback after a confirmed SOS/SOW breakout ---

def test_detects_lps_after_sos():
    events = detect_events(compute_features(_to_df(base_bars() + [SOS_BAR, LPS_PULLBACK_BAR])))
    lps = [e for e in events if e.type == LPS]
    assert len(lps) == 1
    assert lps[0].index == 26  # the pullback bar, right after SOS at index 25


def test_detects_lpsy_after_sow():
    events = detect_events(compute_features(_to_df(base_bars() + [SOW_BAR, LPSY_PULLBACK_BAR])))
    lpsy = [e for e in events if e.type == LPSY]
    assert len(lpsy) == 1
    assert lpsy[0].index == 26


def test_no_lps_when_price_never_pulls_back():
    rally_bars = [
        dict(open=103.0 + i, high=104.0 + i, low=102.5 + i, close=103.5 + i, volume=1000.0)
        for i in range(10)
    ]
    events = detect_events(compute_features(_to_df(base_bars() + [SOS_BAR] + rally_bars)))
    assert not any(e.type == LPS for e in events)


def test_no_lps_when_pullback_violates_the_broken_level():
    # Pulls back but closes well below the broken resistance -> failed retest, not an LPS.
    failed_bar = dict(open=100.0, high=100.5, low=98.0, close=98.5, volume=700.0)
    events = detect_events(compute_features(_to_df(base_bars() + [SOS_BAR, failed_bar])))
    assert not any(e.type == LPS for e in events)


def test_no_lps_when_pullback_volume_is_not_quiet():
    loud_bar = dict(open=102.0, high=102.3, low=101.5, close=101.8, volume=1500.0)
    events = detect_events(compute_features(_to_df(base_bars() + [SOS_BAR, loud_bar])))
    assert not any(e.type == LPS for e in events)


def test_lps_and_lpsy_have_bullish_bearish_polarity():
    assert LPS in BULLISH_EVENTS
    assert LPSY in BEARISH_EVENTS


def test_lps_lookback_bars_is_configurable():
    from dataclasses import replace

    filler_bars = [dict(BASE) for _ in range(3)]  # push the pullback to 4 bars after SOS
    feat = compute_features(_to_df(base_bars() + [SOS_BAR] + filler_bars + [LPS_PULLBACK_BAR]))

    found_with_default = detect_events(feat, DEFAULT_CONFIG)  # default lookback=10 reaches it
    assert any(e.type == LPS for e in found_with_default)

    short_lookback_cfg = replace(DEFAULT_CONFIG, lps_lookback_bars=2)
    found_with_short_lookback = detect_events(feat, short_lookback_cfg)  # window ends before it
    assert not any(e.type == LPS for e in found_with_short_lookback)


def test_analyze_passes_through_daily_trend_and_alignment():
    result = analyze(_to_candles(base_bars() + [SOS_BAR]), daily_trend=TREND_BULLISH)
    assert result.daily_trend == TREND_BULLISH
    assert result.mtf_alignment == MTF_ALIGNED


def test_analyze_without_daily_trend_has_no_alignment():
    result = analyze(_to_candles(base_bars() + [SOS_BAR]))
    assert result.daily_trend is None
    assert result.mtf_alignment is None
