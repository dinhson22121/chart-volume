"""Detector-layer tests for Sonic R events.

Feature columns (dragon/t3_fast/t3_slow/cci_fast/cci_slow) are injected
directly rather than computed via compute_features -- indicator arithmetic is
already covered by test_sonicr_indicators.py, so these tests isolate the
crossing / gating / pullback logic with fully controlled inputs.
"""

import pandas as pd
import pytest

from app.sonicr.config import SonicRConfig
from app.sonicr.events import (
    DRAGON_CROSS_DOWN,
    DRAGON_CROSS_UP,
    SONIC_CROSS_DOWN,
    SONIC_CROSS_UP,
    SONIC_ENTRY_LONG,
    SONIC_ENTRY_SHORT,
    detect_events,
)

CFG = SonicRConfig(pullback_lookback_bars=5)


def _row(close, dragon, t3_fast, t3_slow, cci_fast=0.0, cci_slow=0.0, high=None, low=None):
    return dict(
        open=close,
        high=high if high is not None else close + 0.5,
        low=low if low is not None else close - 0.5,
        close=close,
        volume=1000.0,
        dragon=dragon,
        t3_fast=t3_fast,
        t3_slow=t3_slow,
        cci_fast=cci_fast,
        cci_slow=cci_slow,
    )


def _to_df(rows):
    t0 = pd.Timestamp("2025-01-01")
    return pd.DataFrame([{"time": t0 + pd.Timedelta(days=i), **r} for i, r in enumerate(rows)])


def _by_type(events, event_type):
    return [e for e in events if e.type == event_type]


def test_dragon_cross_up_and_down_detected_on_close_crossing_dragon():
    rows = [
        _row(close=95, dragon=100, t3_fast=90, t3_slow=90),  # below
        _row(close=102, dragon=100, t3_fast=90, t3_slow=90),  # crosses above -> up
        _row(close=97, dragon=100, t3_fast=90, t3_slow=90),  # crosses back below -> down
    ]
    events = detect_events(_to_df(rows), CFG)

    up = _by_type(events, DRAGON_CROSS_UP)
    down = _by_type(events, DRAGON_CROSS_DOWN)
    assert [e.index for e in up] == [1]
    assert [e.index for e in down] == [2]


def test_dragon_cross_notes_translate_to_english():
    rows = [
        _row(close=95, dragon=100, t3_fast=90, t3_slow=90),
        _row(close=102, dragon=100, t3_fast=90, t3_slow=90),
    ]
    events = detect_events(_to_df(rows), CFG, language="en")

    up = _by_type(events, DRAGON_CROSS_UP)[0]
    assert "Dragon EMA" in up.note
    assert "bullish" in up.note.lower()
    assert "Giá cắt lên" not in up.note


def test_sonic_cross_up_and_down_detected_on_t3_fast_crossing_t3_slow():
    rows = [
        _row(close=100, dragon=100, t3_fast=90, t3_slow=95),  # fast below slow
        _row(close=100, dragon=100, t3_fast=96, t3_slow=95),  # fast crosses above -> up
        _row(close=100, dragon=100, t3_fast=94, t3_slow=95),  # fast crosses below -> down
    ]
    events = detect_events(_to_df(rows), CFG)

    up = _by_type(events, SONIC_CROSS_UP)
    down = _by_type(events, SONIC_CROSS_DOWN)
    assert [e.index for e in up] == [1]
    assert [e.index for e in down] == [2]


def test_sonic_entry_long_fires_at_pullback_bar_after_confirmed_cross():
    rows = [
        _row(close=95, dragon=100, t3_fast=90, t3_slow=95, cci_fast=-10, cci_slow=-10),  # baseline: below dragon
        _row(close=102, dragon=100, t3_fast=96, t3_slow=95, cci_fast=5, cci_slow=5),  # confirmed raw candidate
        _row(close=100.7, dragon=100, t3_fast=97, t3_slow=96, low=99.8),  # pullback: touches dragon, holds above
        _row(close=101, dragon=100, t3_fast=97, t3_slow=96),
    ]
    events = detect_events(_to_df(rows), CFG)

    entries = _by_type(events, SONIC_ENTRY_LONG)
    assert len(entries) == 1
    assert entries[0].index == 2
    assert entries[0].price == pytest.approx(100.7)


