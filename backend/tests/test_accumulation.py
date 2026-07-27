import types

import pandas as pd
import pytest

from app.wyckoff import accumulation

BULLISH = accumulation.VOLUME_ACCUMULATION
BEARISH = accumulation.VOLUME_DISTRIBUTION


def _candle(day: int, *, close: float, volume: float, open_: float | None = None):
    t0 = pd.Timestamp("2025-01-01")
    return types.SimpleNamespace(
        bucket_start=(t0 + pd.Timedelta(days=day)).to_pydatetime(),
        open=open_ if open_ is not None else close, high=close, low=close, close=close, volume=volume,
    )


def _flat_history(n: int, *, close: float = 100.0, volume: float = 1000.0) -> list:
    """n days of unremarkable, flat-volume history -- the "nothing to see
    here" baseline every detect_events test builds a trailing window from."""
    return [_candle(i, close=close, volume=volume) for i in range(n)]


# --- detect_events: volume ratio vs trailing 10-session average + %change ---

def test_fires_accumulation_on_volume_spike_with_price_up(mocker):
    candles = _flat_history(accumulation.VOLUME_LOOKBACK)
    # today: volume 3x the flat 1000 average, price up 2% from yesterday's close.
    candles.append(_candle(accumulation.VOLUME_LOOKBACK, close=102.0, volume=3000.0))

    events = accumulation.detect_events(candles)

    assert len(events) == 1
    assert events[0].type == BULLISH
    assert events[0].index == accumulation.VOLUME_LOOKBACK


def test_fires_distribution_on_volume_spike_with_price_down(mocker):
    candles = _flat_history(accumulation.VOLUME_LOOKBACK)
    candles.append(_candle(accumulation.VOLUME_LOOKBACK, close=98.0, volume=3000.0))

    events = accumulation.detect_events(candles)

    assert len(events) == 1
    assert events[0].type == BEARISH


def test_no_event_when_volume_ratio_at_or_below_threshold(mocker):
    candles = _flat_history(accumulation.VOLUME_LOOKBACK)
    # Exactly 2x -- threshold is a strict ">", not ">=".
    candles.append(_candle(accumulation.VOLUME_LOOKBACK, close=102.0, volume=2000.0))

    events = accumulation.detect_events(candles)

    assert events == []


def test_no_event_when_price_change_too_small_despite_volume_spike(mocker):
    candles = _flat_history(accumulation.VOLUME_LOOKBACK)
    # Volume spikes, but price barely moves (0.2%, below the 1% threshold).
    candles.append(_candle(accumulation.VOLUME_LOOKBACK, close=100.2, volume=3000.0))

    events = accumulation.detect_events(candles)

    assert events == []


def test_no_event_when_price_up_but_volume_not_elevated(mocker):
    candles = _flat_history(accumulation.VOLUME_LOOKBACK)
    candles.append(_candle(accumulation.VOLUME_LOOKBACK, close=105.0, volume=1000.0))  # same as average

    events = accumulation.detect_events(candles)

    assert events == []


def test_trailing_average_excludes_todays_own_volume(mocker):
    # If today's own huge volume were folded into its own "trailing average"
    # denominator, a spike could never clear the ratio threshold no matter
    # how large -- the average must be computed over the PRIOR N sessions only.
    candles = _flat_history(accumulation.VOLUME_LOOKBACK, volume=1000.0)
    candles.append(_candle(accumulation.VOLUME_LOOKBACK, close=102.0, volume=100_000.0))

    events = accumulation.detect_events(candles)

    assert len(events) == 1
    assert events[0].type == BULLISH


def test_fires_only_on_the_spike_day_not_every_subsequent_day(mocker):
    # A spike on day N followed by ordinary flat days after shouldn't keep
    # re-firing just because day N is still inside a LATER day's lookback
    # window -- each day's signal depends only on ITS OWN volume/price vs
    # its own trailing window.
    n = accumulation.VOLUME_LOOKBACK
    candles = _flat_history(n)
    candles.append(_candle(n, close=102.0, volume=3000.0))
    candles += [_candle(n + 1 + i, close=102.0, volume=1000.0) for i in range(5)]

    events = accumulation.detect_events(candles)

    assert len(events) == 1


def test_no_event_before_enough_lookback_history():
    candles = _flat_history(accumulation.VOLUME_LOOKBACK - 1)
    candles.append(_candle(accumulation.VOLUME_LOOKBACK - 1, close=110.0, volume=10_000.0))

    events = accumulation.detect_events(candles)

    assert events == []


# --- analyze(): module-level protocol ---

def test_module_exposes_strategy_protocol_constants():
    assert accumulation.BULLISH_EVENTS == {BULLISH}
    assert accumulation.BEARISH_EVENTS == {BEARISH}
    assert "Ranging" in accumulation.RANGING_PHASES


def test_analyze_returns_ranging_phase_always():
    candles = _flat_history(accumulation.VOLUME_LOOKBACK + 5)
    result = accumulation.analyze(candles, None, None, "vi")
    assert result.phase == "Ranging"


def test_analyze_handles_too_few_candles_gracefully():
    candles = _flat_history(2)
    result = accumulation.analyze(candles, None, None, "vi")
    assert result.events == []
    assert result.phase == "Ranging"


def test_analyze_detects_events_end_to_end():
    candles = _flat_history(accumulation.VOLUME_LOOKBACK)
    candles.append(_candle(accumulation.VOLUME_LOOKBACK, close=102.0, volume=3000.0))
    result = accumulation.analyze(candles, None, None, "vi")
    assert len(result.events) == 1
    assert result.events[0].type == BULLISH
