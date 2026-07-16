import types

import pandas as pd
import pytest

from app.ai.narrative import PROVIDER_ANTHROPIC, ProviderConfig
from app.models import TradeScenario, Timeframe
from app.services import trade_scenario
from app.wyckoff import BEARISH_EVENTS, BULLISH_EVENTS, Levels
from app.wyckoff.events import SOW, SPRING, WyckoffEvent

STRATEGY = "wyckoff"
LEVELS = Levels(support=90.0, resistance=110.0)  # range height = 20


def _candle(day: int, *, low: float, high: float, close: float):
    t0 = pd.Timestamp("2025-01-01")
    return types.SimpleNamespace(
        bucket_start=(t0 + pd.Timedelta(days=day)).to_pydatetime(),
        low=low, high=high, close=close,
    )


def _event(event_type: str, index: int, candle) -> WyckoffEvent:
    return WyckoffEvent(type=event_type, index=index, ts=candle.bucket_start, price=candle.close)


def _select_scenario():
    from sqlmodel import select

    return select(TradeScenario)


def _sync(session, ticker, candles, events, language="vi"):
    # api_key="" -> is_available() is False -> explanation always falls back
    # to the deterministic template, so these tests never make a real AI call.
    provider_cfg = ProviderConfig(provider=PROVIDER_ANTHROPIC, model="claude-sonnet-4-5", api_key="", language=language)
    trade_scenario.sync_scenarios(
        session, ticker, Timeframe.DAILY, STRATEGY, candles, events, BULLISH_EVENTS, BEARISH_EVENTS, LEVELS,
        provider_cfg,
    )


def test_creates_bullish_scenario_with_entry_sl_tp_from_formulas(session):
    candles = [_candle(i, low=90.0, high=110.0, close=100.0) for i in range(6)]
    candles[5] = _candle(5, low=95.0, high=101.0, close=100.0)
    event = _event(SPRING, 5, candles[5])

    _sync(session, "FPT", candles, [event])

    row = session.exec(_select_scenario()).one()
    assert row.is_bullish is True
    assert row.entry == 100.0
    assert row.stop_loss == pytest.approx(95.0 * (1 - trade_scenario.SL_BUFFER_PCT))
    assert row.take_profit == pytest.approx(100.0 + 20.0)  # entry + range height
    assert row.max_bars == trade_scenario.DEFAULT_MAX_BARS
    assert row.status == "active"
    assert row.explanation  # template fallback since no AI key is configured
    assert SPRING in row.explanation


def test_explanation_uses_template_when_provider_unavailable(session):
    candles = [_candle(i, low=90.0, high=110.0, close=100.0) for i in range(6)]
    candles[5] = _candle(5, low=95.0, high=101.0, close=100.0)
    event = _event(SPRING, 5, candles[5])

    _sync(session, "FPT", candles, [event])

    row = session.exec(_select_scenario()).one()
    assert "Tín hiệu" in row.explanation  # Vietnamese template wording


def test_explanation_uses_ai_when_provider_available(session, mocker):
    mocker.patch.object(trade_scenario.narrative_mod, "is_available", return_value=True)
    mocker.patch.object(trade_scenario.narrative_mod, "call_provider_raw", return_value="AI-written explanation.")
    candles = [_candle(i, low=90.0, high=110.0, close=100.0) for i in range(6)]
    candles[5] = _candle(5, low=95.0, high=101.0, close=100.0)
    event = _event(SPRING, 5, candles[5])

    _sync(session, "FPT", candles, [event])

    row = session.exec(_select_scenario()).one()
    assert row.explanation == "AI-written explanation."


def test_explanation_falls_back_to_template_on_ai_failure(session, mocker):
    mocker.patch.object(trade_scenario.narrative_mod, "is_available", return_value=True)
    mocker.patch.object(trade_scenario.narrative_mod, "call_provider_raw", side_effect=RuntimeError("provider down"))
    candles = [_candle(i, low=90.0, high=110.0, close=100.0) for i in range(6)]
    candles[5] = _candle(5, low=95.0, high=101.0, close=100.0)
    event = _event(SPRING, 5, candles[5])

    _sync(session, "FPT", candles, [event])

    row = session.exec(_select_scenario()).one()
    assert "Tín hiệu" in row.explanation  # AI failed -> template, not a crash


