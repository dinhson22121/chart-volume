"""Feature engineering for the SMC (Smart Money Concept) engine: swing
points (fractals) + spread stats used to size a Fair Value Gap.

All functions are pure and operate on a DataFrame with columns
``time, open, high, low, close, volume`` (same shape as the other engines).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from numpy.lib.stride_tricks import sliding_window_view

from app.smc.config import DEFAULT_CONFIG, SMCConfig

SPREAD_MA_LEN = 20
_MIN_PERIODS = 3


def _pivot_flags(values: np.ndarray, lookback: int, *, is_high: bool) -> np.ndarray:
    """Fractal pivots for a whole series at once: bar ``i`` qualifies when it
    strictly exceeds (or undercuts) EVERY bar in the ``lookback`` window on
    each side -- which is the same as comparing it against each window's own
    max (or min), and that is what turns a per-bar Python loop into a single
    vectorized pass.

    ``sliding_window_view(values, lookback)[j]`` is ``values[j:j+lookback]``,
    so bar i's left window is row ``i - lookback`` and its right window is row
    ``i + 1``. Only bars with a full window on both sides can qualify, which
    is exactly the ``[lookback, n - lookback)`` range -- the same lagging
    confirmation the per-bar version had, unchanged.
    """
    n = len(values)
    flags = np.zeros(n, dtype=bool)
    if lookback <= 0 or n <= 2 * lookback:
        return flags  # not enough bars for a single full window on both sides
    windows = sliding_window_view(values, lookback)
    extreme = windows.max(axis=1) if is_high else windows.min(axis=1)
    idx = np.arange(lookback, n - lookback)
    pivot, left, right = values[idx], extreme[idx - lookback], extreme[idx + 1]
    flags[idx] = (pivot > left) & (pivot > right) if is_high else (pivot < left) & (pivot < right)
    return flags


def compute_features(df: pd.DataFrame, cfg: SMCConfig = DEFAULT_CONFIG) -> pd.DataFrame:
    """Adds: spread, spread_ma, swing_high, swing_low, major_swing_high,
    major_swing_low.

    A swing high/low at bar ``i`` is only confirmed once the relevant
    lookback's worth of bars exist on both sides of it (a fractal) -- like
    LPS/LPSY or an FVG's middle candle, this is a naturally lagging signal:
    it can't be known in real time until later bars close. major_swing_*
    uses ``cfg.major_swing_lookback`` (a longer horizon) instead of
    ``cfg.swing_lookback`` -- see SMCConfig's own docstring on the two tiers.
    """
    out = df.copy().reset_index(drop=True)

    out["spread"] = out["high"] - out["low"]
    out["spread_ma"] = out["spread"].rolling(SPREAD_MA_LEN, min_periods=_MIN_PERIODS).mean()

    highs = out["high"].to_numpy()
    lows = out["low"].to_numpy()

    # Three independent pivot passes at different horizons. The third feeds
    # Liquidity Sweep detection only (see app.smc.events._detect_liquidity_sweeps)
    # and keeps its own lookback, unrelated to the two structure tiers.
    for prefix, lookback in (
        ("", cfg.swing_lookback),
        ("major_", cfg.major_swing_lookback),
        ("sweep_", cfg.sweep_lookback),
    ):
        out[f"{prefix}swing_high"] = _pivot_flags(highs, lookback, is_high=True)
        out[f"{prefix}swing_low"] = _pivot_flags(lows, lookback, is_high=False)

    return out
