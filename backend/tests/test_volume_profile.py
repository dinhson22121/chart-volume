"""Tests for app.wyckoff.volume_profile: POC/Value Area computation and
per-event confirmation against it."""

import types

import pandas as pd

from app.wyckoff.config import DEFAULT_CONFIG, WyckoffConfig
from app.wyckoff.events import SOS, SOW, SPRING, UPTHRUST, WyckoffEvent
from app.wyckoff.volume_profile import (
    VP_MIN_BARS,
    VolumeProfile,
    annotate_volume_confirmation,
    compute_volume_profile,
)

BASE = dict(open=100.0, high=101.0, low=99.0, close=100.0, volume=1000.0)
WIDE_LOW_VOL = dict(open=115.0, high=130.0, low=105.0, close=120.0, volume=10.0)


def _bars_df(bars):
    t0 = pd.Timestamp("2025-01-01")
    rows = [{"time": t0 + pd.Timedelta(days=i), **b} for i, b in enumerate(bars)]
    return pd.DataFrame(rows)


def _event(event_type, index, price, volume_confirmed=None):
    return WyckoffEvent(type=event_type, index=index, ts=pd.Timestamp("2025-01-01"), price=price, volume_confirmed=volume_confirmed)


# --- compute_volume_profile ---

def test_returns_none_when_fewer_bars_than_vp_min_bars():
    df = _bars_df([dict(BASE) for _ in range(VP_MIN_BARS - 1)])
    assert compute_volume_profile(df, DEFAULT_CONFIG) is None


def test_returns_none_when_window_has_no_price_range():
    flat = dict(open=100.0, high=100.0, low=100.0, close=100.0, volume=1000.0)
    df = _bars_df([dict(flat) for _ in range(DEFAULT_CONFIG.vp_lookback_bars)])
    assert compute_volume_profile(df, DEFAULT_CONFIG) is None


def test_poc_falls_in_the_high_volume_zone_not_a_low_volume_excursion():
    # 45 bars concentrated 99-101 at high volume, 5 bars extending up to 130
    # at low volume -- POC and the value area should stay anchored to the
    # heavy zone, not get dragged into the thin excursion.
    bars = [dict(BASE) for _ in range(45)] + [dict(WIDE_LOW_VOL) for _ in range(5)]
    df = _bars_df(bars)

    vp = compute_volume_profile(df, DEFAULT_CONFIG)

    assert vp is not None
    assert 99.0 <= vp.poc <= 101.0
    assert vp.value_area_high < 110.0  # doesn't stretch into the thin excursion
    assert vp.value_area_low <= vp.poc <= vp.value_area_high


def test_single_bar_with_zero_range_does_not_crash_and_stays_within_window():
    bars = [dict(BASE) for _ in range(DEFAULT_CONFIG.vp_lookback_bars - 1)]
    bars.append(dict(open=100.0, high=100.0, low=100.0, close=100.0, volume=5000.0))
    df = _bars_df(bars)

    vp = compute_volume_profile(df, DEFAULT_CONFIG)

    assert vp is not None
    assert 99.0 <= vp.value_area_low <= vp.poc <= vp.value_area_high <= 101.0


def test_uses_only_the_configured_lookback_window():
    # A wide, high-volume excursion far outside the lookback window must not
    # influence the profile at all.
    cfg = WyckoffConfig(vp_lookback_bars=25)
    old_wide_bars = [dict(open=500.0, high=600.0, low=400.0, close=550.0, volume=50000.0) for _ in range(25)]
    recent_flat_bars = [dict(BASE) for _ in range(25)]
    df = _bars_df(old_wide_bars + recent_flat_bars)

    vp = compute_volume_profile(df, cfg)

    assert vp is not None
    assert 99.0 <= vp.poc <= 101.0


# --- annotate_volume_confirmation ---

def test_vp_none_leaves_events_unchanged_with_volume_confirmed_none():
    events = [_event(SOS, 0, 105.0)]
    result = annotate_volume_confirmation(_bars_df([BASE]), events, None)
    assert result is events
    assert result[0].volume_confirmed is None


def test_sos_confirmed_when_close_breaks_above_value_area_high():
    vp = VolumeProfile(poc=100.0, value_area_high=103.0, value_area_low=97.0)
    df = _bars_df([dict(BASE, close=105.0)])
    events = [_event(SOS, 0, 105.0)]

    [annotated] = annotate_volume_confirmation(df, events, vp)

    assert annotated.volume_confirmed is True


def test_sos_not_confirmed_when_close_stays_inside_value_area():
    vp = VolumeProfile(poc=100.0, value_area_high=103.0, value_area_low=97.0)
    df = _bars_df([dict(BASE, close=101.0)])
    events = [_event(SOS, 0, 101.0)]

    [annotated] = annotate_volume_confirmation(df, events, vp)

    assert annotated.volume_confirmed is False


def test_sow_confirmed_when_close_breaks_below_value_area_low():
    vp = VolumeProfile(poc=100.0, value_area_high=103.0, value_area_low=97.0)
    df = _bars_df([dict(BASE, close=95.0)])
    events = [_event(SOW, 0, 95.0)]

    [annotated] = annotate_volume_confirmation(df, events, vp)

    assert annotated.volume_confirmed is True


def test_spring_confirmed_when_it_sweeps_below_value_area_and_reclaims():
    vp = VolumeProfile(poc=100.0, value_area_high=103.0, value_area_low=97.0)
    df = _bars_df([dict(BASE, low=95.0, close=98.0)])
    events = [_event(SPRING, 0, 98.0)]

    [annotated] = annotate_volume_confirmation(df, events, vp)

    assert annotated.volume_confirmed is True


def test_spring_not_confirmed_when_low_never_reaches_value_area_low():
    vp = VolumeProfile(poc=100.0, value_area_high=103.0, value_area_low=97.0)
    df = _bars_df([dict(BASE, low=98.0, close=99.0)])
    events = [_event(SPRING, 0, 99.0)]

    [annotated] = annotate_volume_confirmation(df, events, vp)

    assert annotated.volume_confirmed is False


def test_upthrust_confirmed_when_it_pokes_above_value_area_and_falls_back():
    vp = VolumeProfile(poc=100.0, value_area_high=103.0, value_area_low=97.0)
    df = _bars_df([dict(BASE, high=105.0, close=101.0)])
    events = [_event(UPTHRUST, 0, 101.0)]

    [annotated] = annotate_volume_confirmation(df, events, vp)

    assert annotated.volume_confirmed is True


def test_event_type_outside_vp_checkable_set_stays_unevaluated():
    from app.wyckoff.events import NO_DEMAND

    vp = VolumeProfile(poc=100.0, value_area_high=103.0, value_area_low=97.0)
    df = _bars_df([dict(BASE)])
    events = [_event(NO_DEMAND, 0, 100.0)]

    [annotated] = annotate_volume_confirmation(df, events, vp)

    assert annotated.volume_confirmed is None