def test_take_profit_range_excludes_the_event_bar_itself(session):
    # Regression: a breakout event's own bar routinely sets a new high (that's
    # what makes it a breakout) -- if the range-height calc included that bar,
    # "resistance" would collapse to ~the event's own price, making the
    # measured-move TP degenerate instead of reflecting the real prior range.
    candles = [_candle(i, low=90.0, high=110.0, close=100.0) for i in range(6)]
    # Event bar breaks well above the prior 90-110 range.
    candles[5] = _candle(5, low=118.0, high=150.0, close=150.0)
    event = _event(SPRING, 5, candles[5])
    # BULLISH_EVENTS classifies SPRING as bullish regardless of the actual
    # price move here -- only the range-height math is under test.

    _sync(session, "FPT", candles, [event])

    row = session.exec(_select_scenario()).one()
    # Pre-event window (bars 0-4) is still 90-110 -> height 20, NOT
    # (150 - the event bar's own low) or anything derived from bar 5.
    assert row.take_profit == pytest.approx(150.0 + 20.0)


def test_range_height_is_capped_so_take_profit_never_goes_absurd_or_negative(session):
    # Regression: a single flash-crash/spike bar inside the 20-bar lookback
    # (real on volatile, low-liquidity crypto) can make max(high)-min(low)
    # many multiples of the current price -- observed producing a NEGATIVE
    # take-profit on the bearish side, which is nonsensical (price can't go
    # negative). One bar in the pre-event window crashes from 300 to 10 and
    # mostly recovers, so the raw window height would be ~290 against an
    # entry of 100 -- capped to 50% of entry (height 50) instead.
    candles = [_candle(i, low=95.0, high=105.0, close=100.0) for i in range(7)]
    candles[2] = _candle(2, low=10.0, high=300.0, close=100.0)  # flash crash + spike bar
    candles[6] = _candle(6, low=90.0, high=100.0, close=95.0)  # bearish event bar
    event = _event(SOW, 6, candles[6])

    _sync(session, "FPT", candles, [event])

    row = session.exec(_select_scenario()).one()
    assert row.take_profit > 0  # never negative, regardless of the raw window
    assert row.take_profit == pytest.approx(row.entry - row.entry * trade_scenario.MAX_RANGE_HEIGHT_PCT)


def test_creates_bearish_scenario_with_mirrored_formulas(session):
    candles = [_candle(i, low=90.0, high=110.0, close=100.0) for i in range(6)]
    candles[5] = _candle(5, low=99.0, high=105.0, close=100.0)
    event = _event(SOW, 5, candles[5])

    _sync(session, "FPT", candles, [event])

    row = session.exec(_select_scenario()).one()
    assert row.is_bullish is False
    assert row.entry == 100.0
    assert row.stop_loss == pytest.approx(105.0 * (1 + trade_scenario.SL_BUFFER_PCT))
    assert row.take_profit == pytest.approx(100.0 - 20.0)


def test_first_ever_run_against_a_long_history_picks_the_latest_event_not_the_oldest(session):
    # Regression: on the very first run for a ticker with years of candle
    # history, `events` contains every qualifying event ever detected. A
    # naive "first untracked event in order" loop would latch onto the
    # oldest one (e.g. from 2 years ago) and crawl through the backlog one
    # ancient event at a time -- never reaching a currently-relevant signal.
    candles = [_candle(i, low=90.0, high=110.0, close=100.0) for i in range(60)]
    candles[5] = _candle(5, low=95.0, high=101.0, close=100.0)  # old Spring
    candles[55] = _candle(55, low=96.0, high=102.0, close=101.0)  # recent Spring
    old_event = _event(SPRING, 5, candles[5])
    recent_event = _event(SPRING, 55, candles[55])

    _sync(session, "FPT", candles, [old_event, recent_event])

    row = session.exec(_select_scenario()).one()
    assert row.event_ts == candles[55].bucket_start


def test_sync_is_idempotent_for_the_same_event(session):
    candles = [_candle(i, low=90.0, high=110.0, close=100.0) for i in range(6)]
    candles[5] = _candle(5, low=95.0, high=101.0, close=100.0)
    event = _event(SPRING, 5, candles[5])

    _sync(session, "FPT", candles, [event])
    _sync(session, "FPT", candles, [event])

    assert len(session.exec(_select_scenario()).all()) == 1


def test_no_new_scenario_while_one_is_already_active(session):
    # Only 3 bars elapse after the first event (well under DEFAULT_MAX_BARS),
    # so the first scenario is still genuinely active when the second event
    # is checked -- confirms the second event is skipped, not that the first
    # one merely expired first.
    candles = [_candle(i, low=90.0, high=110.0, close=100.0) for i in range(9)]
    candles[5] = _candle(5, low=95.0, high=101.0, close=100.0)
    candles[8] = _candle(8, low=96.0, high=102.0, close=101.0)
    first = _event(SPRING, 5, candles[5])
    second = _event(SPRING, 8, candles[8])

    _sync(session, "FPT", candles, [first])
    _sync(session, "FPT", candles, [second])

    rows = session.exec(_select_scenario()).all()
    assert len(rows) == 1
    assert rows[0].event_ts == candles[5].bucket_start
    assert rows[0].status == "active"


