from datetime import datetime, timedelta

import pytest

from app.services.stats_significance import (
    benjamini_hochberg,
    bootstrap_sharpe_ci,
    effective_n,
    monte_carlo_permutation_test,
    one_sample_p_value,
    walk_forward_analysis,
)


def test_p_value_none_when_inputs_missing():
    assert one_sample_p_value(None, 0.3, 50) is None
    assert one_sample_p_value(0.5, None, 50) is None
    assert one_sample_p_value(0.5, 0.3, 0) is None


def test_p_value_none_for_degenerate_baseline():
    assert one_sample_p_value(0.5, 0.0, 50) is None
    assert one_sample_p_value(0.5, 1.0, 50) is None


def test_p_value_small_when_win_rate_far_from_baseline_with_large_n():
    # 80% win rate vs 30% baseline over 200 trials -- overwhelming evidence.
    p = one_sample_p_value(0.8, 0.3, 200)
    assert p is not None
    assert p < 0.001


def test_p_value_large_when_win_rate_close_to_baseline_or_n_tiny():
    p = one_sample_p_value(0.32, 0.3, 5)
    assert p is not None
    assert p > 0.5


def test_p_value_symmetric_for_over_and_under_performance():
    over = one_sample_p_value(0.5, 0.3, 100)
    under = one_sample_p_value(0.1, 0.3, 100)
    assert over == pytest.approx(under)


def test_benjamini_hochberg_empty_input():
    assert benjamini_hochberg([]) == []


def test_benjamini_hochberg_all_none_stays_none():
    assert benjamini_hochberg([None, None]) == [None, None]


def test_benjamini_hochberg_none_entries_pass_through_untested():
    result = benjamini_hochberg([0.001, None, 0.9])
    assert result[1] is None


def test_benjamini_hochberg_flags_strong_signal_among_weak_ones():
    # One p-value far below alpha, several near 1 -- the strong one should
    # survive correction, the weak ones should not.
    result = benjamini_hochberg([0.0001, 0.8, 0.9, 0.95, 0.99])
    assert result[0] is True
    assert all(r is False for r in result[1:])


def test_benjamini_hochberg_uniform_weak_p_values_all_fail():
    result = benjamini_hochberg([0.4, 0.5, 0.6, 0.7])
    assert all(r is False for r in result)


def test_benjamini_hochberg_preserves_input_order():
    # Worst p-value first, best last -- output must still align by index,
    # not by sorted rank.
    result = benjamini_hochberg([0.9, 0.0001])
    assert result[0] is False
    assert result[1] is True


# --- effective_n: declustering overlapping-window observations ---

def test_effective_n_counts_widely_spaced_events_as_independent():
    ts = {"FPT": [datetime(2025, 1, 1), datetime(2025, 1, 20)]}  # 19 days apart
    assert effective_n(ts, horizon_bars=10, bar_duration=timedelta(days=1)) == 2


def test_effective_n_declusters_events_closer_than_the_horizon_window():
    # 4 days apart but horizon=10 bars means their forward-return windows
    # overlap -- only one counts as an independent observation.
    ts = {"FPT": [datetime(2025, 1, 1), datetime(2025, 1, 5)]}
    assert effective_n(ts, horizon_bars=10, bar_duration=timedelta(days=1)) == 1


def test_effective_n_boundary_gap_exactly_horizon_counts_as_independent():
    ts = {"FPT": [datetime(2025, 1, 1), datetime(2025, 1, 11)]}  # exactly 10 days
    assert effective_n(ts, horizon_bars=10, bar_duration=timedelta(days=1)) == 2


def test_effective_n_does_not_decluster_across_different_tickers():
    # Two different tickers' price series are independent of each other even
    # if their events happen to land on the same date.
    ts = {"FPT": [datetime(2025, 1, 1)], "VCB": [datetime(2025, 1, 1)]}
    assert effective_n(ts, horizon_bars=10, bar_duration=timedelta(days=1)) == 2


def test_effective_n_thins_a_dense_run_by_hopping_not_collapsing_to_one():
    # 31 daily events, horizon=10 -> kept at day 0, 10, 20, 30 -> 4 independent
    # observations, not 1 (a naive "keep only the first" would undercount).
    ts = {"FPT": [datetime(2025, 1, 1) + timedelta(days=i) for i in range(31)]}
    assert effective_n(ts, horizon_bars=10, bar_duration=timedelta(days=1)) == 4


