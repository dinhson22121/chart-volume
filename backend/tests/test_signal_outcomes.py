import types
from datetime import datetime, timedelta

import pandas as pd
import pytest

from app.models import AssetClass, Candle, SignalOutcome, Symbol, Timeframe
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


def _seed_real_candles(session, ticker: str, n: int, start_close: float = 100.0, step: float = 1.0):
    """Unlike _candle() above (an in-memory SimpleNamespace passed straight
    into record_outcomes), this persists real Candle rows -- needed for
    get_stats' baseline, which reads directly from the Candle table."""
    t0 = pd.Timestamp("2025-01-01")
    candles = []
    for i in range(n):
        c = Candle(
            ticker=ticker,
            timeframe=Timeframe.DAILY,
            bucket_start=(t0 + pd.Timedelta(days=i)).to_pydatetime(),
            open=start_close, high=start_close, low=start_close,
            close=start_close + step * i,
            volume=1000.0,
        )
        session.add(c)
        candles.append(c)
    session.commit()
    return candles


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


def test_win_requires_clearing_the_threshold_not_just_positive(session):
    # A +0.5% drift (below the 1% threshold) is NOT a win -- only real moves count.
    candles = [_candle(i, 100.0) for i in range(30)]
    candles[10].close = 100.5  # +0.5% at the 5-bar horizon from index 5
    candles[15].close = 102.0  # +2.0% at the 10-bar horizon
    event = _event(SPRING, 5, candles[5].bucket_start, candles[5].close)

    signal_outcomes.record_outcomes(session, "FPT", Timeframe.DAILY, STRATEGY, candles, [event], BULLISH_EVENTS)

    row = session.exec(_select_outcome()).first()
    assert row.is_win_5 is False  # +0.5% < 1% threshold
    assert row.is_win_10 is True  # +2.0% clears it


def test_record_outcomes_stores_alignment_from_phase_trend(session):
    candles = [_candle(i, 100.0 + i) for i in range(30)]
    aligned_event = _event(SPRING, 5, candles[5].bucket_start, candles[5].close)  # bullish
    counter_event = _event(SOW, 8, candles[8].bucket_start, candles[8].close)  # bearish

    # Engine classified a bullish trend: the bullish Spring is aligned, the
    # bearish SOW is counter-trend.
    signal_outcomes.record_outcomes(
        session, "FPT", Timeframe.DAILY, STRATEGY, candles,
        [aligned_event, counter_event], BULLISH_EVENTS, phase_trend="bullish",
    )

    rows = {r.event_type: r for r in session.exec(_select_outcome()).all()}
    assert rows[SPRING].aligned is True
    assert rows[SOW].aligned is False


def test_get_stats_aligned_only_excludes_counter_trend(session):
    candles = [_candle(i, 100.0 + i) for i in range(30)]
    signal_outcomes.record_outcomes(
        session, "FPT", Timeframe.DAILY, STRATEGY, candles,
        [_event(SPRING, 3, candles[3].bucket_start, candles[3].close)], BULLISH_EVENTS,
        phase_trend="bullish",  # aligned
    )
    signal_outcomes.record_outcomes(
        session, "VCB", Timeframe.DAILY, STRATEGY, candles,
        [_event(SPRING, 3, candles[3].bucket_start, candles[3].close)], BULLISH_EVENTS,
        phase_trend="bearish",  # counter-trend
    )

    all_count = sum(s["count"] for s in signal_outcomes.get_stats(session))
    aligned_count = sum(s["count"] for s in signal_outcomes.get_stats(session, aligned_only=True))
    assert all_count == 2
    assert aligned_count == 1


def test_get_stats_win_rate_recomputed_from_returns_with_threshold(session):
    # Two Spring outcomes: one +0.5% (below threshold), one +2%. Win rate must
    # be 1/2 = 0.5, derived from the returns, regardless of stored is_win flags.
    candles = [_candle(i, 100.0) for i in range(30)]
    candles[13].close = 100.5  # index-3 Spring, 10-bar return +0.5%
    candles[20].close = 102.0  # index-10 Spring, 10-bar return +2.0%
    events = [
        _event(SPRING, 3, candles[3].bucket_start, candles[3].close),
        _event(SPRING, 10, candles[10].bucket_start, candles[10].close),
    ]
    signal_outcomes.record_outcomes(session, "FPT", Timeframe.DAILY, STRATEGY, candles, events, BULLISH_EVENTS)

    spring_stats = next(s for s in signal_outcomes.get_stats(session) if s["type"] == SPRING)
    assert spring_stats["win_rate_10"] == 0.5