def test_sonic_entry_short_fires_at_pullback_bar_after_confirmed_cross():
    rows = [
        _row(close=105, dragon=100, t3_fast=110, t3_slow=105, cci_fast=10, cci_slow=10),  # baseline: above dragon
        _row(close=98, dragon=100, t3_fast=94, t3_slow=95, cci_fast=-5, cci_slow=-5),  # confirmed raw candidate
        _row(close=99.3, dragon=100, t3_fast=93, t3_slow=94, high=100.4),  # pullback: touches dragon, holds below
        _row(close=99, dragon=100, t3_fast=93, t3_slow=94),
    ]
    events = detect_events(_to_df(rows), CFG)

    entries = _by_type(events, SONIC_ENTRY_SHORT)
    assert len(entries) == 1
    assert entries[0].index == 2


def test_sonic_entry_not_emitted_when_cci_does_not_confirm():
    # Dragon + T3 cross both agree bullish, but CCI stays negative -> no raw candidate.
    rows = [
        _row(close=95, dragon=100, t3_fast=90, t3_slow=95, cci_fast=-10, cci_slow=-10),
        _row(close=102, dragon=100, t3_fast=96, t3_slow=95, cci_fast=-2, cci_slow=-2),
        _row(close=100.7, dragon=100, t3_fast=97, t3_slow=96, low=99.8),
    ]
    events = detect_events(_to_df(rows), CFG)

    assert _by_type(events, SONIC_ENTRY_LONG) == []


def test_sonic_entry_not_emitted_when_pullback_never_comes():
    rows = [_row(close=95, dragon=100, t3_fast=90, t3_slow=95, cci_fast=-10, cci_slow=-10)]
    rows.append(_row(close=102, dragon=100, t3_fast=96, t3_slow=95, cci_fast=5, cci_slow=5))
    # Price stays far above dragon for the whole lookback window -- no pullback bar.
    for _ in range(CFG.pullback_lookback_bars):
        rows.append(_row(close=110, dragon=100, t3_fast=98, t3_slow=96, low=108))

    events = detect_events(_to_df(rows), CFG)

    assert _by_type(events, SONIC_ENTRY_LONG) == []


def test_sonic_entry_long_blocked_by_conflicting_daily_trend():
    rows = [
        _row(close=95, dragon=100, t3_fast=90, t3_slow=95, cci_fast=-10, cci_slow=-10),
        _row(close=102, dragon=100, t3_fast=96, t3_slow=95, cci_fast=5, cci_slow=5),
        _row(close=100.7, dragon=100, t3_fast=97, t3_slow=96, low=99.8),
    ]
    events = detect_events(_to_df(rows), CFG, daily_trend="bearish")

    assert _by_type(events, SONIC_ENTRY_LONG) == []


def test_sonic_entry_long_allowed_when_daily_trend_aligned_or_absent():
    rows = [
        _row(close=95, dragon=100, t3_fast=90, t3_slow=95, cci_fast=-10, cci_slow=-10),
        _row(close=102, dragon=100, t3_fast=96, t3_slow=95, cci_fast=5, cci_slow=5),
        _row(close=100.7, dragon=100, t3_fast=97, t3_slow=96, low=99.8),
    ]
    assert len(_by_type(detect_events(_to_df(rows), CFG, daily_trend="bullish"), SONIC_ENTRY_LONG)) == 1
    assert len(_by_type(detect_events(_to_df(rows), CFG, daily_trend="neutral"), SONIC_ENTRY_LONG)) == 1
    assert len(_by_type(detect_events(_to_df(rows), CFG, daily_trend=None), SONIC_ENTRY_LONG)) == 1
