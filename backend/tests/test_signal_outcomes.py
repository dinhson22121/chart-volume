import types

import pandas as pd
import pytest

from app.models import SignalOutcome, Timeframe
from app.services import signal_outcomes
from app.wyckoff import BULLISH_EVENTS
from app.wyckoff.events import SOS, SOW, SPRING, WyckoffEvent

STRATEGY = "wyckoff"


def _candle(day: int, close: float):
    t0 = pd.Timestamp("2025-01-01")
    return types.SimpleNamespace(
        bucket_start=(t0 + pd.Timedelta(days=day)).to_pydatetime(),
        close=close,
    )


def _event(event_type: str, index: int, ts, price: float) -> WyckoffEvent:
    return WyckoffEvent(type=event_type, index=index, ts=ts, price=price)


def test_record_outcomes_computes_forward_return_for_bullish_signal(session):
    # Spring at index 5, price rises steadily afterwards -> positive return -> win.
    candles = [_candle(i, 100.0 + i) for i in range(30)]
    event = _event(SPRING, 5, candles[5].bucket_start, candles[5].close)

    signal_outcomes.record_outcomes(session, "FPT", Timeframe.DAILY, STRATEGY, candles, [event], BULLISH_EVENTS)

    row = session.exec(_select_outcome()).first()
    expected_return = (candles[10].close - candles[5].close) / candles[5].close
    assert row.return_5 == pytest.approx(expected_return)
    assert row.is_win_5 is True  # bullish signal + positive return = win


def test_record_outcomes_marks_bearish_signal_win_on_negative_return(session):
    # SOW at index 5, price falls afterwards -> negative return -> win for a bearish signal.
    candles = [_candle(i, 100.0 - i) for i in range(30)]
    event = _event(SOW, 5, candles[5].bucket_start, candles[5].close)

    signal_outcomes.record_outcomes(session, "FPT", Timeframe.DAILY, STRATEGY, candles, [event], BULLISH_EVENTS)

    row = session.exec(_select_outcome()).first()
    assert row.return_5 < 0
    assert row.is_win_5 is True


def test_record_outcomes_leaves_horizon_null_when_not_enough_future_bars(session):
    candles = [_candle(i, 100.0) for i in range(8)]  # only 2 bars after index 5
    event = _event(SOS, 5, candles[5].bucket_start, candles[5].close)

    signal_outcomes.record_outcomes(session, "FPT", Timeframe.DAILY, STRATEGY, candles, [event], BULLISH_EVENTS)

    row = session.exec(_select_outcome()).first()
    assert row.return_5 is None
    assert row.return_10 is None
    assert row.return_20 is None


def test_record_outcomes_is_idempotent_and_does_not_duplicate(session):
    candles = [_candle(i, 100.0 + i) for i in range(30)]
    event = _event(SPRING, 5, candles[5].bucket_start, candles[5].close)

    signal_outcomes.record_outcomes(session, "FPT", Timeframe.DAILY, STRATEGY, candles, [event], BULLISH_EVENTS)
    signal_outcomes.record_outcomes(session, "FPT", Timeframe.DAILY, STRATEGY, candles, [event], BULLISH_EVENTS)

    rows = session.exec(_select_outcome()).all()
    assert len(rows) == 1


def test_record_outcomes_keeps_strategies_separate(session):
    # Same ticker/timeframe/event_ts but two different strategies must not collide.
    candles = [_candle(i, 100.0 + i) for i in range(30)]
    event = _event(SPRING, 5, candles[5].bucket_start, candles[5].close)

    signal_outcomes.record_outcomes(session, "FPT", Timeframe.DAILY, "wyckoff", candles, [event], BULLISH_EVENTS)
    signal_outcomes.record_outcomes(session, "FPT", Timeframe.DAILY, "other-strategy", candles, [event], BULLISH_EVENTS)

    rows = session.exec(_select_outcome()).all()
    assert len(rows) == 2
    assert {r.strategy for r in rows} == {"wyckoff", "other-strategy"}


def test_get_stats_aggregates_win_rate_and_avg_return(session):
    candles = [_candle(i, 100.0 + i) for i in range(30)]
    events = [
        _event(SPRING, 3, candles[3].bucket_start, candles[3].close),
        _event(SPRING, 10, candles[10].bucket_start, candles[10].close),
    ]
    signal_outcomes.record_outcomes(session, "FPT", Timeframe.DAILY, STRATEGY, candles, events, BULLISH_EVENTS)

    stats = signal_outcomes.get_stats(session)
    spring_stats = next(s for s in stats if s["type"] == SPRING)

    assert spring_stats["count"] == 2
    assert spring_stats["is_bullish"] is True
    assert spring_stats["win_rate_5"] == 1.0  # price rises steadily -> both are wins
    assert spring_stats["avg_return_5"] > 0


def test_get_stats_filters_by_ticker(session):
    candles = [_candle(i, 100.0 + i) for i in range(30)]
    signal_outcomes.record_outcomes(
        session, "FPT", Timeframe.DAILY, STRATEGY, candles,
        [_event(SPRING, 3, candles[3].bucket_start, candles[3].close)], BULLISH_EVENTS,
    )
    signal_outcomes.record_outcomes(
        session, "VCB", Timeframe.DAILY, STRATEGY, candles,
        [_event(SPRING, 3, candles[3].bucket_start, candles[3].close)], BULLISH_EVENTS,
    )

    assert sum(s["count"] for s in signal_outcomes.get_stats(session, ticker="FPT")) == 1
    assert sum(s["count"] for s in signal_outcomes.get_stats(session)) == 2


def test_get_stats_filters_by_strategy(session):
    candles = [_candle(i, 100.0 + i) for i in range(30)]
    signal_outcomes.record_outcomes(
        session, "FPT", Timeframe.DAILY, "wyckoff", candles,
        [_event(SPRING, 3, candles[3].bucket_start, candles[3].close)], BULLISH_EVENTS,
    )
    signal_outcomes.record_outcomes(
        session, "FPT", Timeframe.DAILY, "other-strategy", candles,
        [_event(SPRING, 10, candles[10].bucket_start, candles[10].close)], BULLISH_EVENTS,
    )

    assert sum(s["count"] for s in signal_outcomes.get_stats(session, strategy="wyckoff")) == 1
    assert sum(s["count"] for s in signal_outcomes.get_stats(session)) == 2


def _select_outcome():
    from sqlmodel import select

    return select(SignalOutcome)
