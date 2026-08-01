import types

import pandas as pd
import pytest

from app.ai.narrative import PROVIDER_ANTHROPIC, ProviderConfig
from app.models import AssetClass, Candle, Exchange, Symbol, TradeScenario, Timeframe
from app.services import settings_service, trade_scenario
from app.wyckoff import BULLISH_EVENTS, RANGING_PHASES, Levels
from app.wyckoff.events import NO_DEMAND, NO_SUPPLY, SELLING_CLIMAX, SOW, SPRING, WyckoffEvent
from app.wyckoff.phase import PHASE_RANGING

STRATEGY = "wyckoff"
LEVELS = Levels(support=90.0, resistance=110.0)  # range height = 20


def _candle(day: int, *, low: float, high: float, close: float, open: float | None = None):
    # open defaults to close (a flat/no-gap continuation bar) so the vast
    # majority of tests -- which don't care about entry timing -- don't need
    # to specify it; tests exercising entry-price behavior pass it explicitly.
    t0 = pd.Timestamp("2025-01-01")
    return types.SimpleNamespace(
        bucket_start=(t0 + pd.Timedelta(days=day)).to_pydatetime(),
        open=open if open is not None else close, low=low, high=high, close=close,
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


def _fake_strategy_module(phase: str = PHASE_RANGING, continuation_events: set[str] = frozenset()):
    # Default phase is Ranging so pre-existing entry/SL/TP/lifecycle tests --
    # which don't care about the phase gate -- keep creating scenarios as
    # before. Tests that specifically exercise the gate pass a trending phase.
    # continuation_events mirrors app.sonicr's TREND_CONTINUATION_EVENTS --
    # empty by default so this stub matches every OTHER strategy (getattr
    # with a frozenset() fallback in _build_scenario_candidate).
    return types.SimpleNamespace(
        analyze=lambda *a, **k: types.SimpleNamespace(phase=phase),
        TREND_CONTINUATION_EVENTS=continuation_events,
    )


def _sync(session, ticker, candles, events, language="vi", phase=PHASE_RANGING, daily_trend=None):
    # api_key="" -> is_available() is False -> explanation always falls back
    # to the deterministic template, so these tests never make a real AI call.
    provider_cfg = ProviderConfig(provider=PROVIDER_ANTHROPIC, model="claude-sonnet-4-5", api_key="", language=language)
    trade_scenario.sync_scenarios(
        session, ticker, Timeframe.DAILY, STRATEGY, candles, events, BULLISH_EVENTS, LEVELS,
        provider_cfg, _fake_strategy_module(phase), None, daily_trend, RANGING_PHASES,
    )


def test_creates_bullish_scenario_with_entry_sl_tp_from_formulas(session):
    candles = [_candle(i, low=90.0, high=110.0, close=100.0) for i in range(6)]
    candles[5] = _candle(5, low=95.0, high=101.0, close=100.0)
    candles.append(_candle(6, open=103.0, low=102.0, high=105.0, close=104.0))  # next bar, entry fill
    event = _event(SPRING, 5, candles[5])

    _sync(session, "FPT", candles, [event])

    row = session.exec(_select_scenario()).one()
    assert row.is_bullish is True
    assert row.entry == 103.0  # next bar's open, not the event bar's own close
    assert row.stop_loss == pytest.approx(95.0 * (1 - trade_scenario.SL_BUFFER_PCT))
    assert row.take_profit == pytest.approx(103.0 + 20.0)  # entry + range height
    assert row.max_bars == trade_scenario.DEFAULT_MAX_BARS
    assert row.status == "active"
    assert row.explanation  # template fallback since no AI key is configured
    assert SPRING in row.explanation


def test_no_scenario_when_event_is_on_the_latest_bar_with_no_next_bar_yet(session):
    # A live trader can only confirm the signal once the event bar has
    # closed -- the earliest realistic fill is the NEXT bar's open. If that
    # bar doesn't exist yet (the event just fired on the newest candle),
    # there's nothing to enter at; the scenario must wait for a later run.
    candles = [_candle(i, low=90.0, high=110.0, close=100.0) for i in range(6)]
    candles[5] = _candle(5, low=95.0, high=101.0, close=100.0)
    event = _event(SPRING, 5, candles[5])

    _sync(session, "FPT", candles, [event])

    assert session.exec(_select_scenario()).first() is None


def test_scenario_created_retroactively_once_the_next_bar_arrives(session):
    candles = [_candle(i, low=90.0, high=110.0, close=100.0) for i in range(6)]
    candles[5] = _candle(5, low=95.0, high=101.0, close=100.0)
    event = _event(SPRING, 5, candles[5])
    _sync(session, "FPT", candles, [event])
    assert session.exec(_select_scenario()).first() is None  # not yet -- no next bar

    candles.append(_candle(6, open=103.0, low=102.0, high=105.0, close=104.0))
    _sync(session, "FPT", candles, [event])

    row = session.exec(_select_scenario()).one()
    assert row.entry == 103.0


def test_explanation_uses_template_when_provider_unavailable(session):
    candles = [_candle(i, low=90.0, high=110.0, close=100.0) for i in range(6)]
    candles[5] = _candle(5, low=95.0, high=101.0, close=100.0)
    candles.append(_candle(6, low=90.0, high=110.0, close=100.0))
    event = _event(SPRING, 5, candles[5])

    _sync(session, "FPT", candles, [event])

    row = session.exec(_select_scenario()).one()
    assert "Tín hiệu" in row.explanation  # Vietnamese template wording


def test_explanation_uses_ai_when_provider_available(session, mocker):
    mocker.patch.object(trade_scenario.narrative_mod, "is_available", return_value=True)
    mocker.patch.object(trade_scenario.narrative_mod, "call_provider_raw", return_value="AI-written explanation.")
    candles = [_candle(i, low=90.0, high=110.0, close=100.0) for i in range(6)]
    candles[5] = _candle(5, low=95.0, high=101.0, close=100.0)
    candles.append(_candle(6, low=90.0, high=110.0, close=100.0))
    event = _event(SPRING, 5, candles[5])

    _sync(session, "FPT", candles, [event])

    row = session.exec(_select_scenario()).one()
    assert row.explanation == "AI-written explanation."


def test_explanation_falls_back_to_template_on_ai_failure(session, mocker):
    mocker.patch.object(trade_scenario.narrative_mod, "is_available", return_value=True)
    mocker.patch.object(trade_scenario.narrative_mod, "call_provider_raw", side_effect=RuntimeError("provider down"))
    candles = [_candle(i, low=90.0, high=110.0, close=100.0) for i in range(6)]
    candles[5] = _candle(5, low=95.0, high=101.0, close=100.0)
    candles.append(_candle(6, low=90.0, high=110.0, close=100.0))
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
    candles.append(_candle(6, open=150.0, low=149.0, high=151.0, close=150.0))  # entry fill, same level
    event = _event(SPRING, 5, candles[5])
    # BULLISH_EVENTS classifies SPRING as bullish regardless of the actual
    # price move here -- only the range-height math is under test.

    _sync(session, "FPT", candles, [event])

    row = session.exec(_select_scenario()).one()
    # Pre-event window (bars 0-4) is still 90-110 -> height 20, NOT
    # (150 - the event bar's own low) or anything derived from bar 5.
    assert row.take_profit == pytest.approx(150.0 + 20.0)


def test_range_height_is_capped_so_take_profit_never_goes_absurdly_large(session):
    # Regression: a single flash-crash/spike bar inside the 20-bar lookback
    # (real on volatile, low-liquidity crypto) can make max(high)-min(low)
    # many multiples of the current price. One bar in the pre-event window
    # crashes from 300 to 10 and mostly recovers, so the raw window height
    # would be ~290 against an entry of 100 -- capped to 50% of entry
    # (height 50) instead.
    candles = [_candle(i, low=95.0, high=105.0, close=100.0) for i in range(7)]
    candles[2] = _candle(2, low=10.0, high=300.0, close=100.0)  # flash crash + spike bar
    candles[6] = _candle(6, low=95.0, high=101.0, close=100.0)  # bullish event bar
    candles.append(_candle(7, low=95.0, high=105.0, close=100.0))  # entry fill
    event = _event(SPRING, 6, candles[6])

    _sync(session, "FPT", candles, [event])

    row = session.exec(_select_scenario()).one()
    assert row.take_profit == pytest.approx(row.entry + row.entry * trade_scenario.MAX_RANGE_HEIGHT_PCT)


def test_bearish_event_never_creates_a_scenario(session):
    # Spot-only: the app never models short-selling (retail can't short VN
    # equities; crypto here is spot buy/sell, not futures) -- a bearish event
    # is excluded from scenario creation entirely, the same way NoDemand/
    # NoSupply are (see test_no_supply_never_creates_a_scenario). It's still
    # recorded by signal_outcomes for stats.
    candles = [_candle(i, low=90.0, high=110.0, close=100.0) for i in range(6)]
    candles[5] = _candle(5, low=99.0, high=105.0, close=100.0)
    candles.append(_candle(6, open=100.0, low=99.0, high=101.0, close=100.0))  # entry fill, same level
    event = _event(SOW, 5, candles[5])

    _sync(session, "FPT", candles, [event])

    assert session.exec(_select_scenario()).first() is None


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


@pytest.mark.parametrize("raw_signal_type", ["DragonCrossUp", "SonicCrossUp"])
def test_sonicr_raw_signals_never_create_a_scenario(session, raw_signal_type):
    # scripts/backtest_sonicr.py (VN30, then a 100-ticker HOSE/HNX sample)
    # found neither DragonCrossUp nor SonicCrossUp has a statistically
    # significant edge on its own -- bootstrap CI crosses zero both times, no
    # consistent chronological pattern. Only SonicEntryLong (the fully
    # confirmed entry) is left as a real trade-scenario source; these two
    # stay recorded via signal_outcomes for reference, same treatment as
    # Wyckoff's NoDemand/NoSupply.
    candles = [_candle(i, low=90.0, high=110.0, close=100.0) for i in range(6)]
    candles[5] = _candle(5, low=95.0, high=101.0, close=100.0)
    event = _event(raw_signal_type, 5, candles[5])
    provider_cfg = ProviderConfig(provider=PROVIDER_ANTHROPIC, model="claude-sonnet-4-5", api_key="", language="vi")

    trade_scenario.sync_scenarios(
        session, "FPT", Timeframe.DAILY, "sonicr", candles, [event], {raw_signal_type}, LEVELS,
        provider_cfg, _fake_strategy_module(), None, None, RANGING_PHASES,
    )

    assert session.exec(_select_scenario()).first() is None


def test_price_limit_caution_set_when_tp_unreachable_within_max_bars(session):
    # Regression for the price-limit-band flag (WP5): a very wide pre-event
    # window (low=10/high=190) drives BOTH the measured-move TP (capped at
    # 50% of entry -> 50) AND the ATR high enough that max_bars clamps down
    # to MIN_MAX_BARS=5. Reaching a 50% move via HOSE's compounding 7%/day
    # band needs ln(1.5)/ln(1.07) ~= 6 sessions -- more than the 5 the
    # scenario gives itself, so the flag must be set.
    session.add(Symbol(ticker="FPT", asset_class=AssetClass.STOCK))
    session.commit()
    candles = [_candle(i, low=10.0, high=190.0, close=100.0) for i in range(20)]
    candles.append(_candle(20, low=95.0, high=101.0, close=100.0))  # event bar
    candles.append(_candle(21, open=100.0, low=99.0, high=101.0, close=100.0))  # entry fill, same level
    event = _event(SPRING, 20, candles[20])

    _sync(session, "FPT", candles, [event])

    row = session.exec(_select_scenario()).one()
    assert row.max_bars == trade_scenario.MIN_MAX_BARS
    assert row.price_limit_caution is True


def test_price_limit_caution_false_when_move_is_reachable(session):
    session.add(Symbol(ticker="FPT", asset_class=AssetClass.STOCK))
    session.commit()
    # Narrow range (height 20 on entry 100 -> 20% TP move) with generous
    # max_bars (ATR-derived, well above MIN) -- 20% is easily reachable
    # within HOSE's 7%/day band over more than a handful of sessions.
    candles = [_candle(i, low=90.0, high=110.0, close=100.0) for i in range(6)]
    candles[5] = _candle(5, low=95.0, high=101.0, close=100.0)
    candles.append(_candle(6, open=100.0, low=99.0, high=101.0, close=100.0))  # entry fill, same level
    event = _event(SPRING, 5, candles[5])

    _sync(session, "FPT", candles, [event])

    row = session.exec(_select_scenario()).one()
    assert row.price_limit_caution is False


def test_price_limit_caution_false_for_crypto(session):
    # No daily price-limit band applies to crypto -- the flag is stock-only.
    session.add(Symbol(ticker="BITCOIN", asset_class=AssetClass.CRYPTO))
    session.commit()
    candles = [_candle(i, low=10.0, high=190.0, close=100.0) for i in range(20)]
    candles.append(_candle(20, low=95.0, high=101.0, close=100.0))
    candles.append(_candle(21, open=100.0, low=99.0, high=101.0, close=100.0))  # entry fill, same level
    event = _event(SPRING, 20, candles[20])

    _sync(session, "BITCOIN", candles, [event])

    row = session.exec(_select_scenario()).one()
    assert row.price_limit_caution is False


def test_price_limit_caution_uses_wider_band_for_hnx_stocks(session):
    # Identical setup to test_price_limit_caution_set_when_tp_unreachable_
    # within_max_bars (50% TP move, max_bars clamped to 5) -- HOSE's 7%/day
    # band can't cover it in 5 sessions (flag True there), but HNX's wider
    # 10%/day band needs only ln(1.5)/ln(1.10) ~= 4.3 sessions, well within 5.
    # Using the flat HOSE-only threshold for an HNX ticker would wrongly flag
    # this as unreachable.
    session.add(Symbol(ticker="SHS", asset_class=AssetClass.STOCK, exchange=Exchange.HNX))
    session.commit()
    candles = [_candle(i, low=10.0, high=190.0, close=100.0) for i in range(20)]
    candles.append(_candle(20, low=95.0, high=101.0, close=100.0))
    candles.append(_candle(21, open=100.0, low=99.0, high=101.0, close=100.0))  # entry fill, same level
    event = _event(SPRING, 20, candles[20])

    _sync(session, "SHS", candles, [event])

    row = session.exec(_select_scenario()).one()
    assert row.max_bars == trade_scenario.MIN_MAX_BARS
    assert row.price_limit_caution is False


def test_price_limit_caution_falls_back_to_hose_band_when_exchange_unset(session):
    # A stock with no exchange recorded yet (pre-dates this field, or added
    # via VN30/watchlist before hose_hnx seeding ever ran) must not silently
    # get the wider HNX band -- HOSE's tighter 7% is the safe default.
    session.add(Symbol(ticker="FPT", asset_class=AssetClass.STOCK, exchange=None))
    session.commit()
    candles = [_candle(i, low=10.0, high=190.0, close=100.0) for i in range(20)]
    candles.append(_candle(20, low=95.0, high=101.0, close=100.0))
    candles.append(_candle(21, open=100.0, low=99.0, high=101.0, close=100.0))  # entry fill, same level
    event = _event(SPRING, 20, candles[20])

    _sync(session, "FPT", candles, [event])

    row = session.exec(_select_scenario()).one()
    assert row.price_limit_caution is True


def test_config_version_stamped_on_new_scenario_and_immutable(session):
    candles = [_candle(i, low=90.0, high=110.0, close=100.0) for i in range(6)]
    candles[5] = _candle(5, low=95.0, high=101.0, close=100.0)
    candles.append(_candle(6, low=90.0, high=110.0, close=100.0))
    event = _event(SPRING, 5, candles[5])
    provider_cfg = ProviderConfig(provider=PROVIDER_ANTHROPIC, model="claude-sonnet-4-5", api_key="", language="vi")

    trade_scenario.sync_scenarios(
        session, "FPT", Timeframe.DAILY, STRATEGY, candles, [event], BULLISH_EVENTS, LEVELS,
        provider_cfg, _fake_strategy_module(), None, None, RANGING_PHASES, config_version="wyckoff:v1",
    )
    row = session.exec(_select_scenario()).one()
    assert row.config_version == "wyckoff:v1"

    # A later run under a DIFFERENT config_version must not rewrite the
    # already-created row's tag -- it's set once at creation, like every
    # other identity/formula field on this row.
    trade_scenario.sync_scenarios(
        session, "FPT", Timeframe.DAILY, STRATEGY, candles, [event], BULLISH_EVENTS, LEVELS,
        provider_cfg, _fake_strategy_module(), None, None, RANGING_PHASES, config_version="wyckoff:v2",
    )
    row_again = session.exec(_select_scenario()).one()
    assert row_again.config_version == "wyckoff:v1"


def test_scenario_blocked_when_event_conflicts_with_daily_trend(session):
    # mtf_alignment used to be informational only -- a bullish Spring against
    # a bearish daily trend still spawned a trade plan. It's a hard gate now.
    # A next bar exists (so the gate under test is what actually blocks
    # this -- not merely "no bar to enter at yet").
    candles = [_candle(i, low=90.0, high=110.0, close=100.0) for i in range(6)]
    candles[5] = _candle(5, low=95.0, high=101.0, close=100.0)
    candles.append(_candle(6, low=90.0, high=110.0, close=100.0))
    event = _event(SPRING, 5, candles[5])  # bullish

    _sync(session, "FPT", candles, [event], daily_trend="bearish")

    assert session.exec(_select_scenario()).first() is None


def test_scenario_created_when_event_aligns_with_daily_trend(session):
    candles = [_candle(i, low=90.0, high=110.0, close=100.0) for i in range(6)]
    candles[5] = _candle(5, low=95.0, high=101.0, close=100.0)
    candles.append(_candle(6, low=90.0, high=110.0, close=100.0))
    event = _event(SPRING, 5, candles[5])  # bullish

    _sync(session, "FPT", candles, [event], daily_trend="bullish")

    assert session.exec(_select_scenario()).one() is not None


def test_scenario_not_gated_by_daily_trend_when_unknown(session):
    # daily_trend=None (the daily timeframe itself, or no daily analysis yet)
    # must not block anything -- this is the default _sync already exercises
    # in every other test in this file, asserted explicitly here.
    candles = [_candle(i, low=90.0, high=110.0, close=100.0) for i in range(6)]
    candles[5] = _candle(5, low=95.0, high=101.0, close=100.0)
    candles.append(_candle(6, low=90.0, high=110.0, close=100.0))
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
    candles.append(_candle(16, open=100.0, low=99.0, high=101.0, close=100.0))  # entry fill, same level
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


def test_trend_continuation_event_skips_the_phase_before_event_gate(session):
    # SonicEntryLong-style continuation entries (see
    # app.sonicr.phase.TREND_CONTINUATION_EVENTS) only fire once a trend is
    # ALREADY established, so the phase just before one is always
    # Uptrend/Downtrend, never Ranging -- the gate above would otherwise
    # reject 100% of them. A strategy module that lists this event type in
    # its own TREND_CONTINUATION_EVENTS must skip the gate for it.
    candles = [_candle(i, low=90.0, high=110.0, close=100.0) for i in range(6)]
    candles[5] = _candle(5, low=95.0, high=101.0, close=100.0)
    candles.append(_candle(6, open=103.0, low=102.0, high=105.0, close=104.0))
    event = _event(SPRING, 5, candles[5])

    provider_cfg = ProviderConfig(provider=PROVIDER_ANTHROPIC, model="claude-sonnet-4-5", api_key="", language="vi")
    trade_scenario.sync_scenarios(
        session, "FPT", Timeframe.DAILY, STRATEGY, candles, [event], BULLISH_EVENTS, LEVELS,
        provider_cfg, _fake_strategy_module(phase="Markup", continuation_events={SPRING}), None, None, RANGING_PHASES,
    )

    assert session.exec(_select_scenario()).one() is not None


# --- Entry-quality filters: POI zone + minimum RR -----------------------
# These are declared BY THE STRATEGY (app.smc.phase) and read off the
# strategy module via getattr, so every test above -- whose stub strategy
# declares neither -- is gated exactly as it was before they existed. That
# insulation is the point: those tests assert on entry/SL/TP formulas and
# lifecycle using minimal mid-range fixtures that SMC's real defaults would
# reject outright. The tests below put the knobs on the stub explicitly.
# LEVELS is support=90/resistance=110, so at a 0.5 threshold a bullish entry
# must be at or below 100 to sit in the "discount" half.

def _filtering_strategy_module(*, poi_pct=0.0, min_rr=0.0):
    module = _fake_strategy_module()
    module.POI_ZONE_THRESHOLD_PCT = poi_pct
    module.POI_ZONE_FILTER_TYPES = frozenset()
    module.MIN_RR_RATIO = min_rr
    return module


def _sync_with_filters(session, candles, events, *, poi_pct=0.0, min_rr=0.0):
    provider_cfg = ProviderConfig(provider=PROVIDER_ANTHROPIC, model="claude-sonnet-4-5", api_key="", language="vi")
    trade_scenario.sync_scenarios(
        session, "FPT", Timeframe.DAILY, STRATEGY, candles, events, BULLISH_EVENTS, LEVELS,
        provider_cfg, _filtering_strategy_module(poi_pct=poi_pct, min_rr=min_rr), None, None, RANGING_PHASES,
    )


def test_a_strategy_declaring_no_filters_is_gated_exactly_as_before(session):
    # Wyckoff/Sonic R declare neither knob -- getattr's neutral defaults must
    # let a mid-range entry through, the behavior that predates these filters.
    candles, events = _poi_fixture(entry_open=108.0)  # deep in the premium half
    _sync(session, "FPT", candles, events)
    assert session.exec(_select_scenario()).one() is not None


def _poi_fixture(entry_open: float):
    """6 flat bars + a Spring, then an entry bar opening at `entry_open` --
    the only thing the POI filter looks at for a bullish setup."""
    candles = [_candle(i, low=90.0, high=110.0, close=100.0) for i in range(6)]
    candles[5] = _candle(5, low=95.0, high=101.0, close=100.0)
    candles.append(_candle(6, open=entry_open, low=entry_open - 1, high=entry_open + 1, close=entry_open))
    return candles, [_event(SPRING, 5, candles[5])]


def test_poi_filter_blocks_a_bullish_entry_in_the_premium_half(session):
    candles, events = _poi_fixture(entry_open=108.0)  # well above the 100 midpoint
    _sync_with_filters(session, candles, events, poi_pct=0.5)
    assert session.exec(_select_scenario()).first() is None


def test_poi_filter_allows_a_bullish_entry_in_the_discount_half(session):
    candles, events = _poi_fixture(entry_open=93.0)  # below the 100 midpoint
    _sync_with_filters(session, candles, events, poi_pct=0.5)
    assert session.exec(_select_scenario()).one() is not None


def test_poi_filter_is_off_at_zero_threshold(session):
    # Same premium-half entry the first test rejects -- passes with the
    # filter disabled, proving the rejection came from the filter itself.
    candles, events = _poi_fixture(entry_open=108.0)
    _sync_with_filters(session, candles, events, poi_pct=0.0)
    assert session.exec(_select_scenario()).one() is not None


def test_poi_filter_passes_a_degenerate_zero_width_range(session):
    # A flat series has no range to be "cheap" or "expensive" within, so the
    # filter must not block every entry on it.
    flat = Levels(support=100.0, resistance=100.0)
    assert trade_scenario._entry_is_in_favorable_zone(100.0, flat, True, 0.5)
    assert trade_scenario._entry_is_in_favorable_zone(100.0, flat, False, 0.5)


def test_min_rr_filter_blocks_a_setup_below_the_ratio(session):
    # Entry 93 with the event bar's low at 95 gives a tiny stop distance...
    # so instead force a WIDE stop: the event bar's low sits far below entry,
    # making reward:risk small against the 20-wide measured move.
    candles = [_candle(i, low=90.0, high=110.0, close=100.0) for i in range(6)]
    candles[5] = _candle(5, low=80.0, high=101.0, close=100.0)  # stop ~80 -> risk ~13 vs reward 20
    candles.append(_candle(6, open=93.0, low=92.0, high=94.0, close=93.0))
    events = [_event(SPRING, 5, candles[5])]

    _sync_with_filters(session, candles, events, min_rr=3.0)
    assert session.exec(_select_scenario()).first() is None


def test_min_rr_filter_allows_a_setup_meeting_the_ratio(session):
    # Tight stop just under the entry -> risk ~1, reward ~20 -> RR far above 3.
    candles = [_candle(i, low=90.0, high=110.0, close=100.0) for i in range(6)]
    candles[5] = _candle(5, low=92.5, high=101.0, close=100.0)
    candles.append(_candle(6, open=93.0, low=92.0, high=94.0, close=93.0))
    events = [_event(SPRING, 5, candles[5])]

    _sync_with_filters(session, candles, events, min_rr=3.0)
    assert session.exec(_select_scenario()).one() is not None


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
    candles.append(_candle(6, low=90.0, high=110.0, close=100.0))
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
    candles.append(_candle(6, low=90.0, high=110.0, close=100.0))
    event = _event(SELLING_CLIMAX, 5, candles[5], volume_confirmed=None)

    _sync(session, "FPT", candles, [event])

    assert session.exec(_select_scenario()).one() is not None


def test_sync_is_idempotent_for_the_same_event(session):
    candles = [_candle(i, low=90.0, high=110.0, close=100.0) for i in range(6)]
    candles[5] = _candle(5, low=95.0, high=101.0, close=100.0)
    candles.append(_candle(6, low=90.0, high=110.0, close=100.0))
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
    # Narrow so neither pierces the first scenario's SL (~94.7) nor TP (120)
    # intrabar -- the wider 90/110 default template would spuriously trip the
    # SL the instant it's checked intrabar, which isn't what this test is
    # about (it's testing the "no new scenario while active" gate, not fill).
    candles[6] = _candle(6, low=97.0, high=103.0, close=100.0)
    candles[7] = _candle(7, low=97.0, high=103.0, close=100.0)
    candles[8] = _candle(8, low=96.0, high=102.0, close=101.0)
    first = _event(SPRING, 5, candles[5])
    second = _event(SPRING, 8, candles[8])

    _sync(session, "FPT", candles, [first])
    _sync(session, "FPT", candles, [second])

    rows = session.exec(_select_scenario()).all()
    assert len(rows) == 1
    assert rows[0].event_ts == candles[5].bucket_start
    assert rows[0].status == "active"


# --- _resolve_outcome: pure hit_sl/hit_tp/expiry decision, shared by live
# tracking (_update_active_scenarios) and app.services.scenario_backtest ---

def test_resolve_outcome_stays_active_with_no_qualifying_bar_yet():
    candles = [_candle(1, low=99.0, high=101.0, close=100.0)]
    outcome = trade_scenario._resolve_outcome(
        event_ts=candles[0].bucket_start, entry=100.0, stop_loss=95.0, max_bars=10,
        is_bullish=True, candles=candles,
    )
    assert outcome.status == "active"
    assert outcome.closed_bar_ts is None
    assert outcome.exit_price is None


def test_resolve_outcome_hit_sl_bullish_uses_intrabar_low():
    event_bar = _candle(0, low=95.0, high=101.0, close=100.0)
    trigger_bar = _candle(1, low=93.0, high=101.0, close=94.5)  # low pierces SL=94.715
    outcome = trade_scenario._resolve_outcome(
        event_ts=event_bar.bucket_start, entry=100.0, stop_loss=94.715, max_bars=10,
        is_bullish=True, candles=[event_bar, trigger_bar],
    )
    assert outcome.status == "hit_sl"
    assert outcome.closed_bar_ts == trigger_bar.bucket_start
    assert outcome.exit_price == pytest.approx(94.715)
    assert outcome.touch_price == pytest.approx(93.0)


def _flat_pre_event_candles(n: int, *, low: float, high: float, close: float):
    """n candles, identical enough (constant close) that _atr's true-range
    calc is trivial to reason about by hand: every bar's TR is exactly
    (high - low), since prevclose == close never creates a gap component."""
    return [_candle(i, low=low, high=high, close=close) for i in range(n)]


def test_resolve_outcome_hit_tp_via_trailing_stop_after_favorable_move():
    # No fixed take-profit anymore (see TRAIL_ATR_MULT) -- "hit_tp" now means
    # the stop trailed to breakeven-or-better and then got hit. Needs real
    # pre-event history for a computable ATR (ATR_PERIOD=14, so >=15 bars),
    # unlike the old fixed-level check which needed none.
    pre_event = _flat_pre_event_candles(15, low=99.0, high=101.0, close=100.0)  # TR=2/bar -> ATR=2.0
    event_bar = pre_event[-1]
    entry, stop_loss = 100.0, 105.0  # bearish: risk_distance=5.0 (2.5x ATR)
    # Bar 1: low=95 -> unrealized = entry-95 = 5 >= risk_distance -> trail
    # activates: trail_level = 95 + 1.5*2 = 98; current_stop = min(105,100,98) = 98.
    bar1 = _candle(15, low=95.0, high=101.0, close=96.0)
    # Bar 2: high=99 pierces the now-98 stop (>= current_stop=98, <= entry=100 -> hit_tp).
    bar2 = _candle(16, low=95.0, high=99.0, close=97.0)
    outcome = trade_scenario._resolve_outcome(
        event_ts=event_bar.bucket_start, entry=entry, stop_loss=stop_loss, max_bars=10,
        is_bullish=False, candles=[*pre_event, bar1, bar2],
    )
    assert outcome.status == "hit_tp"
    assert outcome.closed_bar_ts == bar2.bucket_start
    assert outcome.exit_price == pytest.approx(98.0)


def test_resolve_outcome_stop_check_uses_prior_bars_level_not_this_bars_trail_update():
    # A bar whose own low pierces the CURRENT (pre-this-bar) stop resolves
    # immediately, even though that same bar's high would, if evaluated
    # first, have triggered a favorable-move trail update -- the stop for
    # bar N is fixed before bar N's own range can move it.
    event_bar = _candle(0, low=95.0, high=101.0, close=100.0)
    bar = _candle(1, low=90.0, high=125.0, close=110.0)  # pierces original SL=94.715
    outcome = trade_scenario._resolve_outcome(
        event_ts=event_bar.bucket_start, entry=100.0, stop_loss=94.715, max_bars=10,
        is_bullish=True, candles=[event_bar, bar],
    )
    assert outcome.status == "hit_sl"
    assert outcome.exit_price == pytest.approx(94.715)


def test_resolve_outcome_expires_after_max_bars_to_last_close():
    event_bar = _candle(0, low=95.0, high=101.0, close=100.0)
    flat_bars = [_candle(i, low=99.0, high=101.0, close=100.0) for i in range(1, 4)]
    outcome = trade_scenario._resolve_outcome(
        event_ts=event_bar.bucket_start, entry=100.0, stop_loss=94.715, max_bars=3,
        is_bullish=True, candles=[event_bar, *flat_bars],
    )
    assert outcome.status == "expired"
    assert outcome.closed_bar_ts == flat_bars[-1].bucket_start
    assert outcome.exit_price == pytest.approx(flat_bars[-1].close)


def test_closes_hit_sl_when_a_later_candle_closes_past_stop_loss(session):
    candles = [_candle(i, low=90.0, high=110.0, close=100.0) for i in range(6)]
    candles[5] = _candle(5, low=95.0, high=101.0, close=100.0)
    candles.append(_candle(6, low=97.0, high=103.0, close=100.0))  # entry fill, narrow so it never itself pierces SL/TP
    event = _event(SPRING, 5, candles[5])
    _sync(session, "FPT", candles, [event])
    stop_loss = session.exec(_select_scenario()).one().stop_loss

    # A later candle closes below the stop loss.
    candles.append(_candle(7, low=stop_loss - 1, high=stop_loss - 1, close=stop_loss - 1))
    _sync(session, "FPT", candles, [event])

    row = session.exec(_select_scenario()).one()
    assert row.status == "hit_sl"
    assert row.closed_bar_ts == candles[7].bucket_start
    assert row.closed_at is not None
    assert str(round(stop_loss, 2)) in row.close_reason or "SL" in row.close_reason


def test_hit_sl_triggers_on_intrabar_low_even_if_close_recovers_above_it(session):
    # Real stop orders execute on touch, not only once the candle closes past
    # the level -- a bar whose low pierces SL then recovers to close above it
    # must still count as a stop-out, not survive as active.
    candles = [_candle(i, low=90.0, high=110.0, close=100.0) for i in range(6)]
    candles[5] = _candle(5, low=95.0, high=101.0, close=100.0)
    candles.append(_candle(6, low=97.0, high=103.0, close=100.0))  # entry fill, narrow so it never itself pierces SL/TP
    event = _event(SPRING, 5, candles[5])
    _sync(session, "FPT", candles, [event])
    stop_loss = session.exec(_select_scenario()).one().stop_loss

    candles.append(_candle(7, low=stop_loss - 1, high=stop_loss + 5, close=stop_loss + 2))
    _sync(session, "FPT", candles, [event])

    row = session.exec(_select_scenario()).one()
    assert row.status == "hit_sl"


def test_bar_touching_both_sl_and_tp_resolves_as_hit_sl_not_hit_tp(session):
    # A single wide-range bar can pierce SL and reach TP in the same session
    # (real on volatile/low-liquidity assets). Without tick data there's no
    # way to know which happened first, so the conservative assumption is SL
    # first -- a real stop order would already have executed on the way down,
    # before price could recover enough to also reach TP.
    candles = [_candle(i, low=90.0, high=110.0, close=100.0) for i in range(6)]
    candles[5] = _candle(5, low=95.0, high=101.0, close=100.0)
    candles.append(_candle(6, low=97.0, high=103.0, close=100.0))  # entry fill, narrow so it never itself pierces SL/TP
    event = _event(SPRING, 5, candles[5])
    _sync(session, "FPT", candles, [event])
    scenario = session.exec(_select_scenario()).one()
    stop_loss, take_profit = scenario.stop_loss, scenario.take_profit

    candles.append(_candle(7, low=stop_loss - 5, high=take_profit + 5, close=(stop_loss + take_profit) / 2))
    _sync(session, "FPT", candles, [event])

    row = session.exec(_select_scenario()).one()
    assert row.status == "hit_sl"


def _bullish_scenario_with_atr(session, ticker="FPT"):
    """15 identical pre-event candles -- enough real history for a
    computable ATR (ATR_PERIOD=14) -- plus an event bar and entry fill,
    synced once. TR is exactly (high-low)=10 for every bar (constant close
    means no gap component), so ATR=10.0 exactly. Returns the candles built
    so far (callers append their own subsequent bars) and the event."""
    candles = [_candle(i, low=95.0, high=105.0, close=100.0) for i in range(15)]
    event = _event(SPRING, 14, candles[14])
    candles.append(_candle(15, open=103.0, low=101.0, high=104.0, close=103.0))  # entry fill
    _sync(session, ticker, candles, [event])
    return candles, event


def test_hit_tp_still_triggers_on_intrabar_high_when_sl_untouched(session):
    # No fixed take-profit anymore (see TRAIL_ATR_MULT) -- "hit_tp" now means
    # the stop trailed to breakeven-or-better and got hit, still resolved
    # intrabar like SL always was.
    candles, event = _bullish_scenario_with_atr(session)
    # unrealized = 115-103 = 12 >= risk_distance (103-94.715=8.285) -> trail
    # activates: trail_level = 115-1.5*10=100 -> current_stop=max(94.715,103,100)=103 (breakeven).
    candles.append(_candle(16, low=100.0, high=115.0, close=105.0))
    candles.append(_candle(17, low=102.0, high=106.0, close=104.0))  # low=102 pierces the 103 stop
    _sync(session, "FPT", candles, [event])

    row = session.exec(_select_scenario()).one()
    assert row.status == "hit_tp"


def test_bearish_hit_sl_triggers_on_intrabar_high_even_if_close_recovers_below_it(session):
    # Bearish scenarios are never CREATED anymore (spot-only, see
    # test_bearish_event_never_creates_a_scenario), but _resolve_outcome's
    # intrabar hit_sl/hit_tp branching is shared for both directions -- a
    # pre-existing bearish row (e.g. from before this app enforced spot-only,
    # or a future non-spot mode) must still resolve correctly. _make_scenario
    # bypasses the creation gate to set one up directly.
    _make_scenario(session, is_bullish=False, entry=100.0, stop_loss=105.0, take_profit=90.0, day=0)
    candles = [_candle(1, low=103.0, high=106.0, close=104.0)]

    _sync(session, "FPT", candles, [])

    row = session.exec(_select_scenario()).one()
    assert row.status == "hit_sl"


def test_bearish_bar_touching_both_sl_and_tp_resolves_as_hit_sl(session):
    _make_scenario(session, is_bullish=False, entry=100.0, stop_loss=105.0, take_profit=90.0, day=0)
    candles = [_candle(1, low=85.0, high=110.0, close=97.5)]

    _sync(session, "FPT", candles, [])

    row = session.exec(_select_scenario()).one()
    assert row.status == "hit_sl"


def test_close_reason_respects_language(session):
    candles = [_candle(i, low=90.0, high=110.0, close=100.0) for i in range(6)]
    candles[5] = _candle(5, low=95.0, high=101.0, close=100.0)
    candles.append(_candle(6, low=97.0, high=103.0, close=100.0))  # entry fill, narrow so it never itself pierces SL/TP
    event = _event(SPRING, 5, candles[5])
    _sync(session, "FPT", candles, [event], language="en")
    stop_loss = session.exec(_select_scenario()).one().stop_loss

    candles.append(_candle(7, low=stop_loss - 1, high=stop_loss - 1, close=stop_loss - 1))
    _sync(session, "FPT", candles, [event], language="en")

    row = session.exec(_select_scenario()).one()
    assert "SL" in row.close_reason
    assert "vượt qua" not in row.close_reason


def test_closes_hit_tp_via_trailing_stop_after_favorable_move(session):
    candles, event = _bullish_scenario_with_atr(session)
    candles.append(_candle(16, low=100.0, high=115.0, close=105.0))
    candles.append(_candle(17, low=102.0, high=106.0, close=104.0))
    _sync(session, "FPT", candles, [event])

    row = session.exec(_select_scenario()).one()
    assert row.status == "hit_tp"
    assert row.closed_bar_ts == candles[-1].bucket_start


def test_expires_after_max_bars_with_neither_tp_nor_sl_hit(session):
    candles = [_candle(i, low=90.0, high=110.0, close=100.0) for i in range(6)]
    candles[5] = _candle(5, low=95.0, high=101.0, close=100.0)
    candles.append(_candle(6, low=97.0, high=103.0, close=100.0))  # entry fill, narrow so it never itself pierces SL/TP
    event = _event(SPRING, 5, candles[5])
    _sync(session, "FPT", candles, [event])

    # DEFAULT_MAX_BARS candles afterwards, all flat -- never touches SL or TP.
    for i in range(7, 7 + trade_scenario.DEFAULT_MAX_BARS):
        candles.append(_candle(i, low=99.0, high=101.0, close=100.0))
    _sync(session, "FPT", candles, [event])

    row = session.exec(_select_scenario()).one()
    assert row.status == "expired"
    assert "hết" in row.close_reason.lower() or str(trade_scenario.DEFAULT_MAX_BARS) in row.close_reason


def test_settlement_gate_blocks_hit_sl_before_min_bars_for_stock(session):
    # T+2.5: a stock scenario can't realistically exit within the settlement
    # window even if price already crossed the stop-loss level -- shares
    # bought at the event aren't tradeable again yet.
    session.add(Symbol(ticker="FPT", asset_class=AssetClass.STOCK))
    session.commit()
    candles = [_candle(i, low=90.0, high=110.0, close=100.0) for i in range(6)]
    candles[5] = _candle(5, low=95.0, high=101.0, close=100.0)
    candles.append(_candle(6, low=90.0, high=110.0, close=100.0))  # entry fill
    event = _event(SPRING, 5, candles[5])
    _sync(session, "FPT", candles, [event])
    stop_loss = session.exec(_select_scenario()).one().stop_loss

    # Only 2 bars elapse after entry fill -- under
    # SETTLEMENT_BARS_STOCK[Timeframe.DAILY]=3 -- both closing past
    # stop-loss, yet the scenario must stay active.
    candles.append(_candle(7, low=stop_loss - 1, high=stop_loss - 1, close=stop_loss - 1))
    candles.append(_candle(8, low=stop_loss - 1, high=stop_loss - 1, close=stop_loss - 1))
    _sync(session, "FPT", candles, [event])

    row = session.exec(_select_scenario()).one()
    assert row.status == "active"


def test_settlement_gate_allows_hit_sl_once_bars_elapsed_for_stock(session):
    session.add(Symbol(ticker="FPT", asset_class=AssetClass.STOCK))
    session.commit()
    candles = [_candle(i, low=90.0, high=110.0, close=100.0) for i in range(6)]
    candles[5] = _candle(5, low=95.0, high=101.0, close=100.0)
    candles.append(_candle(6, low=90.0, high=110.0, close=100.0))  # entry fill
    event = _event(SPRING, 5, candles[5])
    _sync(session, "FPT", candles, [event])
    stop_loss = session.exec(_select_scenario()).one().stop_loss

    # Entry fill is itself the first settlement-window bar, so 1 more bar than
    # SETTLEMENT_BARS_STOCK is needed past it for the gate to lift.
    settlement_bars = trade_scenario.SETTLEMENT_BARS_STOCK[Timeframe.DAILY]
    for i in range(7, 7 + settlement_bars + 1):
        candles.append(_candle(i, low=stop_loss - 1, high=stop_loss - 1, close=stop_loss - 1))
    _sync(session, "FPT", candles, [event])

    row = session.exec(_select_scenario()).one()
    assert row.status == "hit_sl"


def test_settlement_gate_does_not_apply_to_crypto(session):
    session.add(Symbol(ticker="BITCOIN", asset_class=AssetClass.CRYPTO))
    session.commit()
    candles = [_candle(i, low=90.0, high=110.0, close=100.0) for i in range(6)]
    candles[5] = _candle(5, low=95.0, high=101.0, close=100.0)
    candles.append(_candle(6, low=90.0, high=110.0, close=100.0))  # entry fill
    event = _event(SPRING, 5, candles[5])
    _sync(session, "BITCOIN", candles, [event])
    stop_loss = session.exec(_select_scenario()).one().stop_loss

    # Crypto: T+0, 24/7 -- hits on the very next bar like before the gate existed.
    candles.append(_candle(7, low=stop_loss - 1, high=stop_loss - 1, close=stop_loss - 1))
    _sync(session, "BITCOIN", candles, [event])

    row = session.exec(_select_scenario()).one()
    assert row.status == "hit_sl"


def test_get_scenario_prefers_active_over_closed(session):
    candles = [_candle(i, low=90.0, high=110.0, close=100.0) for i in range(6)]
    candles[5] = _candle(5, low=95.0, high=101.0, close=100.0)
    candles.append(_candle(6, low=97.0, high=103.0, close=100.0))  # entry fill, narrow so it never itself pierces SL/TP
    event = _event(SPRING, 5, candles[5])
    _sync(session, "FPT", candles, [event])

    scenario = trade_scenario.get_scenario(session, "FPT", Timeframe.DAILY, STRATEGY)
    assert scenario is not None
    assert scenario.status == "active"


def test_get_scenario_falls_back_to_most_recently_closed(session):
    candles = [_candle(i, low=90.0, high=110.0, close=100.0) for i in range(6)]
    candles[5] = _candle(5, low=95.0, high=101.0, close=100.0)
    candles.append(_candle(6, low=97.0, high=103.0, close=100.0))  # entry fill, narrow so it never itself pierces SL/TP
    event = _event(SPRING, 5, candles[5])
    _sync(session, "FPT", candles, [event])
    stop_loss = session.exec(_select_scenario()).one().stop_loss
    candles.append(_candle(7, low=stop_loss - 1, high=stop_loss - 1, close=stop_loss - 1))
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
    exit_price=None, source="live",
):
    row = TradeScenario(
        ticker=ticker, timeframe=timeframe, strategy=strategy, event_type=event_type,
        event_ts=pd.Timestamp("2025-01-01") + pd.Timedelta(days=day), is_bullish=is_bullish,
        entry=entry, stop_loss=stop_loss, take_profit=take_profit, max_bars=10, status=status,
        exit_price=exit_price, source=source,
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


def test_list_scenarios_defaults_to_live_source_only(session):
    _make_scenario(session, ticker="FPT", source="live")
    _make_scenario(session, ticker="HPG", source="backtest", day=1)

    items, total = trade_scenario.list_scenarios(session, page=1, page_size=50)

    assert total == 1
    assert {i.ticker for i in items} == {"FPT"}


def test_list_scenarios_source_none_pools_every_source(session):
    _make_scenario(session, ticker="FPT", source="live")
    _make_scenario(session, ticker="HPG", source="backtest", day=1)

    items, total = trade_scenario.list_scenarios(session, page=1, page_size=50, source=None)

    assert total == 2
    assert {i.ticker for i in items} == {"FPT", "HPG"}


def test_list_scenarios_can_filter_to_backtest_only(session):
    _make_scenario(session, ticker="FPT", source="live")
    _make_scenario(session, ticker="HPG", source="backtest", day=1)

    items, total = trade_scenario.list_scenarios(session, page=1, page_size=50, source="backtest")

    assert total == 1
    assert {i.ticker for i in items} == {"HPG"}


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


def test_scenario_stats_ignores_backtest_rows_by_default(session):
    # Trust Layer numbers (win_rate, expectancy, monte_carlo/bootstrap/
    # walk_forward) must never silently pool in scenario_backtest's
    # hindsight-generated rows alongside genuinely out-of-sample live ones.
    _make_scenario(session, status="hit_tp", source="live")
    _make_scenario(session, status="hit_sl", day=1, source="backtest")

    stats = trade_scenario.get_scenario_stats(session)

    assert stats["decided_count"] == 1
    assert stats["win_count"] == 1
    assert stats["loss_count"] == 0


def test_scenario_stats_source_none_pools_every_source(session):
    _make_scenario(session, status="hit_tp", source="live")
    _make_scenario(session, status="hit_sl", day=1, source="backtest")

    stats = trade_scenario.get_scenario_stats(session, source=None)

    assert stats["decided_count"] == 2


def test_scenario_stats_can_be_scoped_to_backtest_only(session):
    _make_scenario(session, status="hit_tp", source="live")
    _make_scenario(session, status="hit_sl", day=1, source="backtest")

    stats = trade_scenario.get_scenario_stats(session, source="backtest")

    assert stats["decided_count"] == 1
    assert stats["loss_count"] == 1
    assert stats["win_count"] == 0


def test_list_scenarios_no_direction_filter_by_default(session):
    _make_scenario(session, ticker="FPT", is_bullish=True)
    _make_scenario(session, ticker="HPG", is_bullish=False, day=1)

    items, total = trade_scenario.list_scenarios(session, page=1, page_size=50)

    assert total == 2


def test_list_scenarios_can_filter_to_bullish_only(session):
    _make_scenario(session, ticker="FPT", is_bullish=True)
    _make_scenario(session, ticker="HPG", is_bullish=False, day=1)

    items, total = trade_scenario.list_scenarios(session, page=1, page_size=50, is_bullish=True)

    assert total == 1
    assert items[0].ticker == "FPT"


def test_list_scenarios_can_filter_to_bearish_only(session):
    _make_scenario(session, ticker="FPT", is_bullish=True)
    _make_scenario(session, ticker="HPG", is_bullish=False, day=1)

    items, total = trade_scenario.list_scenarios(session, page=1, page_size=50, is_bullish=False)

    assert total == 1
    assert items[0].ticker == "HPG"


def test_scenario_stats_can_be_scoped_to_bullish_only(session):
    # Spot-only trading (no short-selling) can never actually execute a
    # bearish scenario -- pooling both directions into one Trust Layer number
    # answers a question a spot trader never asked. This filter lets stats
    # reflect only what's actually tradeable.
    _make_scenario(session, status="hit_tp", is_bullish=True)
    _make_scenario(session, status="hit_sl", day=1, is_bullish=False)

    stats = trade_scenario.get_scenario_stats(session, is_bullish=True)

    assert stats["decided_count"] == 1
    assert stats["win_count"] == 1
    assert stats["loss_count"] == 0


def test_scenario_stats_can_be_scoped_to_bearish_only(session):
    _make_scenario(session, status="hit_tp", is_bullish=True)
    _make_scenario(session, status="hit_sl", day=1, is_bullish=False)

    stats = trade_scenario.get_scenario_stats(session, is_bullish=False)

    assert stats["decided_count"] == 1
    assert stats["loss_count"] == 1
    assert stats["win_count"] == 0


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
    candles.append(_candle(6, low=97.0, high=103.0, close=100.0))  # entry fill
    event = _event(SPRING, 5, candles[5])
    _sync(session, "FPT", candles, [event])
    stop_loss = session.exec(_select_scenario()).one().stop_loss

    candles.append(_candle(7, low=stop_loss - 1, high=stop_loss - 1, close=stop_loss - 1))
    _sync(session, "FPT", candles, [event])

    row = session.exec(_select_scenario()).one()
    assert row.status == "hit_sl"
    assert row.exit_price == pytest.approx(stop_loss)


def test_exit_price_set_on_hit_tp(session):
    candles, event = _bullish_scenario_with_atr(session)
    entry = session.exec(_select_scenario()).one().entry
    candles.append(_candle(16, low=100.0, high=115.0, close=105.0))
    candles.append(_candle(17, low=102.0, high=106.0, close=104.0))
    _sync(session, "FPT", candles, [event])

    row = session.exec(_select_scenario()).one()
    assert row.status == "hit_tp"
    assert row.exit_price == pytest.approx(entry)  # trailed to breakeven exactly in this setup


def test_exit_price_set_on_expiry_to_last_close(session):
    candles = [_candle(i, low=90.0, high=110.0, close=100.0) for i in range(6)]
    candles[5] = _candle(5, low=95.0, high=101.0, close=100.0)
    candles.append(_candle(6, low=97.0, high=103.0, close=100.0))  # entry fill
    event = _event(SPRING, 5, candles[5])
    _sync(session, "FPT", candles, [event])

    for i in range(7, 7 + trade_scenario.DEFAULT_MAX_BARS):
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


def test_portfolio_cap_is_scoped_per_strategy_not_global(session):
    # Regression: shadow multi-strategy analysis (settings.shadow_strategy_keys)
    # runs several strategies against the same tracked universe purely for
    # signal_outcomes/trade_scenario data accumulation -- they aren't
    # competing for the same real capital, so one strategy's active scenarios
    # must never count against another strategy's own cap. Before this fix,
    # _count_active pooled every strategy together, so once ANY one strategy
    # (typically whichever ran first/longest) reached max_concurrent_scenarios
    # globally, every OTHER strategy was silently blocked from ever creating
    # a scenario again, no matter how few (zero) it had of its own.
    settings_service.update(session, {"max_concurrent_scenarios": "1"})
    _make_scenario(session, ticker="HPG", strategy="wyckoff", status="active")  # wyckoff already at its own cap

    candles = [_candle(i, low=90.0, high=110.0, close=100.0) for i in range(6)]
    candles[5] = _candle(5, low=95.0, high=101.0, close=100.0)
    candles.append(_candle(6, low=90.0, high=110.0, close=100.0))  # entry fill
    event = _event(SPRING, 5, candles[5])
    provider_cfg = ProviderConfig(provider=PROVIDER_ANTHROPIC, model="claude-sonnet-4-5", api_key="", language="vi")
    trade_scenario.sync_scenarios(
        session, "FPT", Timeframe.DAILY, "smc", candles, [event], BULLISH_EVENTS, LEVELS,
        provider_cfg, _fake_strategy_module(), None, None, RANGING_PHASES,
    )

    assert session.exec(_select_scenario().where(TradeScenario.strategy == "smc")).first() is not None


def test_portfolio_cap_crypto_sub_limit_blocks_only_crypto(session):
    session.add(Symbol(ticker="BITCOIN", asset_class=AssetClass.CRYPTO))
    session.add(Symbol(ticker="ETHEREUM", asset_class=AssetClass.CRYPTO))
    session.add(Symbol(ticker="FPT", asset_class=AssetClass.STOCK))
    session.commit()
    settings_service.update(session, {"max_concurrent_scenarios_crypto": "1"})
    _make_scenario(session, ticker="ETHEREUM", status="active")  # crypto already at its sub-cap

    candles = [_candle(i, low=90.0, high=110.0, close=100.0) for i in range(6)]
    candles[5] = _candle(5, low=95.0, high=101.0, close=100.0)
    candles.append(_candle(6, low=90.0, high=110.0, close=100.0))  # entry fill (for FPT's later check)
    event = _event(SPRING, 5, candles[5])

    _sync(session, "BITCOIN", candles, [event])
    assert session.exec(_select_scenario().where(TradeScenario.ticker == "BITCOIN")).first() is None

    _sync(session, "FPT", candles, [event])  # stock isn't subject to the crypto sub-cap
    assert session.exec(_select_scenario().where(TradeScenario.ticker == "FPT")).first() is not None


def test_scenario_stats_expectancy_r_and_pnl_amount(session):
    settings_service.update(session, {"notional_capital": "100000", "risk_pct_per_trade": "1.0"})
    # Bullish, risk distance 5 (entry 100 -> stop 95). Hits TP at 110 ->
    # raw R = (110-100)/5 = +2.0R. Zero out every stock cost (slippage, fee, tax).
    settings_service.update(session, {
        "slippage_pct_stock": "0.0", "slippage_pct_crypto": "0.0",
        "broker_fee_pct_stock": "0.0", "sell_tax_pct_stock": "0.0",
    })
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
    settings_service.update(session, {
        "slippage_pct_stock": "0.0", "broker_fee_pct_stock": "0.0", "sell_tax_pct_stock": "0.0",
    })
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
    settings_service.update(session, {
        "slippage_pct_stock": "1.0", "broker_fee_pct_stock": "0.0", "sell_tax_pct_stock": "0.0",
    })  # 1% of entry, fee/tax zeroed to isolate slippage's own effect
    _make_scenario(
        session, ticker="FPT", is_bullish=True, entry=100.0, stop_loss=95.0, take_profit=110.0,
        status="hit_tp", exit_price=110.0,
    )

    stats = trade_scenario.get_scenario_stats(session)

    # Slippage worsens a bullish exit downward: adjusted_exit = 110 - 1 (1% of
    # entry 100) = 109 -> R = (109-100)/5 = 1.8, less than the naive 2.0.
    assert stats["expectancy_r"] == pytest.approx(1.8)


def test_scenario_stats_broker_fee_and_tax_worsen_stock_exit(session):
    settings_service.update(session, {
        "slippage_pct_stock": "0.0", "broker_fee_pct_stock": "0.5", "sell_tax_pct_stock": "0.2",
    })
    _make_scenario(
        session, ticker="FPT", is_bullish=True, entry=100.0, stop_loss=95.0, take_profit=110.0,
        status="hit_tp", exit_price=110.0,
    )

    stats = trade_scenario.get_scenario_stats(session)

    # Combined cost 0.7% of entry (100) = 0.7 -> adjusted_exit = 109.3 ->
    # R = (109.3-100)/5 = 1.86.
    assert stats["expectancy_r"] == pytest.approx(1.86)


def test_scenario_stats_crypto_trading_fee_worsens_exit(session):
    # Crypto exchanges charge a round-trip taker fee on top of slippage --
    # omitting it understated crypto costs relative to stock (which already
    # carries broker fee + tax alongside slippage).
    session.add(Symbol(ticker="BITCOIN", asset_class=AssetClass.CRYPTO))
    session.commit()
    settings_service.update(session, {"slippage_pct_crypto": "0.0", "trading_fee_pct_crypto": "0.2"})
    _make_scenario(
        session, ticker="BITCOIN", is_bullish=True, entry=100.0, stop_loss=95.0, take_profit=110.0,
        status="hit_tp", exit_price=110.0,
    )

    stats = trade_scenario.get_scenario_stats(session, asset_class=AssetClass.CRYPTO)

    # Cost 0.2% of entry (100) = 0.2 -> adjusted_exit = 109.8 -> R = (109.8-100)/5 = 1.96.
    assert stats["expectancy_r"] == pytest.approx(1.96)


def test_scenario_stats_crypto_ignores_stock_fee_and_tax(session):
    session.add(Symbol(ticker="BITCOIN", asset_class=AssetClass.CRYPTO))
    session.commit()
    settings_service.update(session, {
        "slippage_pct_crypto": "0.0", "trading_fee_pct_crypto": "0.0",
        "broker_fee_pct_stock": "5.0", "sell_tax_pct_stock": "5.0",
    })
    _make_scenario(
        session, ticker="BITCOIN", is_bullish=True, entry=100.0, stop_loss=95.0, take_profit=110.0,
        status="hit_tp", exit_price=110.0,
    )

    stats = trade_scenario.get_scenario_stats(session, asset_class=AssetClass.CRYPTO)

    # Stock-only fee/tax must not leak into a crypto scenario's cost calc.
    assert stats["expectancy_r"] == pytest.approx(2.0)


def test_scenario_stats_max_drawdown_and_consecutive_losses(session):
    settings_service.update(session, {
        "slippage_pct_stock": "0.0", "broker_fee_pct_stock": "0.0", "sell_tax_pct_stock": "0.0",
    })
    # +1R, -1R, -1R, +1R in that order (day 0..3) -> cumulative R curve:
    # 1, 0, -1, 0. Peak after trade 1 is 1; trough at trade 3 is -1 -> max
    # drawdown = 1 - (-1) = 2R. Longest losing streak = 2 (trades 2-3).
    _make_scenario(session, entry=100.0, stop_loss=95.0, take_profit=105.0, status="hit_tp", exit_price=105.0, day=0)
    _make_scenario(session, entry=100.0, stop_loss=95.0, take_profit=105.0, status="hit_sl", exit_price=95.0, day=1)
    _make_scenario(session, entry=100.0, stop_loss=95.0, take_profit=105.0, status="hit_sl", exit_price=95.0, day=2)
    _make_scenario(session, entry=100.0, stop_loss=95.0, take_profit=105.0, status="hit_tp", exit_price=105.0, day=3)

    stats = trade_scenario.get_scenario_stats(session)

    assert stats["max_drawdown_r"] == pytest.approx(2.0)
    assert stats["max_consecutive_losses"] == 2
    # The Monte Carlo permutation test's "actual" drawdown must match the
    # scenario-level max_drawdown_r above -- same calculation, same
    # chronological order, just reused inside stats_significance.
    assert stats["monte_carlo"] is not None
    assert stats["monte_carlo"]["actual_max_drawdown_r"] == pytest.approx(2.0)
    assert stats["monte_carlo"]["n_trades"] == 4
    assert 0.0 <= stats["monte_carlo"]["p_value_max_drawdown_r"] <= 1.0
    assert 0.0 <= stats["monte_carlo"]["p_value_r_sharpe"] <= 1.0
    # 4 trades clears monte_carlo's 3-trade minimum but not bootstrap's
    # (5) or walk_forward's (10, for the default 5 windows).
    assert stats["bootstrap"] is None
    assert stats["walk_forward"] is None


def test_scenario_stats_monte_carlo_is_none_with_too_few_trades(session):
    settings_service.update(session, {
        "slippage_pct_stock": "0.0", "broker_fee_pct_stock": "0.0", "sell_tax_pct_stock": "0.0",
    })
    _make_scenario(session, entry=100.0, stop_loss=95.0, take_profit=105.0, status="hit_tp", exit_price=105.0, day=0)
    _make_scenario(session, entry=100.0, stop_loss=95.0, take_profit=105.0, status="hit_sl", exit_price=95.0, day=1)

    stats = trade_scenario.get_scenario_stats(session)

    assert stats["pnl_sample_count"] == 2  # below the 3-trade minimum
    assert stats["monte_carlo"] is None
    assert stats["bootstrap"] is None
    assert stats["walk_forward"] is None


def test_scenario_stats_bootstrap_and_walk_forward_populate_with_enough_trades(session):
    settings_service.update(session, {
        "slippage_pct_stock": "0.0", "broker_fee_pct_stock": "0.0", "sell_tax_pct_stock": "0.0",
    })
    # 10 trades, alternating +1R/-1R -- clears bootstrap's 5-trade minimum
    # and walk_forward's 10-trade minimum (5 windows x 2 trades each).
    for i in range(10):
        status = "hit_tp" if i % 2 == 0 else "hit_sl"
        exit_price = 105.0 if status == "hit_tp" else 95.0
        _make_scenario(
            session, entry=100.0, stop_loss=95.0, take_profit=105.0,
            status=status, exit_price=exit_price, day=i,
        )

    stats = trade_scenario.get_scenario_stats(session)

    assert stats["pnl_sample_count"] == 10
    assert stats["bootstrap"] is not None
    assert stats["bootstrap"]["n_trades"] == 10
    assert 0.0 <= stats["bootstrap"]["prob_positive"] <= 1.0
    assert stats["bootstrap"]["ci_lower"] <= stats["bootstrap"]["median_r_sharpe"] <= stats["bootstrap"]["ci_upper"]

    assert stats["walk_forward"] is not None
    assert stats["walk_forward"]["n_windows"] == 5
    assert sum(w["n_trades"] for w in stats["walk_forward"]["per_window"]) == 10


def test_scenario_stats_median_expectancy_r_resists_a_single_outlier(session):
    # A too-tight stop (tiny risk_distance) can blow the SAME $ move up into
    # a huge R-multiple (see the _create_scenarios comment on NoDemand/
    # NoSupply -- up to ~18x observed in production). One such outlier among
    # otherwise ordinary +1R trades drags the mean (expectancy_r) well above
    # what most trades actually looked like; the median isn't moved by a
    # single extreme value the way a mean is.
    settings_service.update(session, {
        "slippage_pct_stock": "0.0", "broker_fee_pct_stock": "0.0", "sell_tax_pct_stock": "0.0",
    })
    for i in range(4):
        _make_scenario(
            session, entry=100.0, stop_loss=95.0, take_profit=105.0,
            status="hit_tp", exit_price=105.0, day=i,
        )
    # Same $5 move, razor-thin risk distance (0.25) -> R = 5/0.25 = 20.
    _make_scenario(
        session, entry=100.0, stop_loss=99.75, take_profit=105.0,
        status="hit_tp", exit_price=105.0, day=4,
    )

    stats = trade_scenario.get_scenario_stats(session)

    assert stats["expectancy_r"] == pytest.approx((1.0 * 4 + 20.0) / 5)  # mean dragged to 4.8
    assert stats["median_expectancy_r"] == pytest.approx(1.0)  # unmoved by the one outlier


def test_low_sample_size_flag_true_below_threshold(session):
    _make_scenario(session, status="hit_tp", exit_price=110.0)

    stats = trade_scenario.get_scenario_stats(session)

    assert stats["pnl_sample_count"] == 1
    assert stats["low_sample_size"] is True


def test_low_sample_size_flag_false_at_or_above_threshold(session):
    for i in range(trade_scenario.MIN_RELIABLE_SAMPLE_SIZE):
        _make_scenario(session, status="hit_tp", exit_price=110.0, day=i)

    stats = trade_scenario.get_scenario_stats(session)

    assert stats["pnl_sample_count"] == trade_scenario.MIN_RELIABLE_SAMPLE_SIZE
    assert stats["low_sample_size"] is False


def test_low_sample_size_flag_none_when_no_data(session):
    stats = trade_scenario.get_scenario_stats(session)

    assert stats["pnl_sample_count"] == 0
    assert stats["low_sample_size"] is None


def test_scenario_stats_current_config_count_only_set_with_strategy(session):
    _make_scenario(session, status="hit_tp")

    no_strategy = trade_scenario.get_scenario_stats(session)
    assert no_strategy["current_config_count"] is None

    with_version = trade_scenario.get_scenario_stats(session, strategy=STRATEGY, current_config_version="wyckoff:abc")
    assert with_version["current_config_count"] == 0  # row predates config_version, stored as ""


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


# --- edge_vs_buy_hold_pct: benchmark matched to each trade's own $ size and window ---

def _seed_candle(session, ticker: str, day: int, close: float):
    session.add(
        Candle(
            ticker=ticker, timeframe=Timeframe.DAILY,
            bucket_start=(pd.Timestamp("2025-01-01") + pd.Timedelta(days=day)).to_pydatetime(),
            open=close, high=close, low=close, close=close, volume=1000.0,
        )
    )


def test_edge_vs_buy_hold_uses_the_scenarios_own_window_and_dollar_sizing(session):
    settings_service.update(session, {
        "notional_capital": "100000", "risk_pct_per_trade": "1.0",
        "slippage_pct_stock": "0.0", "broker_fee_pct_stock": "0.0", "sell_tax_pct_stock": "0.0",
    })
    # Risk distance 5 (entry 100 -> stop 95), hits TP 110 -> raw R = +2.0.
    _make_scenario(
        session, ticker="FPT", is_bullish=True, entry=100.0, stop_loss=95.0, take_profit=110.0,
        status="hit_tp", exit_price=110.0, day=0,
    )
    scenario = session.exec(_select_scenario()).one()
    scenario.closed_bar_ts = (pd.Timestamp("2025-01-01") + pd.Timedelta(days=5)).to_pydatetime()
    session.add(scenario)
    # Buy-and-hold over that SAME window (day 0 -> day 5): 100 -> 120, +20%.
    _seed_candle(session, "FPT", 0, 100.0)
    _seed_candle(session, "FPT", 5, 120.0)
    session.commit()

    stats = trade_scenario.get_scenario_stats(session)

    risk_amount = 100_000 * 1.0 / 100  # 1000
    assert stats["strategy_return_pct"] == pytest.approx(2.0 * risk_amount / 100_000)  # 0.02
    assert stats["benchmark_buy_hold_pct"] == pytest.approx(0.20 * risk_amount / 100_000)  # 0.002
    assert stats["edge_vs_buy_hold_pct"] == pytest.approx(
        (2.0 * risk_amount - 0.20 * risk_amount) / 100_000
    )


def test_benchmark_and_edge_are_none_without_matching_candle_data(session):
    # No Candle rows seeded at all -- buy_hold_return has nothing to compute
    # from, so the benchmark/edge stay None. strategy_return_pct is
    # unaffected, since it doesn't depend on the benchmark being computable.
    settings_service.update(session, {"notional_capital": "100000", "risk_pct_per_trade": "1.0"})
    _make_scenario(
        session, is_bullish=True, entry=100.0, stop_loss=95.0, take_profit=110.0,
        status="hit_tp", exit_price=110.0,
    )

    stats = trade_scenario.get_scenario_stats(session)

    assert stats["benchmark_buy_hold_pct"] is None
    assert stats["edge_vs_buy_hold_pct"] is None
    assert stats["strategy_return_pct"] is not None


def test_edge_vs_buy_hold_only_counts_scenarios_with_a_computable_benchmark(session):
    # FPT has candle data (benchmark computable); HPG doesn't. Both still
    # contribute to strategy_return_pct (the whole book's return), but only
    # FPT's trade should count toward benchmark_buy_hold_pct/edge -- mixing in
    # HPG's $ P&L on the strategy side without a matching benchmark $ amount
    # would silently re-introduce the mismatched-population bug.
    settings_service.update(session, {
        "notional_capital": "100000", "risk_pct_per_trade": "1.0",
        "slippage_pct_stock": "0.0", "broker_fee_pct_stock": "0.0", "sell_tax_pct_stock": "0.0",
    })
    session.add(Symbol(ticker="FPT", asset_class=AssetClass.STOCK))
    session.add(Symbol(ticker="HPG", asset_class=AssetClass.STOCK))
    session.commit()

    _make_scenario(
        session, ticker="FPT", is_bullish=True, entry=100.0, stop_loss=95.0, take_profit=110.0,
        status="hit_tp", exit_price=110.0, day=0,
    )
    fpt = session.exec(_select_scenario().where(TradeScenario.ticker == "FPT")).one()
    fpt.closed_bar_ts = (pd.Timestamp("2025-01-01") + pd.Timedelta(days=5)).to_pydatetime()
    session.add(fpt)
    _seed_candle(session, "FPT", 0, 100.0)
    _seed_candle(session, "FPT", 5, 120.0)

    _make_scenario(
        session, ticker="HPG", is_bullish=True, entry=100.0, stop_loss=95.0, take_profit=110.0,
        status="hit_tp", exit_price=110.0, day=1,
    )
    session.commit()

    stats = trade_scenario.get_scenario_stats(session)

    risk_amount = 100_000 * 1.0 / 100  # 1000
    # Both scenarios (R=+2.0 each) count toward the whole-book return.
    assert stats["strategy_return_pct"] == pytest.approx(2 * 2.0 * risk_amount / 100_000)
    # Only FPT's trade counts toward the benchmark/edge.
    assert stats["benchmark_buy_hold_pct"] == pytest.approx(0.20 * risk_amount / 100_000)
    assert stats["edge_vs_buy_hold_pct"] == pytest.approx(
        (2.0 * risk_amount - 0.20 * risk_amount) / 100_000
    )


# --- Scale-out at a profit target ----------------------------------------
# Off by default (see trade_scenario's scale-out block),
# so every test above resolves through the trailing stop alone exactly as it
# always did; these switch the knobs on explicitly.
#
# Shared numbers: 15 flat pre-event bars give ATR=10 exactly, entry=103.0 and
# stop_loss=94.715 (the event bar's 95.0 low less SL_BUFFER_PCT), so
# risk_distance=8.285 and a 2R target sits at 103 + 2*8.285 = 119.57.

_R_ENTRY = 103.0
_R_STOP = 95.0 * (1 - trade_scenario.SL_BUFFER_PCT)
_R_RISK = _R_ENTRY - _R_STOP
_R_TARGET_2R = _R_ENTRY + 2 * _R_RISK


def _resolve_with_future(future_bars, *, take_profit=None, max_bars=20):
    """15 flat pre-event bars + an event bar, then the caller's own future."""
    candles = [_candle(i, low=95.0, high=105.0, close=100.0) for i in range(15)]
    event_ts = candles[14].bucket_start
    candles.extend(future_bars)
    return trade_scenario._resolve_outcome(
        event_ts, _R_ENTRY, _R_STOP, max_bars, True, candles, 0, take_profit=take_profit,
    )


def test_first_profit_target_picks_the_nearest_of_the_two(monkeypatch):
    monkeypatch.setattr(trade_scenario, "PARTIAL_EXIT_R_MULTIPLE", 2.0)
    monkeypatch.setattr(trade_scenario, "PARTIAL_EXIT_AT_TAKE_PROFIT", True)
    # 2R sits at 119.57; a measured-move TP at 112 is nearer, so it wins.
    assert trade_scenario._first_profit_target(_R_ENTRY, _R_RISK, True, 112.0) == 112.0
    # ...and when the measured move is further out, the R target wins.
    assert trade_scenario._first_profit_target(_R_ENTRY, _R_RISK, True, 150.0) == pytest.approx(_R_TARGET_2R)


def test_first_profit_target_is_none_when_both_targets_are_off():
    assert trade_scenario._first_profit_target(_R_ENTRY, _R_RISK, True, 120.0) is None


def test_scale_out_records_the_partial_and_pulls_the_stop_to_breakeven(monkeypatch):
    monkeypatch.setattr(trade_scenario, "PARTIAL_EXIT_FRACTION", 0.5)
    monkeypatch.setattr(trade_scenario, "PARTIAL_EXIT_R_MULTIPLE", 1.0)  # target = 111.285
    outcome = _resolve_with_future([
        # Tags the 1R target. The trail also arms here (excursion >= 1R) but
        # lands at 112 - 1.5*ATR = 97, below breakeven -- so breakeven is what
        # actually protects the rest, which is the point being tested.
        _candle(15, low=104.0, high=112.0, close=110.0),
        _candle(16, low=100.0, high=112.0, close=105.0),  # falls back through breakeven
    ])
    assert outcome.partial_exit_price == pytest.approx(_R_ENTRY + _R_RISK)
    # The remaining half exits at breakeven, not at the original 94.715 stop --
    # taking profit off must protect what's left.
    assert outcome.exit_price == pytest.approx(_R_ENTRY)
    assert outcome.status == "hit_tp"


def test_the_trail_keeps_protecting_the_remainder_above_breakeven_after_a_scale_out(monkeypatch):
    monkeypatch.setattr(trade_scenario, "PARTIAL_EXIT_FRACTION", 0.5)
    monkeypatch.setattr(trade_scenario, "PARTIAL_EXIT_R_MULTIPLE", 2.0)  # target = 119.57
    outcome = _resolve_with_future([
        # A bigger excursion puts the trail at 120 - 1.5*ATR = 105, ABOVE
        # breakeven -- scaling out must not cap the rest at breakeven when the
        # trail has already locked in more.
        _candle(15, low=110.0, high=120.0, close=115.0),
        _candle(16, low=100.0, high=112.0, close=105.0),
    ])
    assert outcome.partial_exit_price == pytest.approx(_R_TARGET_2R)
    assert outcome.exit_price == pytest.approx(105.0)
    assert outcome.status == "hit_tp"


def test_a_target_closes_the_whole_position_when_scale_out_is_disabled(monkeypatch):
    monkeypatch.setattr(trade_scenario, "PARTIAL_EXIT_FRACTION", 0.0)
    monkeypatch.setattr(trade_scenario, "PARTIAL_EXIT_R_MULTIPLE", 2.0)
    outcome = _resolve_with_future([_candle(15, low=110.0, high=120.0, close=115.0)])
    assert outcome.partial_exit_price is None
    assert outcome.exit_price == pytest.approx(_R_TARGET_2R)
    assert outcome.status == "hit_tp"





def test_blended_r_multiple_weights_both_exit_legs(monkeypatch):
    monkeypatch.setattr(trade_scenario, "PARTIAL_EXIT_FRACTION", 0.5)
    # Half out at exactly 2R, the rest at breakeven (0R) -> a blended 1R.
    blended = trade_scenario.realized_r_multiple(
        _R_ENTRY, _R_STOP, _R_ENTRY, True, cost_pct=0.0, partial_exit_price=_R_TARGET_2R
    )
    assert blended == pytest.approx(1.0)


def test_r_multiple_falls_back_to_the_single_exit_formula_without_a_partial(monkeypatch):
    monkeypatch.setattr(trade_scenario, "PARTIAL_EXIT_FRACTION", 0.5)
    # partial_exit_price=None -- every scenario predating scale-out.
    plain = trade_scenario.realized_r_multiple(_R_ENTRY, _R_STOP, _R_TARGET_2R, True, cost_pct=0.0)
    assert plain == pytest.approx(2.0)


