"""Fractal swing-point detection for the SMC engine."""

import pandas as pd

from app.smc.config import SMCConfig
from app.smc.indicators import compute_features


def _bars_to_df(values):
    n = len(values)
    return pd.DataFrame(
        {
            "time": pd.date_range("2025-01-01", periods=n, freq="D"),
            "open": [v - 0.2 for v in values],
            "high": [v + 0.5 for v in values],
            "low": [v - 0.5 for v in values],
            "close": values,
            "volume": [1000.0] * n,
        }
    )


def test_confirms_swing_high_and_low_at_a_zigzag_peak_and_trough():
    # down to a trough at idx 5, up to a peak at idx 10, down again.
    values = [110, 108, 106, 104, 102, 100, 102, 104, 106, 108, 110, 108, 106, 104, 102]
    feat = compute_features(_bars_to_df(values), SMCConfig(swing_lookback=2))

    assert feat.index[feat["swing_low"]].tolist() == [5]
    assert feat.index[feat["swing_high"]].tolist() == [10]


def test_swing_lookback_controls_how_many_bars_must_confirm_it():
    # A shallow 1-bar wiggle should confirm at lookback=1 but not at lookback=3
    # (not enough bars on either side share the required strict ordering).
    values = [100, 99, 101, 99, 100]  # peak at idx 2 (101), only 1 bar of clearance each side
    feat_1 = compute_features(_bars_to_df(values), SMCConfig(swing_lookback=1))
    feat_3 = compute_features(_bars_to_df(values), SMCConfig(swing_lookback=3))

    assert feat_1["swing_high"].iloc[2] == True  # noqa: E712
    assert not feat_3["swing_high"].any()  # too short a series to confirm at lookback=3


def test_no_swing_points_on_a_pure_monotonic_move():
    values = [100 + i for i in range(15)]  # straight uptrend, no local peak/trough
    feat = compute_features(_bars_to_df(values), SMCConfig(swing_lookback=2))

    assert not feat["swing_high"].any()
    assert not feat["swing_low"].any()


def test_spread_ma_is_nan_until_min_periods_reached():
    values = [100, 101, 102]
    feat = compute_features(_bars_to_df(values), SMCConfig())

    assert pd.isna(feat["spread_ma"].iloc[0])
    assert pd.isna(feat["spread_ma"].iloc[1])
    assert not pd.isna(feat["spread_ma"].iloc[2])  # _MIN_PERIODS = 3
