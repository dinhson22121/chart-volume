import types

from app.services.baseline import compute_baseline, wilson_ci


def _candle(close: float):
    return types.SimpleNamespace(close=close)


def test_compute_baseline_long_win_rate_reflects_steady_uptrend():
    # Every bar rises by 1 -> every horizon clears the 1% WIN_THRESHOLD on the
    # long side well before the horizon is large, and never on the short side.
    candles = [_candle(100.0 + i) for i in range(40)]

    result = compute_baseline(candles, horizons=(5, 10, 20))

    for horizon in (5, 10, 20):
        stat = result[horizon]
        assert stat["n"] == 40 - horizon
        assert stat["long_win_rate"] == 1.0
        assert stat["short_win_rate"] == 0.0
        assert stat["long_wins"] == stat["n"]
        assert stat["short_wins"] == 0


def test_compute_baseline_flat_series_has_no_wins_either_direction():
    candles = [_candle(100.0) for i in range(30)]

    result = compute_baseline(candles, horizons=(5,))

    assert result[5]["long_win_rate"] == 0.0
    assert result[5]["short_win_rate"] == 0.0


def test_compute_baseline_none_rate_when_no_bars_reach_the_horizon():
    candles = [_candle(100.0 + i) for i in range(3)]

    result = compute_baseline(candles, horizons=(5,))

    assert result[5]["n"] == 0
    assert result[5]["long_win_rate"] is None
    assert result[5]["short_win_rate"] is None


def test_wilson_ci_none_when_no_samples():
    assert wilson_ci(0, 0) is None


def test_wilson_ci_contains_the_point_estimate():
    low, high = wilson_ci(30, 100)
    assert low <= 0.30 <= high
    assert 0.0 <= low <= high <= 1.0


def test_wilson_ci_narrows_as_sample_size_grows():
    small_low, small_high = wilson_ci(30, 100)
    large_low, large_high = wilson_ci(300, 1000)  # same 30% rate, 10x the sample

    assert (large_high - large_low) < (small_high - small_low)
