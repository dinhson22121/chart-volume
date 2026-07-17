import types

import pandas as pd

from app.sonicr import BEARISH_EVENTS, BULLISH_EVENTS, analyze
from app.sonicr.config import SonicRConfig
from app.sonicr.phase import PHASE_DOWNTREND, PHASE_RANGING, PHASE_UPTREND

BASE = dict(open=100.0, high=101.0, low=99.0, close=100.0, volume=1000.0)


def _to_candles(bars):
    t0 = pd.Timestamp("2025-01-01")
    return [
        types.SimpleNamespace(bucket_start=(t0 + pd.Timedelta(days=i)).to_pydatetime(), **b)
        for i, b in enumerate(bars)
    ]


def test_analyze_returns_insufficient_data_below_min_bars():
    candles = _to_candles([dict(BASE) for _ in range(5)])
    result = analyze(candles)
    assert result.phase == "Insufficient data"
    assert result.confidence == 0.0
    assert result.events == []


def test_analyze_returns_valid_result_on_a_steady_uptrend():
    bars = [dict(open=100 + i, high=101 + i, low=99 + i, close=100 + i, volume=1000.0) for i in range(60)]
    candles = _to_candles(bars)

    result = analyze(candles, config=SonicRConfig())

    assert result.phase in {PHASE_UPTREND, PHASE_DOWNTREND, PHASE_RANGING}
    assert result.levels.support > 0
    assert result.levels.resistance > result.levels.support
    assert result.as_of is not None
    assert isinstance(result.events, list)


def test_analyze_events_are_compatible_with_events_as_dicts():
    bars = [dict(open=100 + i, high=101 + i, low=99 + i, close=100 + i, volume=1000.0) for i in range(60)]
    candles = _to_candles(bars)

    result = analyze(candles)
    dicts = result.events_as_dicts()

    assert len(dicts) == len(result.events)
    for d in dicts:
        # volume_confirmed is Wyckoff-only (see app.wyckoff.volume_profile) --
        # always None here since SonicEvent has no such attribute.
        assert set(d.keys()) == {"type", "ts", "price", "note", "volume_confirmed"}
        assert d["volume_confirmed"] is None


def test_bullish_and_bearish_event_sets_are_disjoint_and_reference_real_types():
    from app.sonicr.events import (
        DRAGON_CROSS_DOWN,
        DRAGON_CROSS_UP,
        SONIC_CROSS_DOWN,
        SONIC_CROSS_UP,
        SONIC_ENTRY_LONG,
        SONIC_ENTRY_SHORT,
    )

    assert BULLISH_EVENTS == {DRAGON_CROSS_UP, SONIC_CROSS_UP, SONIC_ENTRY_LONG}
    assert BEARISH_EVENTS == {DRAGON_CROSS_DOWN, SONIC_CROSS_DOWN, SONIC_ENTRY_SHORT}
    assert BULLISH_EVENTS.isdisjoint(BEARISH_EVENTS)
