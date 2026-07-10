import pandas as pd

from app.sonicr.events import (
    DRAGON_CROSS_DOWN,
    DRAGON_CROSS_UP,
    SONIC_CROSS_DOWN,
    SONIC_CROSS_UP,
    SONIC_ENTRY_LONG,
    SonicEvent,
)
from app.sonicr.phase import (
    BEARISH_EVENTS,
    BULLISH_EVENTS,
    MTF_ALIGNED,
    PHASE_DOWNTREND,
    PHASE_RANGING,
    PHASE_UPTREND,
    classify_regime,
)

N_BARS = 30


def _df(n=N_BARS):
    t0 = pd.Timestamp("2025-01-01")
    return pd.DataFrame({"time": [t0 + pd.Timedelta(days=i) for i in range(n)]})


def _event(event_type, index):
    return SonicEvent(type=event_type, index=index, ts=pd.Timestamp("2025-01-01"), price=100.0)


def test_classify_regime_is_ranging_with_no_recent_events():
    phase, confidence, drivers, mtf = classify_regime(_df(), [])
    assert phase == PHASE_RANGING
    assert drivers == []
    assert mtf is None


def test_classify_regime_is_uptrend_when_bullish_events_dominate_recent_window():
    events = [_event(DRAGON_CROSS_UP, N_BARS - 3), _event(SONIC_CROSS_UP, N_BARS - 2)]
    phase, confidence, drivers, mtf = classify_regime(_df(), events)
    assert phase == PHASE_UPTREND
    assert set(drivers) <= BULLISH_EVENTS
    assert confidence > 0.4


def test_classify_regime_is_downtrend_when_bearish_events_dominate_recent_window():
    events = [_event(DRAGON_CROSS_DOWN, N_BARS - 3), _event(SONIC_CROSS_DOWN, N_BARS - 2)]
    phase, confidence, drivers, mtf = classify_regime(_df(), events)
    assert phase == PHASE_DOWNTREND
    assert set(drivers) <= BEARISH_EVENTS


def test_classify_regime_ignores_events_outside_recent_window():
    # RECENT_WINDOW=10, N_BARS=30 -> index 5 is well outside the trailing window.
    events = [_event(SONIC_ENTRY_LONG, 5)]
    phase, _, drivers, _ = classify_regime(_df(), events)
    assert phase == PHASE_RANGING
    assert drivers == []


def test_classify_regime_boosts_confidence_when_mtf_aligned():
    events = [_event(SONIC_CROSS_UP, N_BARS - 2)]
    _, confidence_plain, _, mtf_plain = classify_regime(_df(), events)
    phase, confidence_aligned, _, mtf_aligned = classify_regime(_df(), events, daily_trend="bullish")

    assert phase == PHASE_UPTREND
    assert mtf_plain is None
    assert mtf_aligned == MTF_ALIGNED
    assert confidence_aligned > confidence_plain


def test_classify_regime_penalizes_confidence_and_filters_when_mtf_conflicting():
    # A lone bullish SonicCrossUp against a bearish daily trend is filtered out
    # entirely (mirrors Wyckoff's suppression of counter-trend supporting
    # signals), so this collapses to Ranging rather than a low-confidence Uptrend.
    # (Sonic R has no unfiltered "always trusted" tier like Wyckoff's SOS/SOW,
    # so MTF_CONFLICTING can't be produced from raw cross signals alone --
    # SonicEntryLong/Short are already gated against daily_trend at detection
    # time, see test_sonicr_events.py.)
    events = [_event(SONIC_CROSS_UP, N_BARS - 2)]
    phase, confidence, drivers, mtf = classify_regime(_df(), events, daily_trend="bearish")
    assert phase == PHASE_RANGING
    assert drivers == []
    assert mtf is None
