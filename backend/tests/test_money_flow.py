"""Tests for app.services.money_flow: technical volume/price-based money-flow
detection -- reintroduces the Volume Accumulation/Distribution detection
logic removed from app.wyckoff.accumulation (see d95c1f6) as a standalone
analysis/display layer (not a trade-scenario-generating strategy)."""

import types

import pandas as pd

from app.services import money_flow

BASE = dict(open=100.0, high=101.0, low=99.0, close=100.0, volume=1000.0)


def _to_candles(bars):
    t0 = pd.Timestamp("2025-01-01")
    return [
        types.SimpleNamespace(bucket_start=(t0 + pd.Timedelta(days=i)).to_pydatetime(), **b)
        for i, b in enumerate(bars)
    ]


def base_bars(n=10):
    return [dict(BASE) for _ in range(n)]


def test_detects_money_flow_in_on_volume_spike_and_price_up():
    # volume 2500 vs trailing-10 average 1000 = 2.5x (> 2.0x threshold),
    # price +2% (> 1% threshold) -> inflow.
    spike_bar = dict(open=100.0, high=103.0, low=100.0, close=102.0, volume=2500.0)
    candles = _to_candles(base_bars(10) + [spike_bar])

    events = money_flow.detect_money_flow_events(candles)

    assert len(events) == 1
    assert events[0].type == money_flow.MONEY_FLOW_IN
    assert events[0].index == 10


def test_detects_money_flow_out_on_volume_spike_and_price_down():
    spike_bar = dict(open=100.0, high=100.0, low=97.0, close=98.0, volume=2500.0)
    candles = _to_candles(base_bars(10) + [spike_bar])

    events = money_flow.detect_money_flow_events(candles)

    assert len(events) == 1
    assert events[0].type == money_flow.MONEY_FLOW_OUT


def test_no_event_on_normal_volume():
    normal_bar = dict(open=100.0, high=103.0, low=100.0, close=102.0, volume=1100.0)  # 1.1x, below 2.0x
    candles = _to_candles(base_bars(10) + [normal_bar])

    assert money_flow.detect_money_flow_events(candles) == []


def test_no_event_on_volume_spike_without_price_move():
    quiet_bar = dict(open=100.0, high=100.5, low=99.8, close=100.2, volume=2500.0)  # spike but < 1% move
    candles = _to_candles(base_bars(10) + [quiet_bar])

    assert money_flow.detect_money_flow_events(candles) == []


def test_insufficient_history_returns_empty():
    candles = _to_candles(base_bars(5))  # fewer than volume_lookback=10
    assert money_flow.detect_money_flow_events(candles) == []


def test_analyze_reports_net_inflow_when_recent_window_favors_in():
    # Each spike bar's own close must be its own >1% move from the PRECEDING
    # bar's close (not the base level) -- 3 distinct, escalating closes/
    # volumes, not the same bar repeated (which would show 0% change
    # bar-to-bar and only the first would count).
    spike_1 = dict(open=100.0, high=103.0, low=100.0, close=102.0, volume=2500.0)
    spike_2 = dict(open=102.0, high=105.0, low=102.0, close=104.5, volume=2600.0)
    spike_3 = dict(open=104.5, high=108.0, low=104.5, close=107.2, volume=3000.0)
    bars = base_bars(10) + [spike_1, spike_2, spike_3] + base_bars(5)
    candles = _to_candles(bars)

    result = money_flow.analyze_money_flow(candles)

    assert result.recent_in_count == 3
    assert result.recent_out_count == 0
    assert result.net_signal == money_flow.NET_INFLOW


def test_analyze_reports_net_outflow_when_recent_window_favors_out():
    spike_1 = dict(open=100.0, high=100.0, low=97.0, close=98.0, volume=2500.0)
    spike_2 = dict(open=98.0, high=98.0, low=94.5, close=95.5, volume=2600.0)
    bars = base_bars(10) + [spike_1, spike_2] + base_bars(5)
    candles = _to_candles(bars)

    result = money_flow.analyze_money_flow(candles)

    assert result.recent_out_count == 2
    assert result.recent_in_count == 0
    assert result.net_signal == money_flow.NET_OUTFLOW


def test_analyze_reports_neutral_when_no_recent_signal():
    candles = _to_candles(base_bars(25))

    result = money_flow.analyze_money_flow(candles)

    assert result.recent_in_count == 0
    assert result.recent_out_count == 0
    assert result.net_signal == money_flow.NET_NEUTRAL


def test_analyze_as_of_reflects_last_candle():
    candles = _to_candles(base_bars(15))
    result = money_flow.analyze_money_flow(candles)
    assert result.as_of == candles[-1].bucket_start


def test_analyze_empty_candles_returns_none_as_of():
    result = money_flow.analyze_money_flow([])
    assert result.as_of is None
    assert result.events == []
    assert result.net_signal == money_flow.NET_NEUTRAL


def test_result_as_dict_is_json_serializable_shape():
    spike_bar = dict(open=100.0, high=103.0, low=100.0, close=102.0, volume=2500.0)
    candles = _to_candles(base_bars(10) + [spike_bar])
    result = money_flow.analyze_money_flow(candles)

    out = money_flow.result_as_dict(result)

    assert out["net_signal"] == money_flow.NET_INFLOW
    assert out["recent_in_count"] == 1
    assert out["recent_out_count"] == 0
    assert out["recent_window"] == money_flow.RECENT_WINDOW
    assert out["as_of"] == candles[-1].bucket_start.isoformat()
    assert out["events"][0]["type"] == money_flow.MONEY_FLOW_IN
    assert out["events"][0]["ts"] == candles[10].bucket_start.isoformat()
    assert out["events"][0]["price"] == 102.0


def test_result_as_dict_handles_empty_result():
    out = money_flow.result_as_dict(money_flow.MoneyFlowResult())
    assert out["as_of"] is None
    assert out["events"] == []


def test_event_causal_only_depends_on_trailing_window():
    # A later bar's own huge volume/price move must never retroactively
    # change an earlier bar's classification (no look-ahead).
    spike_bar = dict(open=100.0, high=103.0, low=100.0, close=102.0, volume=2500.0)
    later_crash = dict(open=100.0, high=100.0, low=50.0, close=55.0, volume=9000.0)
    candles = _to_candles(base_bars(10) + [spike_bar] + base_bars(5) + [later_crash])

    events = money_flow.detect_money_flow_events(candles)

    first_event = next(e for e in events if e.index == 10)
    assert first_event.type == money_flow.MONEY_FLOW_IN