def _select_outcome():
    from sqlmodel import select

    return select(SignalOutcome)


# --- Baseline/edge: win rate needs a reference point to mean anything ---

def test_get_stats_includes_baseline_and_edge_when_real_candles_exist(session):
    real_candles = _seed_real_candles(session, "FPT", 30)  # steady +1/day uptrend
    signal_outcomes.record_outcomes(
        session, "FPT", Timeframe.DAILY, STRATEGY, real_candles,
        [_event(SPRING, 3, real_candles[3].bucket_start, real_candles[3].close)], BULLISH_EVENTS,
    )

    spring_stats = next(s for s in signal_outcomes.get_stats(session) if s["type"] == SPRING)

    # Steady uptrend -> long-side baseline win rate is 1.0, same as the
    # Spring's own win rate here -> edge is ~0 (the signal doesn't beat just
    # holding anything in a market that only goes up).
    assert spring_stats["baseline_win_rate_5"] == 1.0
    assert spring_stats["edge_5"] == pytest.approx(spring_stats["win_rate_5"] - 1.0)
    assert spring_stats["win_rate_5_ci"] is not None
    low, high = spring_stats["win_rate_5_ci"]
    assert low <= spring_stats["win_rate_5"] <= high


def test_get_stats_baseline_is_none_without_matching_candles(session):
    # record_outcomes was fed in-memory SimpleNamespace candles (never
    # persisted as real Candle rows) -- baseline has nothing to compute from.
    candles = [_candle(i, 100.0 + i) for i in range(30)]
    signal_outcomes.record_outcomes(
        session, "FPT", Timeframe.DAILY, STRATEGY, candles,
        [_event(SPRING, 3, candles[3].bucket_start, candles[3].close)], BULLISH_EVENTS,
    )

    spring_stats = next(s for s in signal_outcomes.get_stats(session) if s["type"] == SPRING)
    assert spring_stats["baseline_win_rate_5"] is None
    assert spring_stats["edge_5"] is None
    # No baseline -> nothing to test significance against.
    assert spring_stats["significant_10"] is None


# --- Multiple-comparisons correction (Benjamini-Hochberg) ---

def test_get_stats_significant_10_true_for_strong_edge_vs_baseline(session, mocker):
    candles = [_candle(i, 100.0 + i) for i in range(40)]  # steady uptrend -> every event wins
    events = [_event(SPRING, i, candles[i].bucket_start, candles[i].close) for i in range(3, 26)]
    signal_outcomes.record_outcomes(session, "FPT", Timeframe.DAILY, STRATEGY, candles, events, BULLISH_EVENTS)

    weak_baseline = {"long_win_rate": 0.3, "short_win_rate": 0.3, "n": 1000}
    mocker.patch.object(
        signal_outcomes, "_pooled_baseline",
        return_value={5: weak_baseline, 10: weak_baseline, 20: weak_baseline},
    )

    spring_stats = next(s for s in signal_outcomes.get_stats(session) if s["type"] == SPRING)
    assert spring_stats["win_rate_10"] == 1.0
    assert spring_stats["significant_10"] is True


def _seed_outcome(session, *, ticker: str, event_ts: datetime, return_10: float, is_bullish: bool = True):
    session.add(
        SignalOutcome(
            ticker=ticker, timeframe=Timeframe.DAILY, strategy=STRATEGY, event_type=SPRING,
            event_ts=event_ts, event_price=100.0, is_bullish=is_bullish, return_10=return_10,
        )
    )


def test_significant_10_false_when_apparent_edge_only_comes_from_overlapping_events(session, mocker):
    # 30 daily events on the SAME ticker, one bar apart -- their horizon-10
    # forward-return windows overlap almost completely, so they are nowhere
    # near 30 independent trials. Naively treating raw n=30 as independent
    # would call a middling 50% win rate "significant" against a weak 30%
    # baseline; declustered (~3 independent observations, since each kept
    # event must be >=10 days from the last) it is not.
    start = datetime(2025, 1, 1)
    for i in range(30):
        _seed_outcome(
            session, ticker="FPT", event_ts=start + timedelta(days=i),
            return_10=0.02 if i % 2 == 0 else 0.0,  # exactly 15 wins / 15 non-wins
        )
    session.commit()

    weak_baseline = {"long_win_rate": 0.3, "short_win_rate": 0.3, "n": 1000}
    mocker.patch.object(
        signal_outcomes, "_pooled_baseline", return_value={5: weak_baseline, 10: weak_baseline, 20: weak_baseline},
    )

    spring_stats = next(s for s in signal_outcomes.get_stats(session) if s["type"] == SPRING)
    assert spring_stats["win_rate_10"] == pytest.approx(0.5)
    assert spring_stats["n_10"] == 30  # point estimate still uses every observation
    assert spring_stats["significant_10"] is False  # but the overlap-corrected p-value doesn't clear alpha


