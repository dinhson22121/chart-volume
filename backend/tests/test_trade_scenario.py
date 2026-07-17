import types

import pandas as pd
import pytest

from app.ai.narrative import PROVIDER_ANTHROPIC, ProviderConfig
from app.models import TradeScenario, Timeframe
from app.services import trade_scenario
from app.wyckoff import BEARISH_EVENTS, BULLISH_EVENTS, RANGING_PHASES, Levels
from app.wyckoff.events import SOW, SPRING, WyckoffEvent
from app.wyckoff.phase import PHASE_RANGING

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


def _fake_strategy_module(phase: str = PHASE_RANGING):
    # Default phase is Ranging so pre-existing entry/SL/TP/lifecycle tests --
    # which don't care about the phase gate -- keep creating scenarios as
    # before. Tests that specifically exercise the gate pass a trending phase.
    return types.SimpleNamespace(analyze=lambda *a, **k: types.SimpleNamespace(phase=phase))


def _sync(session, ticker, candles, events, language="vi", phase=PHASE_RANGING):
    # api_key="" -> is_available() is False -> explanation always falls back
    # to the deterministic template, so these tests never make a real AI call.
    provider_cfg = ProviderConfig(provider=PROVIDER_ANTHROPIC, model="claude-sonnet-4-5", api_key="", language=language)
    trade_scenario.sync_scenarios(
        session, ticker, Timeframe.DAILY, STRATEGY, candles, events, BULLISH_EVENTS, BEARISH_EVENTS, LEVELS,
        provider_cfg, _fake_strategy_module(phase), None, None, RANGING_PHASES,
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
):
    row = TradeScenario(
        ticker=ticker, timeframe=timeframe, strategy=strategy, event_type=event_type,
        event_ts=pd.Timestamp("2025-01-01") + pd.Timedelta(days=day), is_bullish=is_bullish,
        entry=entry, stop_loss=stop_loss, take_profit=take_profit, max_bars=10, status=status,
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
