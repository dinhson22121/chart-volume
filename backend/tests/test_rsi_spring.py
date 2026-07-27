import types

import pandas as pd
import pytest

from app.wyckoff import rsi_spring

BULLISH = rsi_spring.RSI_SPRING
BEARISH = rsi_spring.RSI_UPTHRUST


def _candle(day: int, close: float):
    t0 = pd.Timestamp("2025-01-01")
    return types.SimpleNamespace(
        bucket_start=(t0 + pd.Timedelta(days=day)).to_pydatetime(),
        open=close, high=close, low=close, close=close, volume=1000.0,
    )


# --- compute_rsi: standard Wilder's RSI, no look-ahead (only past closes) ---

def test_compute_rsi_approaches_100_on_a_monotonic_uptrend():
    closes = [100.0 + i for i in range(30)]  # steady +1 every bar, never a single down-tick
    rsi = rsi_spring.compute_rsi(closes, period=14)
    assert rsi[-1] == pytest.approx(100.0, abs=0.01)


def test_compute_rsi_approaches_0_on_a_monotonic_downtrend():
    closes = [100.0 - i for i in range(30)]
    rsi = rsi_spring.compute_rsi(closes, period=14)
    assert rsi[-1] == pytest.approx(0.0, abs=0.01)


def test_compute_rsi_is_neutral_on_flat_prices():
    closes = [100.0] * 20
    rsi = rsi_spring.compute_rsi(closes, period=14)
    assert rsi[-1] == pytest.approx(50.0)


def test_compute_rsi_returns_none_before_enough_history():
    closes = [100.0 + i for i in range(10)]  # fewer than period+1 closes
    rsi = rsi_spring.compute_rsi(closes, period=14)
    assert all(v is None for v in rsi)


def test_compute_rsi_output_length_matches_input():
    closes = [100.0 + (i % 3) for i in range(25)]
    rsi = rsi_spring.compute_rsi(closes, period=14)
    assert len(rsi) == len(closes)


# --- detect_events: causal (no future bars) oversold-then-turning-up / ---
# --- overbought-then-turning-down detection, RSI itself stubbed out so ---
# --- these tests are about the DETECTION LOGIC, not the RSI formula.   ---
# All sequences are padded to >= RSI_PERIOD + 1 candles so analyze()'s own
# "not enough history" gate never masks the (mocked) RSI values.

def _detect_with_rsi(mocker, rsi_values, candles=None):
    candles = candles or [_candle(i, 100.0) for i in range(len(rsi_values))]
    mocker.patch.object(rsi_spring, "compute_rsi", return_value=rsi_values)
    result = rsi_spring.analyze(candles, None, None, "vi")
    return result


def test_fires_rsi_spring_when_oversold_then_two_bars_rising(mocker):
    # Dips to 20 (oversold, <=30) at index 4, then rises for 2 steps
    # (4->5->6) -- confirmation completes at index 6.
    rsi_values = [50.0, 45.0, 35.0, 28.0, 20.0, 24.0, 28.0] + [28.0] * 10
    result = _detect_with_rsi(mocker, rsi_values)

    spring_events = [e for e in result.events if e.type == BULLISH]
    assert len(spring_events) == 1
    assert spring_events[0].index == 6


def test_no_rsi_spring_without_ever_touching_oversold(mocker):
    # Rises two steps in a row but RSI never dipped to <=30 beforehand.
    rsi_values = [50.0, 45.0, 40.0, 38.0, 42.0, 46.0] + [46.0] * 10
    result = _detect_with_rsi(mocker, rsi_values)

    assert not [e for e in result.events if e.type == BULLISH]


def test_no_rsi_spring_while_still_falling(mocker):
    rsi_values = [50.0, 40.0, 30.0, 25.0, 20.0, 15.0] + [15.0] * 10
    result = _detect_with_rsi(mocker, rsi_values)

    assert not [e for e in result.events if e.type == BULLISH]


def test_rsi_spring_fires_only_once_per_rally_not_every_bar(mocker):
    # A long, sustained rise off an oversold trough -- should fire once when
    # the 2-step-rise confirmation FIRST completes (index 5), not again on
    # every subsequent bar of the same continuing rally.
    rsi_values = [50.0, 40.0, 25.0, 20.0, 22.0, 25.0, 29.0, 33.0, 37.0, 41.0, 45.0] + [45.0] * 5
    result = _detect_with_rsi(mocker, rsi_values)

    spring_events = [e for e in result.events if e.type == BULLISH]
    assert len(spring_events) == 1
    assert spring_events[0].index == 5


def test_no_rsi_spring_if_oversold_touch_is_outside_the_lookback_window(mocker):
    # RSI dipped to 20 at index 0, but by the time it rises (2-step confirm
    # ending far later) the trough is well outside LOOKBACK_BARS -- too
    # stale to count as "the same recovery".
    rsi_values = [20.0] + [50.0] * (rsi_spring.LOOKBACK_BARS + 5) + [55.0, 60.0] + [60.0] * 5
    result = _detect_with_rsi(mocker, rsi_values)

    assert not [e for e in result.events if e.type == BULLISH]


def test_fires_rsi_upthrust_when_overbought_then_two_bars_falling(mocker):
    rsi_values = [50.0, 55.0, 65.0, 72.0, 78.0, 74.0, 70.0] + [70.0] * 10
    result = _detect_with_rsi(mocker, rsi_values)

    upthrust_events = [e for e in result.events if e.type == BEARISH]
    assert len(upthrust_events) == 1
    assert upthrust_events[0].index == 6


def test_events_carry_correct_index_ts_and_price(mocker):
    rsi_values = [50.0, 40.0, 25.0, 22.0, 26.0, 30.0] + [30.0] * 10
    candles = [_candle(i, 100.0 + i) for i in range(len(rsi_values))]
    result = _detect_with_rsi(mocker, rsi_values, candles=candles)

    spring_events = [e for e in result.events if e.type == BULLISH]
    assert len(spring_events) == 1
    event = spring_events[0]
    assert event.index == 5
    assert event.ts == candles[5].bucket_start
    assert event.price == candles[5].close


def test_module_exposes_strategy_protocol_constants():
    assert rsi_spring.BULLISH_EVENTS == {BULLISH}
    assert rsi_spring.BEARISH_EVENTS == {BEARISH}
    assert "Ranging" in rsi_spring.RANGING_PHASES


def test_analyze_returns_ranging_phase_always():
    # No real market-structure/phase concept in this simple momentum
    # detector -- always reports Ranging so _build_scenario_candidate's
    # phase-before-event gate never blocks it (see rsi_spring's module
    # docstring).
    candles = [_candle(i, 100.0 + i) for i in range(20)]
    result = rsi_spring.analyze(candles, None, None, "vi")
    assert result.phase == "Ranging"


def test_analyze_returns_none_phase_result_gracefully_with_too_few_candles():
    candles = [_candle(i, 100.0) for i in range(3)]
    result = rsi_spring.analyze(candles, None, None, "vi")
    assert result.events == []
    assert result.phase == "Ranging"