def test_significant_10_still_true_when_events_are_spaced_beyond_the_horizon(session, mocker):
    # Same edge/baseline shape as above, but events are 15 days apart --
    # beyond the horizon-10 overlap window -- so every observation is already
    # independent and declustering shouldn't change the verdict.
    start = datetime(2025, 1, 1)
    for i in range(5):
        _seed_outcome(session, ticker="FPT", event_ts=start + timedelta(days=15 * i), return_10=0.02)
    session.commit()

    weak_baseline = {"long_win_rate": 0.3, "short_win_rate": 0.3, "n": 1000}
    mocker.patch.object(
        signal_outcomes, "_pooled_baseline", return_value={5: weak_baseline, 10: weak_baseline, 20: weak_baseline},
    )

    spring_stats = next(s for s in signal_outcomes.get_stats(session) if s["type"] == SPRING)
    assert spring_stats["win_rate_10"] == 1.0
    assert spring_stats["significant_10"] is True


# --- config_version: separates stats produced under different detector thresholds ---

def test_record_outcomes_stamps_config_version_and_is_immutable(session):
    candles = [_candle(i, 100.0 + i) for i in range(30)]
    event = _event(SPRING, 5, candles[5].bucket_start, candles[5].close)

    signal_outcomes.record_outcomes(
        session, "FPT", Timeframe.DAILY, STRATEGY, candles, [event], BULLISH_EVENTS, config_version="wyckoff:v1",
    )
    row = session.exec(_select_outcome()).first()
    assert row.config_version == "wyckoff:v1"

    # Same event, later run under a different version -- must not rewrite
    # the already-created row's tag.
    signal_outcomes.record_outcomes(
        session, "FPT", Timeframe.DAILY, STRATEGY, candles, [event], BULLISH_EVENTS, config_version="wyckoff:v2",
    )
    row_again = session.exec(_select_outcome()).first()
    assert row_again.config_version == "wyckoff:v1"


def test_get_stats_n_current_config_counts_only_matching_rows(session):
    candles = [_candle(i, 100.0 + i) for i in range(40)]
    old_event = _event(SPRING, 3, candles[3].bucket_start, candles[3].close)
    new_event = _event(SPRING, 10, candles[10].bucket_start, candles[10].close)
    signal_outcomes.record_outcomes(
        session, "FPT", Timeframe.DAILY, STRATEGY, candles, [old_event], BULLISH_EVENTS, config_version="wyckoff:old",
    )
    signal_outcomes.record_outcomes(
        session, "FPT", Timeframe.DAILY, STRATEGY, candles, [new_event], BULLISH_EVENTS, config_version="wyckoff:new",
    )

    without_version = next(s for s in signal_outcomes.get_stats(session) if s["type"] == SPRING)
    assert "n_current_config" not in without_version

    with_version = next(
        s for s in signal_outcomes.get_stats(session, current_config_version="wyckoff:new") if s["type"] == SPRING
    )
    assert with_version["count"] == 2
    assert with_version["n_current_config"] == 1


def test_get_stats_filters_by_asset_class(session):
    session.add(Symbol(ticker="FPT", asset_class=AssetClass.STOCK))
    session.add(Symbol(ticker="BITCOIN", asset_class=AssetClass.CRYPTO))
    session.commit()

    candles = [_candle(i, 100.0 + i) for i in range(30)]
    signal_outcomes.record_outcomes(
        session, "FPT", Timeframe.DAILY, STRATEGY, candles,
        [_event(SPRING, 3, candles[3].bucket_start, candles[3].close)], BULLISH_EVENTS,
    )
    signal_outcomes.record_outcomes(
        session, "BITCOIN", Timeframe.DAILY, STRATEGY, candles,
        [_event(SPRING, 3, candles[3].bucket_start, candles[3].close)], BULLISH_EVENTS,
    )

    stock_count = sum(s["count"] for s in signal_outcomes.get_stats(session, asset_class=AssetClass.STOCK))
    crypto_count = sum(s["count"] for s in signal_outcomes.get_stats(session, asset_class=AssetClass.CRYPTO))
    all_count = sum(s["count"] for s in signal_outcomes.get_stats(session))
    assert stock_count == 1
    assert crypto_count == 1
    assert all_count == 2
