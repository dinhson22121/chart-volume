import types

import pandas as pd
import pytest

from app.models import TradeScenario, Timeframe
from app.services import scenario_backtest
from app.wyckoff import BEARISH_EVENTS, BULLISH_EVENTS, RANGING_PHASES, Levels
from app.wyckoff.events import SOW, SPRING, WyckoffEvent
from app.wyckoff.phase import PHASE_RANGING, PHASE_MARKUP

STRATEGY = "wyckoff"
LEVELS = Levels(support=90.0, resistance=110.0)  # range height = 20


def _candle(day: int, *, low: float, high: float, close: float, open: float | None = None):
    t0 = pd.Timestamp("2025-01-01")
    return types.SimpleNamespace(
        bucket_start=(t0 + pd.Timedelta(days=day)).to_pydatetime(),
        open=open if open is not None else close, low=low, high=high, close=close,
    )


def _event(event_type: str, index: int, candle, volume_confirmed: bool | None = True) -> WyckoffEvent:
    return WyckoffEvent(
        type=event_type, index=index, ts=candle.bucket_start, price=candle.close, volume_confirmed=volume_confirmed
    )


def _select_scenario():
    from sqlmodel import select

    return select(TradeScenario)


def _fake_strategy_module(phase: str = PHASE_RANGING):
    return types.SimpleNamespace(analyze=lambda *a, **k: types.SimpleNamespace(phase=phase))


def _run(session, ticker, candles, events, phase=PHASE_RANGING, daily_trend=None):
    return scenario_backtest.run_backtest(
        session, ticker, Timeframe.DAILY, STRATEGY, candles, events, BULLISH_EVENTS, BEARISH_EVENTS, LEVELS,
        _fake_strategy_module(phase), None, daily_trend, RANGING_PHASES,
    )


def test_creates_and_resolves_a_scenario_immediately_against_known_future_candles(session):
    # No fixed take-profit anymore (see trade_scenario.TRAIL_ATR_MULT) --
    # "hit_tp" now means the stop trailed to breakeven-or-better and got
    # hit. Needs >=15 real pre-event candles for a computable ATR
    # (ATR_PERIOD=14); the flat low=95/high=105/close=100 shape gives TR=10
    # per bar (constant close -> no gap component) so ATR=10.0 exactly.
    candles = [_candle(i, low=95.0, high=105.0, close=100.0) for i in range(15)]
    event = _event(SPRING, 14, candles[14])
    candles.append(_candle(15, open=103.0, low=101.0, high=104.0, close=103.0))  # entry fill
    # unrealized = 115-103 = 12 >= risk_distance (103-94.715=8.285) -> trail
    # activates: trail_level = 115-1.5*10=100 -> current_stop=max(94.715,103,100)=103 (breakeven).
    candles.append(_candle(16, low=100.0, high=115.0, close=105.0))
    candles.append(_candle(17, low=102.0, high=106.0, close=104.0))  # low=102 pierces the 103 stop

    created = _run(session, "FPT", candles, [event])

    assert created == 1
    row = session.exec(_select_scenario()).one()
    assert row.source == "backtest"
    assert row.status == "hit_tp"  # resolved in the same call, no waiting for a later sync
    assert row.entry == 103.0
    assert row.exit_price == pytest.approx(103.0)


def test_never_calls_ai_even_when_a_provider_is_configured(session, mocker):
    # Backtests replay years of history in one call -- spending real AI cost
    # explaining each ancient historical event (instead of the deterministic
    # template) would be needlessly slow and expensive for data nobody reads
    # narratively.
    mocker.patch("app.services.trade_scenario.narrative_mod.is_available", return_value=True)
    call = mocker.patch("app.services.trade_scenario.narrative_mod.call_provider_raw")
    candles = [_candle(i, low=90.0, high=110.0, close=100.0) for i in range(6)]
    candles[5] = _candle(5, low=95.0, high=101.0, close=100.0)
    candles.append(_candle(6, open=103.0, low=102.0, high=105.0, close=104.0))
    event = _event(SPRING, 5, candles[5])

    _run(session, "FPT", candles, [event])

    call.assert_not_called()
    row = session.exec(_select_scenario()).one()
    assert "Tín hiệu" in row.explanation  # template, not an AI-written string