def test_effective_n_empty_input_is_zero():
    assert effective_n({}, horizon_bars=10, bar_duration=timedelta(days=1)) == 0


# --- monte_carlo_permutation_test: is the realized trade ORDER (not the mean
# R-multiple, which shuffling can't change) unusually smooth/rough vs a
# random reshuffle of the same set of wins/losses? ---

def test_monte_carlo_returns_none_below_three_trades():
    assert monte_carlo_permutation_test([1.0, -1.0], risk_amount=1000, notional_capital=100_000) is None
    assert monte_carlo_permutation_test([], risk_amount=1000, notional_capital=100_000) is None


def test_monte_carlo_is_reproducible_with_the_same_seed():
    r_multiples = [2.0, -1.0, 1.5, -0.5, 3.0, -2.0, 0.5]
    first = monte_carlo_permutation_test(r_multiples, risk_amount=1000, notional_capital=100_000, seed=7)
    second = monte_carlo_permutation_test(r_multiples, risk_amount=1000, notional_capital=100_000, seed=7)
    assert first == second


def test_monte_carlo_different_seeds_can_differ():
    r_multiples = [2.0, -1.0, 1.5, -0.5, 3.0, -2.0, 0.5, -1.5, 2.5, -0.75]
    a = monte_carlo_permutation_test(r_multiples, risk_amount=1000, notional_capital=100_000, seed=1)
    b = monte_carlo_permutation_test(r_multiples, risk_amount=1000, notional_capital=100_000, seed=2)
    assert a["p_value_r_sharpe"] != b["p_value_r_sharpe"] or a["p_value_max_drawdown_r"] != b["p_value_max_drawdown_r"]


def test_monte_carlo_output_shape_is_well_formed():
    r_multiples = [2.0, -1.0, 1.5, -0.5, 3.0, -2.0, 0.5]
    result = monte_carlo_permutation_test(
        r_multiples, risk_amount=1000, notional_capital=100_000, n_simulations=200, seed=1
    )
    assert result["n_simulations"] == 200
    assert result["n_trades"] == len(r_multiples)
    assert 0.0 <= result["p_value_r_sharpe"] <= 1.0
    assert 0.0 <= result["p_value_max_drawdown_r"] <= 1.0
    assert result["actual_max_drawdown_r"] >= 0.0
    assert result["simulated_r_sharpe_p5"] <= result["simulated_r_sharpe_mean"] <= result["simulated_r_sharpe_p95"]


def test_monte_carlo_flags_clustered_losses_as_the_worst_possible_drawdown():
    # Wins clustered first, then losses clustered last: the peak (4) is
    # reached using every +1 before either -3 lands, then both losses erase
    # it in one stretch -- mathematically the DEEPEST drawdown this exact
    # multiset can produce (no ordering can reach a higher peak or a lower
    # post-peak trough). Every reshuffle is therefore "at least as good"
    # (<=) as this one; nearly none can do worse.
    r_multiples = [1.0, 1.0, 1.0, 1.0, -3.0, -3.0]

    result = monte_carlo_permutation_test(
        r_multiples, risk_amount=1000, notional_capital=100_000, n_simulations=500, seed=3
    )

    assert result["actual_max_drawdown_r"] == pytest.approx(6.0)
    # High p-value here is the UNFAVORABLE reading for this metric: it means
    # almost every reshuffle matched or beat (shallower than) the actual
    # drawdown -- i.e. the realized path is the unusually BAD case, not a
    # reassuring one. See the function's own docstring for this direction.
    assert result["p_value_max_drawdown_r"] > 0.9


def test_r_sharpe_handles_zero_variance_without_raising():
    from app.services.stats_significance import _r_sharpe

    # Identical returns -- stdev is exactly 0, would divide by zero without
    # a guard. (Note: equal R-multiples do NOT produce equal equity-curve
    # returns once compounded through a growing account -- this checks the
    # guard directly on the returns themselves, not through the public API.)
    assert _r_sharpe([0.01, 0.01, 0.01, 0.01]) == 0.0


# --- bootstrap_sharpe_ci: how much would r_sharpe wobble on a fresh sample
# of the same size, if returns are treated as i.i.d. draws? ---

def test_bootstrap_returns_none_below_five_trades():
    assert bootstrap_sharpe_ci([1.0, -1.0, 1.0, -1.0], risk_amount=1000, notional_capital=100_000) is None


