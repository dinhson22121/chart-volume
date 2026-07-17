import types

import pandas as pd
import pytest

from app.ai.narrative import PROVIDER_ANTHROPIC, ProviderConfig
from app.models import AssetClass, Symbol, TradeScenario, Timeframe
from app.services import settings_service, trade_scenario
from app.wyckoff import BEARISH_EVENTS, BULLISH_EVENTS, RANGING_PHASES, Levels
from app.wyckoff.events import NO_DEMAND, NO_SUPPLY, SELLING_CLIMAX, SOW, SPRING, WyckoffEvent
from app.wyckoff.phase import PHASE_RANGING

STRATEGY = "wyckoff"
LEVELS = Levels(support=90.0, resistance=110.0)  # range height = 20


def _candle(day: int, *, low: float, high: float, close: float):
    t0 = pd.Timestamp("2025-01-01")
    return types.SimpleNamespace(
        bucket_start=(t0 + pd.Timedelta(days=day)).to_pydatetime(),
        low=low, high=high, close=close,
    )


def _event(event_type: str, index: int, candle, volume_confirmed: bool | None = True) -> WyckoffEvent:
    # Defaults to True so pre-existing tests (about entry/SL/TP formulas,
    # lifecycle, idempotency -- not about Volume Profile gating) keep
    # creating scenarios as before; tests exercising the VP gate itself pass
    # False/None explicitly.
    return WyckoffEvent(
        type=event_type, index=index, ts=candle.bucket_start, price=candle.close, volume_confirmed=volume_confirmed
    )


def _select_scenario():
    from sqlmodel import select

    return select(TradeScenario)


def _fake_strategy_module(phase: str = PHASE_RANGING):
    # Default phase is Ranging so pre-existing entry/SL/TP/lifecycle tests --
    # which don't care about the phase gate -- keep creating scenarios as
    # before. Tests that specifically exercise the gate pass a trending phase.
    return types.SimpleNamespace(analyze=lambda *a, **k: types.SimpleNamespace(phase=phase))