def test_walks_multiple_qualifying_events_in_chronological_order(session):
    # No fixed take-profit anymore (see trade_scenario.TRAIL_ATR_MULT) --
    # event #2's "hit_tp" now needs a computable ATR (>=15 real pre-event
    # candles) to let the trailing stop activate. Event #1's SL-hit doesn't
    # depend on ATR (an immediate stop-out never reaches the trail-update
    # step), so its short setup is unchanged.
    candles = [_candle(i, low=95.0, high=105.0, close=100.0) for i in range(5)]
    candles.append(_candle(5, low=95.0, high=105.0, close=100.0))  # event #1 bar
    candles.append(_candle(6, open=103.0, low=101.0, high=104.0, close=103.0))  # entry #1
    candles.append(_candle(7, low=60.0, high=105.0, close=61.0))  # SL hit for #1 (SL~94.7)
    # Filler so event #2 has >=15 clean pre-event candles for its own ATR
    # window (range(len-14, len)) -- long enough that it never reaches back
    # into the crash bar at index 7 or event #1's own non-flat bars.
    for i in range(8, 24):
        candles.append(_candle(i, low=95.0, high=105.0, close=100.0))
    candles.append(_candle(24, low=95.0, high=105.0, close=100.0))  # event #2 bar
    candles.append(_candle(25, open=103.0, low=101.0, high=104.0, close=103.0))  # entry #2
    # unrealized = 115-103 = 12 >= risk_distance (103-94.715=8.285) -> trail
    # activates: trail_level = 115-1.5*10=100 -> current_stop=max(94.715,103,100)=103 (breakeven).
    candles.append(_candle(26, low=100.0, high=115.0, close=105.0))
    candles.append(_candle(27, low=102.0, high=106.0, close=104.0))  # low=102 pierces the 103 stop

    first = _event(SPRING, 5, candles[5])
    second = _event(SPRING, 24, candles[24])

    created = _run(session, "FPT", candles, [first, second])

    assert created == 2
    rows = sorted(session.exec(_select_scenario()).all(), key=lambda r: r.event_ts)
    assert rows[0].status == "hit_sl"
    assert rows[1].status == "hit_tp"


def test_does_not_start_a_new_candidate_while_the_previous_one_is_still_unresolved(session):
    candles = [_candle(i, low=90.0, high=110.0, close=100.0) for i in range(6)]
    candles[5] = _candle(5, low=95.0, high=101.0, close=100.0)
    candles.append(_candle(6, open=103.0, low=102.0, high=105.0, close=104.0))  # entry #1, never resolves
    for i in range(7, 13):
        candles.append(_candle(i, low=97.0, high=103.0, close=100.0))
    candles[12] = _candle(12, low=95.0, high=101.0, close=100.0)  # second qualifying event bar
    candles.append(_candle(13, open=103.0, low=102.0, high=105.0, close=104.0))

    first = _event(SPRING, 5, candles[5])
    second = _event(SPRING, 12, candles[12])

    created = _run(session, "FPT", candles, [first, second])

    assert created == 1  # second event never gets a candidate: first is still "active"
    row = session.exec(_select_scenario()).one()
    assert row.status == "active"
    assert row.event_ts == candles[5].bucket_start


def test_backtest_row_does_not_collide_with_a_live_row_for_the_same_event(session):
    from app.ai.narrative import PROVIDER_ANTHROPIC, ProviderConfig
    from app.services import trade_scenario

    candles = [_candle(i, low=90.0, high=110.0, close=100.0) for i in range(6)]
    candles[5] = _candle(5, low=95.0, high=101.0, close=100.0)
    candles.append(_candle(6, open=103.0, low=102.0, high=105.0, close=104.0))
    event = _event(SPRING, 5, candles[5])

    provider_cfg = ProviderConfig(provider=PROVIDER_ANTHROPIC, model="claude-sonnet-4-5", api_key="", language="vi")
    trade_scenario.sync_scenarios(
        session, "FPT", Timeframe.DAILY, STRATEGY, candles, [event], BULLISH_EVENTS, LEVELS,
        provider_cfg, _fake_strategy_module(), None, None, RANGING_PHASES,
    )
    created = _run(session, "FPT", candles, [event])

    assert created == 1
    rows = session.exec(_select_scenario()).all()
    assert len(rows) == 2
    assert {r.source for r in rows} == {"live", "backtest"}


def test_rerunning_a_backtest_clears_its_own_previous_rows_first(session):
    candles = [_candle(i, low=90.0, high=110.0, close=100.0) for i in range(6)]
    candles[5] = _candle(5, low=95.0, high=101.0, close=100.0)
    candles.append(_candle(6, open=103.0, low=102.0, high=105.0, close=104.0))
    candles.append(_candle(7, low=102.0, high=125.0, close=123.0))
    event = _event(SPRING, 5, candles[5])

    _run(session, "FPT", candles, [event])
    created_again = _run(session, "FPT", candles, [event])

    assert created_again == 1
    rows = session.exec(_select_scenario()).all()
    assert len(rows) == 1  # not duplicated by the second run


def test_events_blocked_by_the_phase_gate_produce_no_candidate(session):
    candles = [_candle(i, low=90.0, high=110.0, close=100.0) for i in range(6)]
    candles[5] = _candle(5, low=95.0, high=101.0, close=100.0)
    candles.append(_candle(6, open=103.0, low=102.0, high=105.0, close=104.0))
    event = _event(SPRING, 5, candles[5])

    created = _run(session, "FPT", candles, [event], phase=PHASE_MARKUP)  # not a ranging phase

    assert created == 0
    assert session.exec(_select_scenario()).first() is None


def test_continuation_events_never_spawn_a_backtest_candidate(session):
    from app.wyckoff.events import NO_DEMAND

    candles = [_candle(i, low=90.0, high=110.0, close=100.0) for i in range(6)]
    candles.append(_candle(6, low=90.0, high=110.0, close=100.0))
    event = _event(NO_DEMAND, 5, candles[5])

    created = _run(session, "FPT", candles, [event])

    assert created == 0
    assert session.exec(_select_scenario()).first() is None


def test_returns_zero_when_no_events_qualify(session):
    candles = [_candle(i, low=90.0, high=110.0, close=100.0) for i in range(6)]

    created = _run(session, "FPT", candles, [])

    assert created == 0