def test_bootstrap_is_reproducible_with_the_same_seed():
    r_multiples = [2.0, -1.0, 1.5, -0.5, 3.0, -2.0, 0.5]
    first = bootstrap_sharpe_ci(r_multiples, risk_amount=1000, notional_capital=100_000, seed=5)
    second = bootstrap_sharpe_ci(r_multiples, risk_amount=1000, notional_capital=100_000, seed=5)
    assert first == second


def test_bootstrap_output_shape_is_well_formed():
    r_multiples = [2.0, -1.0, 1.5, -0.5, 3.0, -2.0, 0.5]
    result = bootstrap_sharpe_ci(
        r_multiples, risk_amount=1000, notional_capital=100_000, n_bootstrap=200, seed=1
    )
    assert result["n_bootstrap"] == 200
    assert result["n_trades"] == len(r_multiples)
    assert result["confidence"] == 0.95
    assert 0.0 <= result["prob_positive"] <= 1.0
    # The median of the bootstrap distribution always sits between its own
    # percentiles, by construction.
    assert result["ci_lower"] <= result["median_r_sharpe"] <= result["ci_upper"]


def test_bootstrap_prob_positive_is_high_when_every_trade_wins():
    # Every trade a win (varying size, so returns actually have variance) --
    # any resample of an all-positive set is itself all-positive, so nearly
    # every bootstrap draw should show a positive r_sharpe.
    r_multiples = [1.0, 2.0, 1.5, 3.0, 0.5, 2.5, 1.0, 2.0]
    result = bootstrap_sharpe_ci(
        r_multiples, risk_amount=1000, notional_capital=100_000, n_bootstrap=300, seed=1
    )
    assert result["prob_positive"] >= 0.95
    assert result["ci_lower"] > 0


def test_bootstrap_prob_positive_is_low_when_every_trade_loses():
    r_multiples = [-1.0, -2.0, -1.5, -3.0, -0.5, -2.5, -1.0, -2.0]
    result = bootstrap_sharpe_ci(
        r_multiples, risk_amount=1000, notional_capital=100_000, n_bootstrap=300, seed=1
    )
    assert result["prob_positive"] <= 0.05
    assert result["ci_upper"] < 0


# --- walk_forward_analysis: does the edge hold up across sequential time
# windows, or is it carried entirely by one lucky stretch? ---

def test_walk_forward_returns_none_with_too_few_trades_for_the_window_count():
    # 5 trades can't fill 5 windows with >=2 trades each.
    assert walk_forward_analysis([1.0, -1.0, 1.0, -1.0, 1.0], n_windows=5) is None


def test_walk_forward_splits_evenly_divisible_trades_into_equal_windows():
    r_multiples = [1.0, 1.0, -1.0, -1.0, 2.0, 2.0, -2.0, -2.0, 3.0, 3.0]  # 10 trades, 5 windows of 2
    result = walk_forward_analysis(r_multiples, n_windows=5)
    assert result["n_windows"] == 5
    assert [w["n_trades"] for w in result["per_window"]] == [2, 2, 2, 2, 2]
    assert sum(w["n_trades"] for w in result["per_window"]) == len(r_multiples)


def test_walk_forward_handles_uneven_division_without_dropping_trades():
    r_multiples = [float(i) for i in range(11)]  # 11 trades, 5 windows
    result = walk_forward_analysis(r_multiples, n_windows=5)
    assert sum(w["n_trades"] for w in result["per_window"]) == 11
    assert all(w["n_trades"] >= 2 for w in result["per_window"])


def test_walk_forward_consistency_ratio_counts_positive_windows():
    # Windows: [+1,+1]=+1, [+1,+1]=+1, [-1,-1]=-1, [+1,+1]=+1, [-1,-1]=-1
    # -> 3 of 5 windows positive.
    r_multiples = [1, 1, 1, 1, -1, -1, 1, 1, -1, -1]
    result = walk_forward_analysis([float(r) for r in r_multiples], n_windows=5)
    assert result["positive_windows"] == 3
    assert result["consistency_ratio"] == pytest.approx(0.6)


def test_walk_forward_all_positive_windows_gives_full_consistency():
    r_multiples = [1.0, 2.0, 1.0, 2.0, 1.0, 2.0, 1.0, 2.0, 1.0, 2.0]
    result = walk_forward_analysis(r_multiples, n_windows=5)
    assert result["positive_windows"] == 5
    assert result["consistency_ratio"] == pytest.approx(1.0)
    assert result["expectancy_std_across_windows"] == pytest.approx(0.0)