def test_closes_hit_sl_when_a_later_candle_closes_past_stop_loss(session):
    candles = [_candle(i, low=90.0, high=110.0, close=100.0) for i in range(6)]
    candles[5] = _candle(5, low=95.0, high=101.0, close=100.0)
    event = _event(SPRING, 5, candles[5])
    _sync(session, "FPT", candles, [event])
    stop_loss = session.exec(_select_scenario()).one().stop_loss

    # A later candle closes below the stop loss.
    candles.append(_candle(6, low=stop_loss - 1, high=stop_loss - 1, close=stop_loss - 1))
    _sync(session, "FPT", candles, [event])

    row = session.exec(_select_scenario()).one()
    assert row.status == "hit_sl"
    assert row.closed_bar_ts == candles[6].bucket_start
    assert row.closed_at is not None
    assert str(round(stop_loss, 2)) in row.close_reason or "SL" in row.close_reason


def test_close_reason_respects_language(session):
    candles = [_candle(i, low=90.0, high=110.0, close=100.0) for i in range(6)]
    candles[5] = _candle(5, low=95.0, high=101.0, close=100.0)
    event = _event(SPRING, 5, candles[5])
    _sync(session, "FPT", candles, [event], language="en")
    stop_loss = session.exec(_select_scenario()).one().stop_loss

    candles.append(_candle(6, low=stop_loss - 1, high=stop_loss - 1, close=stop_loss - 1))
    _sync(session, "FPT", candles, [event], language="en")

    row = session.exec(_select_scenario()).one()
    assert "SL" in row.close_reason
    assert "vượt qua" not in row.close_reason


def test_closes_hit_tp_when_a_later_candle_reaches_take_profit(session):
    candles = [_candle(i, low=90.0, high=110.0, close=100.0) for i in range(6)]
    candles[5] = _candle(5, low=95.0, high=101.0, close=100.0)
    event = _event(SPRING, 5, candles[5])
    _sync(session, "FPT", candles, [event])
    take_profit = session.exec(_select_scenario()).one().take_profit

    candles.append(_candle(6, low=take_profit, high=take_profit + 1, close=take_profit))
    _sync(session, "FPT", candles, [event])

    row = session.exec(_select_scenario()).one()
    assert row.status == "hit_tp"
    assert row.closed_bar_ts == candles[6].bucket_start


def test_expires_after_max_bars_with_neither_tp_nor_sl_hit(session):
    candles = [_candle(i, low=90.0, high=110.0, close=100.0) for i in range(6)]
    candles[5] = _candle(5, low=95.0, high=101.0, close=100.0)
    event = _event(SPRING, 5, candles[5])
    _sync(session, "FPT", candles, [event])

    # DEFAULT_MAX_BARS candles afterwards, all flat -- never touches SL or TP.
    for i in range(6, 6 + trade_scenario.DEFAULT_MAX_BARS):
        candles.append(_candle(i, low=99.0, high=101.0, close=100.0))
    _sync(session, "FPT", candles, [event])

    row = session.exec(_select_scenario()).one()
    assert row.status == "expired"
    assert "hết" in row.close_reason.lower() or str(trade_scenario.DEFAULT_MAX_BARS) in row.close_reason


def test_get_scenario_prefers_active_over_closed(session):
    candles = [_candle(i, low=90.0, high=110.0, close=100.0) for i in range(6)]
    candles[5] = _candle(5, low=95.0, high=101.0, close=100.0)
    event = _event(SPRING, 5, candles[5])
    _sync(session, "FPT", candles, [event])

    scenario = trade_scenario.get_scenario(session, "FPT", Timeframe.DAILY, STRATEGY)
    assert scenario is not None
    assert scenario.status == "active"


def test_get_scenario_falls_back_to_most_recently_closed(session):
    candles = [_candle(i, low=90.0, high=110.0, close=100.0) for i in range(6)]
    candles[5] = _candle(5, low=95.0, high=101.0, close=100.0)
    event = _event(SPRING, 5, candles[5])
    _sync(session, "FPT", candles, [event])
    stop_loss = session.exec(_select_scenario()).one().stop_loss
    candles.append(_candle(6, low=stop_loss - 1, high=stop_loss - 1, close=stop_loss - 1))
    _sync(session, "FPT", candles, [event])

    scenario = trade_scenario.get_scenario(session, "FPT", Timeframe.DAILY, STRATEGY)
    assert scenario is not None
    assert scenario.status == "hit_sl"


def test_get_scenario_returns_none_when_nothing_tracked(session):
    assert trade_scenario.get_scenario(session, "NOPE", Timeframe.DAILY, STRATEGY) is None
