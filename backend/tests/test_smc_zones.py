"""Premium/Discount/Equilibrium zone boundaries + Strong/Weak High/Low
labels -- display-only reference context, never a gate on entries."""

from app.smc.phase import PHASE_BEARISH, PHASE_BULLISH, PHASE_RANGING
from app.smc.zones import compute_zones


def test_premium_zone_sits_at_the_top_of_the_range():
    zones = compute_zones(support=100.0, resistance=200.0, phase=PHASE_RANGING)
    # top 5% of a 100-wide range: [195, 200]
    assert zones["premium_low"] == 195.0


def test_discount_zone_sits_at_the_bottom_of_the_range():
    zones = compute_zones(support=100.0, resistance=200.0, phase=PHASE_RANGING)
    # bottom 5% of a 100-wide range: [100, 105]
    assert zones["discount_high"] == 105.0


def test_equilibrium_zone_straddles_the_midpoint():
    zones = compute_zones(support=100.0, resistance=200.0, phase=PHASE_RANGING)
    midpoint = 150.0
    assert zones["equilibrium_low"] < midpoint < zones["equilibrium_high"]
    assert zones["discount_high"] < zones["equilibrium_low"]
    assert zones["equilibrium_high"] < zones["premium_low"]


def test_bullish_structure_labels_recent_low_strong_and_recent_high_weak():
    # A bullish trend means the recent low held (strong support) while the
    # recent high is the one likely to break next (weak resistance).
    zones = compute_zones(support=100.0, resistance=200.0, phase=PHASE_BULLISH)
    assert zones["low_label"] == "Strong Low"
    assert zones["high_label"] == "Weak High"


def test_bearish_structure_labels_recent_high_strong_and_recent_low_weak():
    zones = compute_zones(support=100.0, resistance=200.0, phase=PHASE_BEARISH)
    assert zones["high_label"] == "Strong High"
    assert zones["low_label"] == "Weak Low"


def test_ranging_phase_has_no_strong_extreme():
    # No established trend to call either extreme "strong" -- both sides are
    # equally exposed to breaking.
    zones = compute_zones(support=100.0, resistance=200.0, phase=PHASE_RANGING)
    assert zones["high_label"] == "Weak High"
    assert zones["low_label"] == "Weak Low"


def test_zero_width_range_does_not_raise():
    zones = compute_zones(support=100.0, resistance=100.0, phase=PHASE_RANGING)
    assert zones["premium_low"] == 100.0
    assert zones["discount_high"] == 100.0