def _sync(session, ticker, candles, events, language="vi", phase=PHASE_RANGING, daily_trend=None):
    # api_key="" -> is_available() is False -> explanation always falls back
    # to the deterministic template, so these tests never make a real AI call.
    provider_cfg = ProviderConfig(provider=PROVIDER_ANTHROPIC, model="claude-sonnet-4-5", api_key="", language=language)
    trade_scenario.sync_scenarios(
        session, ticker, Timeframe.DAILY, STRATEGY, candles, events, BULLISH_EVENTS, BEARISH_EVENTS, LEVELS,
        provider_cfg, _fake_strategy_module(phase), None, daily_trend, RANGING_PHASES,
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


def test_no_supply_never_creates_a_scenario(session):
    # NoDemand/NoSupply are continuation signals that fire inside an existing
    # trend, not a range breakout -- there's no coherent prior range to
    # measure a move from, and they're the two weakest event types by win
    # rate (signal_outcomes stats). They're excluded from scenario creation
    # entirely; signal_outcomes still records them separately.
    candles = [_candle(i, low=99.0, high=101.0, close=100.0) for i in range(20)]
    candles.append(_candle(20, low=99.0, high=105.0, close=105.0))  # NoSupply event bar
    event = _event(NO_SUPPLY, 20, candles[20])

    _sync(session, "FPT", candles, [event])

    assert session.exec(_select_scenario()).first() is None


def test_no_demand_never_creates_a_scenario(session):
    candles = [_candle(i, low=90.0, high=110.0, close=100.0) for i in range(6)]
    candles[5] = _candle(5, low=95.0, high=101.0, close=100.0)
    event = _event(NO_DEMAND, 5, candles[5])

    _sync(session, "FPT", candles, [event])

    assert session.exec(_select_scenario()).first() is None


def test_scenario_blocked_when_event_conflicts_with_daily_trend(session):
    # mtf_alignment used to be informational only -- a bullish Spring against
    # a bearish daily trend still spawned a trade plan. It's a hard gate now.
    candles = [_candle(i, low=90.0, high=110.0, close=100.0) for i in range(6)]
    candles[5] = _candle(5, low=95.0, high=101.0, close=100.0)
    event = _event(SPRING, 5, candles[5])  # bullish

    _sync(session, "FPT", candles, [event], daily_trend="bearish")

    assert session.exec(_select_scenario()).first() is None


def test_scenario_created_when_event_aligns_with_daily_trend(session):
    candles = [_candle(i, low=90.0, high=110.0, close=100.0) for i in range(6)]
    candles[5] = _candle(5, low=95.0, high=101.0, close=100.0)
    event = _event(SPRING, 5, candles[5])  # bullish

    _sync(session, "FPT", candles, [event], daily_trend="bullish")

    assert session.exec(_select_scenario()).one() is not None


def test_scenario_not_gated_by_daily_trend_when_unknown(session):
    # daily_trend=None (the daily timeframe itself, or no daily analysis yet)
    # must not block anything -- this is the default _sync already exercises
    # in every other test in this file, asserted explicitly here.
    candles = [_candle(i, low=90.0, high=110.0, close=100.0) for i in range(6)]
    candles[5] = _candle(5, low=95.0, high=101.0, close=100.0)
    event = _event(SPRING, 5, candles[5])

    _sync(session, "FPT", candles, [event], daily_trend=None)

    assert session.exec(_select_scenario()).one() is not None


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


def test_atr_returns_none_when_history_shorter_than_period_plus_one(session):
    candles = [_candle(i, low=95.0, high=105.0, close=100.0) for i in range(trade_scenario.ATR_PERIOD)]
    assert trade_scenario._atr(candles) is None


def test_atr_averages_true_range_over_the_period(session):
    period = trade_scenario.ATR_PERIOD
    candles = [_candle(i, low=95.0, high=105.0, close=100.0) for i in range(period + 1)]
    # Every bar: high-low=10, |high-prev_close|=5, |low-prev_close|=5 -> TR=10.
    assert trade_scenario._atr(candles) == pytest.approx(10.0)


def test_compute_max_bars_clamps_to_max_when_tp_distance_dwarfs_atr(session):
    period = trade_scenario.ATR_PERIOD
    candles = [_candle(i, low=95.0, high=105.0, close=100.0) for i in range(period + 1)]  # ATR = 10
    assert trade_scenario._compute_max_bars(candles, tp_distance=1000.0) == trade_scenario.MAX_MAX_BARS


def test_compute_max_bars_clamps_to_min_when_tp_distance_is_tiny(session):
    period = trade_scenario.ATR_PERIOD
    candles = [_candle(i, low=95.0, high=105.0, close=100.0) for i in range(period + 1)]  # ATR = 10
    assert trade_scenario._compute_max_bars(candles, tp_distance=1.0) == trade_scenario.MIN_MAX_BARS


def test_compute_max_bars_falls_back_to_default_when_atr_is_zero(session):
    period = trade_scenario.ATR_PERIOD
    candles = [_candle(i, low=100.0, high=100.0, close=100.0) for i in range(period + 1)]  # zero true range
    assert trade_scenario._compute_max_bars(candles, tp_distance=20.0) == trade_scenario.DEFAULT_MAX_BARS


def test_compute_max_bars_falls_back_to_default_when_history_too_short(session):
    candles = [_candle(i, low=95.0, high=105.0, close=100.0) for i in range(5)]
    assert trade_scenario._compute_max_bars(candles, tp_distance=20.0) == trade_scenario.DEFAULT_MAX_BARS


def test_scenario_max_bars_is_atr_driven_when_enough_pre_event_history(session):
    # 16 flat bars before the event (ATR window needs 15) + 1 event bar. ATR
    # over the flat 90/110/100 bars is 20 (high-low=20 each); the bullish
    # event's range height is also 20 (support/resistance 90/110 from the
    # pre-event window) -> tp_distance=20 -> round(20/20)=1, clamped up to
    # MIN_MAX_BARS=5.
    candles = [_candle(i, low=90.0, high=110.0, close=100.0) for i in range(16)]
    candles[15] = _candle(15, low=95.0, high=101.0, close=100.0)
    event = _event(SPRING, 15, candles[15])

    _sync(session, "FPT", candles, [event])

    row = session.exec(_select_scenario()).one()
    assert row.max_bars == trade_scenario.MIN_MAX_BARS


def test_no_scenario_when_phase_before_event_was_already_trending(session):
    # A breakout event's own bar routinely flips the phase to Markup/Markdown
    # in the SAME analysis run -- gating on that post-event phase would pass
    # trivially every time. Gating on the phase as of just before the event
    # (what strategy_module.analyze(candles[:event.index], ...) returns)
    # correctly skips an event that fired once already trending, since there's
    # no real range left to measure a breakout against.
    candles = [_candle(i, low=90.0, high=110.0, close=100.0) for i in range(6)]
    candles[5] = _candle(5, low=95.0, high=101.0, close=100.0)
    event = _event(SPRING, 5, candles[5])

    _sync(session, "FPT", candles, [event], phase="Markup")

    assert session.exec(_select_scenario()).all() == []


def test_no_scenario_when_volume_profile_does_not_confirm_a_gated_event_type(session):
    # SOS/SOW/Spring/Upthrust are the only event types Volume Profile has a
    # confirmation rule for -- explicitly unconfirmed (volume_confirmed=False)
    # must block scenario creation now that VP is part of the entry gate.
    candles = [_candle(i, low=90.0, high=110.0, close=100.0) for i in range(6)]
    candles[5] = _candle(5, low=95.0, high=101.0, close=100.0)
    event = _event(SPRING, 5, candles[5], volume_confirmed=False)

    _sync(session, "FPT", candles, [event])

    assert session.exec(_select_scenario()).all() == []


def test_no_scenario_when_volume_profile_confirmation_was_never_evaluated(session):
    # volume_confirmed=None means "not enough history for a profile yet", not
    # "confirmed" -- treating None as a free pass would let a gated event
    # type through before VP could ever weigh in. Must block the same as an
    # explicit False.
    candles = [_candle(i, low=90.0, high=110.0, close=100.0) for i in range(6)]
    candles[5] = _candle(5, low=95.0, high=101.0, close=100.0)
    event = _event(SPRING, 5, candles[5], volume_confirmed=None)

    _sync(session, "FPT", candles, [event])

    assert session.exec(_select_scenario()).all() == []


def test_scenario_created_when_volume_profile_confirms_a_gated_event_type(session):
    candles = [_candle(i, low=90.0, high=110.0, close=100.0) for i in range(6)]
    candles[5] = _candle(5, low=95.0, high=101.0, close=100.0)
    event = _event(SPRING, 5, candles[5], volume_confirmed=True)

    _sync(session, "FPT", candles, [event])

    assert session.exec(_select_scenario()).one() is not None


def test_ungated_event_type_ignores_missing_volume_confirmation(session):
    # Climaxes/LPS/LPSY have no VP confirmation rule at all (see
    # _VP_GATED_EVENT_TYPES) -- their volume_confirmed is always None in
    # production, and that must NOT block them the way it blocks a gated
    # type. (NoDemand/NoSupply are also ungated here, but they're excluded
    # from scenario creation entirely -- see test_no_supply_never_creates_a_scenario.)
    candles = [_candle(i, low=90.0, high=110.0, close=100.0) for i in range(6)]
    candles[5] = _candle(5, low=95.0, high=101.0, close=100.0)
    event = _event(SELLING_CLIMAX, 5, candles[5], volume_confirmed=None)

    _sync(session, "FPT", candles, [event])

    assert session.exec(_select_scenario()).one() is not None


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


# --- list_scenarios / get_scenario_stats: Trade History page ---

def _make_scenario(
    session, *, ticker="FPT", timeframe=Timeframe.DAILY, strategy=STRATEGY, event_type="SOS",
    day=0, is_bullish=True, entry=100.0, stop_loss=95.0, take_profit=110.0, status="active",
    exit_price=None,
):
    row = TradeScenario(
        ticker=ticker, timeframe=timeframe, strategy=strategy, event_type=event_type,
        event_ts=pd.Timestamp("2025-01-01") + pd.Timedelta(days=day), is_bullish=is_bullish,
        entry=entry, stop_loss=stop_loss, take_profit=take_profit, max_bars=10, status=status,
        exit_price=exit_price,
    )
    session.add(row)
    session.commit()
    return row


def test_list_scenarios_orders_most_recent_event_first(session):
    _make_scenario(session, day=0, event_type="SOS")
    _make_scenario(session, day=5, event_type="Spring", ticker="HPG")
    _make_scenario(session, day=2, event_type="SOW", ticker="ACB")

    items, total = trade_scenario.list_scenarios(session, page=1, page_size=50)

    assert total == 3
    assert [i.ticker for i in items] == ["HPG", "ACB", "FPT"]


def test_list_scenarios_paginates(session):
    for i in range(5):
        _make_scenario(session, day=i, ticker=f"T{i}")

    page1, total = trade_scenario.list_scenarios(session, page=1, page_size=2)
    page2, _ = trade_scenario.list_scenarios(session, page=2, page_size=2)

    assert total == 5
    assert len(page1) == 2
    assert len(page2) == 2
    assert {i.ticker for i in page1} != {i.ticker for i in page2}


def test_list_scenarios_filters_by_ticker_status_strategy(session):
    _make_scenario(session, ticker="FPT", status="active", strategy="wyckoff")
    _make_scenario(session, ticker="FPT", status="hit_tp", strategy="wyckoff", day=1)
    _make_scenario(session, ticker="HPG", status="hit_tp", strategy="smc", day=2)

    by_ticker, _ = trade_scenario.list_scenarios(session, page=1, page_size=50, ticker="fpt")
    assert {i.ticker for i in by_ticker} == {"FPT"}

    by_status, _ = trade_scenario.list_scenarios(session, page=1, page_size=50, status="hit_tp")
    assert {i.ticker for i in by_status} == {"FPT", "HPG"}

    by_strategy, _ = trade_scenario.list_scenarios(session, page=1, page_size=50, strategy="smc")
    assert {i.ticker for i in by_strategy} == {"HPG"}


def test_scenario_stats_win_rate_and_pnl_bullish(session):
    # Bullish hit_tp: entry 100 -> tp 110 -> +10% win.
    _make_scenario(session, is_bullish=True, entry=100.0, take_profit=110.0, stop_loss=95.0, status="hit_tp")
    # Bullish hit_sl: entry 100 -> sl 95 -> -5% loss.
    _make_scenario(session, is_bullish=True, entry=100.0, take_profit=110.0, stop_loss=95.0, status="hit_sl", day=1)

    stats = trade_scenario.get_scenario_stats(session)

    assert stats["decided_count"] == 2
    assert stats["win_count"] == 1
    assert stats["loss_count"] == 1
    assert stats["win_rate"] == pytest.approx(0.5)
    assert stats["avg_pnl_pct"] == pytest.approx((0.10 + (-0.05)) / 2)


def test_scenario_stats_pnl_sign_for_bearish(session):
    # Bearish hit_tp: entry 100 -> tp 90 (below entry) -> a WIN, +10%.
    _make_scenario(session, is_bullish=False, entry=100.0, take_profit=90.0, stop_loss=105.0, status="hit_tp")
    # Bearish hit_sl: entry 100 -> sl 105 (above entry) -> a LOSS, -5%.
    _make_scenario(session, is_bullish=False, entry=100.0, take_profit=90.0, stop_loss=105.0, status="hit_sl", day=1)

    stats = trade_scenario.get_scenario_stats(session)

    assert stats["win_count"] == 1
    assert stats["loss_count"] == 1
    assert stats["avg_pnl_pct"] == pytest.approx((0.10 + (-0.05)) / 2)


def test_scenario_stats_excludes_expired_and_active_from_decided(session):
    _make_scenario(session, status="hit_tp")
    _make_scenario(session, status="expired", day=1)
    _make_scenario(session, status="active", day=2)

    stats = trade_scenario.get_scenario_stats(session)

    assert stats["total_count"] == 3  # all statuses counted here
    assert stats["decided_count"] == 1  # only the hit_tp row


def test_scenario_stats_returns_none_win_rate_and_pnl_when_no_decided_scenarios(session):
    _make_scenario(session, status="active")

    stats = trade_scenario.get_scenario_stats(session)

    assert stats["decided_count"] == 0
    assert stats["win_rate"] is None
    assert stats["avg_pnl_pct"] is None


def test_scenario_stats_respects_ticker_and_strategy_filters(session):
    _make_scenario(session, ticker="FPT", strategy="wyckoff", status="hit_tp")
    _make_scenario(session, ticker="HPG", strategy="smc", status="hit_sl", day=1)

    fpt_stats = trade_scenario.get_scenario_stats(session, ticker="FPT")
    assert fpt_stats["decided_count"] == 1
    assert fpt_stats["win_count"] == 1

    smc_stats = trade_scenario.get_scenario_stats(session, strategy="smc")
    assert smc_stats["decided_count"] == 1
    assert smc_stats["loss_count"] == 1


# --- M4: exit_price capture, portfolio caps, R-multiple/expectancy/$ P&L ---

def test_exit_price_set_on_hit_sl(session):
    candles = [_candle(i, low=90.0, high=110.0, close=100.0) for i in range(6)]
    candles[5] = _candle(5, low=95.0, high=101.0, close=100.0)
    event = _event(SPRING, 5, candles[5])
    _sync(session, "FPT", candles, [event])
    stop_loss = session.exec(_select_scenario()).one().stop_loss

    candles.append(_candle(6, low=stop_loss - 1, high=stop_loss - 1, close=stop_loss - 1))
    _sync(session, "FPT", candles, [event])

    row = session.exec(_select_scenario()).one()
    assert row.status == "hit_sl"
    assert row.exit_price == pytest.approx(stop_loss)


def test_exit_price_set_on_hit_tp(session):
    candles = [_candle(i, low=90.0, high=110.0, close=100.0) for i in range(6)]
    candles[5] = _candle(5, low=95.0, high=101.0, close=100.0)
    event = _event(SPRING, 5, candles[5])
    _sync(session, "FPT", candles, [event])
    take_profit = session.exec(_select_scenario()).one().take_profit

    candles.append(_candle(6, low=take_profit, high=take_profit + 1, close=take_profit))
    _sync(session, "FPT", candles, [event])

    row = session.exec(_select_scenario()).one()
    assert row.status == "hit_tp"
    assert row.exit_price == pytest.approx(take_profit)


def test_exit_price_set_on_expiry_to_last_close(session):
    candles = [_candle(i, low=90.0, high=110.0, close=100.0) for i in range(6)]
    candles[5] = _candle(5, low=95.0, high=101.0, close=100.0)
    event = _event(SPRING, 5, candles[5])
    _sync(session, "FPT", candles, [event])

    for i in range(6, 6 + trade_scenario.DEFAULT_MAX_BARS):
        candles.append(_candle(i, low=99.0, high=101.0, close=100.0))
    _sync(session, "FPT", candles, [event])

    row = session.exec(_select_scenario()).one()
    assert row.status == "expired"
    assert row.exit_price == pytest.approx(candles[-1].close)


def test_portfolio_cap_blocks_new_scenario_when_at_global_limit(session):
    settings_service.update(session, {"max_concurrent_scenarios": "1"})
    _make_scenario(session, ticker="HPG", status="active")  # already at the cap of 1

    candles = [_candle(i, low=90.0, high=110.0, close=100.0) for i in range(6)]
    candles[5] = _candle(5, low=95.0, high=101.0, close=100.0)
    event = _event(SPRING, 5, candles[5])
    _sync(session, "FPT", candles, [event])

    assert session.exec(_select_scenario().where(TradeScenario.ticker == "FPT")).first() is None


def test_portfolio_cap_crypto_sub_limit_blocks_only_crypto(session):
    session.add(Symbol(ticker="BITCOIN", asset_class=AssetClass.CRYPTO))
    session.add(Symbol(ticker="ETHEREUM", asset_class=AssetClass.CRYPTO))
    session.add(Symbol(ticker="FPT", asset_class=AssetClass.STOCK))
    session.commit()
    settings_service.update(session, {"max_concurrent_scenarios_crypto": "1"})
    _make_scenario(session, ticker="ETHEREUM", status="active")  # crypto already at its sub-cap

    candles = [_candle(i, low=90.0, high=110.0, close=100.0) for i in range(6)]
    candles[5] = _candle(5, low=95.0, high=101.0, close=100.0)
    event = _event(SPRING, 5, candles[5])

    _sync(session, "BITCOIN", candles, [event])
    assert session.exec(_select_scenario().where(TradeScenario.ticker == "BITCOIN")).first() is None

    _sync(session, "FPT", candles, [event])  # stock isn't subject to the crypto sub-cap
    assert session.exec(_select_scenario().where(TradeScenario.ticker == "FPT")).first() is not None


def test_scenario_stats_expectancy_r_and_pnl_amount(session):
    settings_service.update(session, {"notional_capital": "100000", "risk_pct_per_trade": "1.0"})
    # Bullish, risk distance 5 (entry 100 -> stop 95). Hits TP at 110 ->
    # raw R = (110-100)/5 = +2.0R. Zero slippage configured for stock.
    settings_service.update(session, {"slippage_pct_stock": "0.0", "slippage_pct_crypto": "0.0"})
    _make_scenario(
        session, is_bullish=True, entry=100.0, stop_loss=95.0, take_profit=110.0,
        status="hit_tp", exit_price=110.0,
    )
    # Bullish, same risk distance, hits SL at 95 -> raw R = (95-100)/5 = -1.0R.
    _make_scenario(
        session, is_bullish=True, entry=100.0, stop_loss=95.0, take_profit=110.0,
        status="hit_sl", exit_price=95.0, day=1,
    )

    stats = trade_scenario.get_scenario_stats(session)

    assert stats["pnl_sample_count"] == 2
    assert stats["expectancy_r"] == pytest.approx((2.0 + (-1.0)) / 2)
    risk_amount = 100000 * 1.0 / 100  # 1000
    assert stats["risk_amount_per_trade"] == pytest.approx(risk_amount)
    assert stats["total_pnl_amount"] == pytest.approx(risk_amount * (2.0 + (-1.0)))


def test_scenario_stats_includes_expired_in_expectancy_but_not_win_rate(session):
    # Expired-but-favorable: drifted to 105 without ever touching TP(110)/SL(95).
    # Contributes positive R to expectancy, but win_count/win_rate stay
    # defined only over hit_tp/hit_sl (unchanged, narrower meaning).
    settings_service.update(session, {"slippage_pct_stock": "0.0"})
    _make_scenario(
        session, is_bullish=True, entry=100.0, stop_loss=95.0, take_profit=110.0,
        status="expired", exit_price=105.0,
    )

    stats = trade_scenario.get_scenario_stats(session)

    assert stats["decided_count"] == 0  # no hit_tp/hit_sl rows
    assert stats["win_rate"] is None
    assert stats["pnl_sample_count"] == 1
    assert stats["expectancy_r"] == pytest.approx((105.0 - 100.0) / 5.0)  # +1.0R


def test_scenario_stats_slippage_worsens_bullish_exit(session):
    settings_service.update(session, {"slippage_pct_stock": "1.0"})  # 1% of entry
    _make_scenario(
        session, ticker="FPT", is_bullish=True, entry=100.0, stop_loss=95.0, take_profit=110.0,
        status="hit_tp", exit_price=110.0,
    )

    stats = trade_scenario.get_scenario_stats(session)

    # Slippage worsens a bullish exit downward: adjusted_exit = 110 - 1 (1% of
    # entry 100) = 109 -> R = (109-100)/5 = 1.8, less than the naive 2.0.
    assert stats["expectancy_r"] == pytest.approx(1.8)


# --- M5: filter by asset_class (VN30 stocks vs crypto) ---

def test_list_scenarios_filters_by_asset_class(session):
    session.add(Symbol(ticker="FPT", asset_class=AssetClass.STOCK))
    session.add(Symbol(ticker="BITCOIN", asset_class=AssetClass.CRYPTO))
    session.commit()
    _make_scenario(session, ticker="FPT")
    _make_scenario(session, ticker="BITCOIN", day=1)

    stock_items, stock_total = trade_scenario.list_scenarios(
        session, page=1, page_size=50, asset_class=AssetClass.STOCK
    )
    crypto_items, crypto_total = trade_scenario.list_scenarios(
        session, page=1, page_size=50, asset_class=AssetClass.CRYPTO
    )

    assert stock_total == 1
    assert {i.ticker for i in stock_items} == {"FPT"}
    assert crypto_total == 1
    assert {i.ticker for i in crypto_items} == {"BITCOIN"}


def test_list_scenarios_unfiltered_ignores_asset_class(session):
    # A scenario for a ticker with no Symbol row at all (e.g. seeded directly
    # in a test, or a ticker not yet promoted) must still show up when no
    # asset_class filter is applied -- the join only kicks in when requested.
    _make_scenario(session, ticker="UNKNOWN")

    items, total = trade_scenario.list_scenarios(session, page=1, page_size=50)
    assert total == 1


def test_scenario_stats_filters_by_asset_class(session):
    session.add(Symbol(ticker="FPT", asset_class=AssetClass.STOCK))
    session.add(Symbol(ticker="BITCOIN", asset_class=AssetClass.CRYPTO))
    session.commit()
    _make_scenario(session, ticker="FPT", status="hit_tp")
    _make_scenario(session, ticker="BITCOIN", status="hit_sl", day=1)

    stock_stats = trade_scenario.get_scenario_stats(session, asset_class=AssetClass.STOCK)
    crypto_stats = trade_scenario.get_scenario_stats(session, asset_class=AssetClass.CRYPTO)

    assert stock_stats["decided_count"] == 1
    assert stock_stats["win_count"] == 1
    assert crypto_stats["decided_count"] == 1
    assert crypto_stats["loss_count"] == 1
