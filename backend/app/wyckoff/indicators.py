"""Price-volume feature engineering for the Wyckoff engine.

All functions are pure and operate on a DataFrame with columns
``time, open, high, low, close, volume``.
"""

from __future__ import annotations

import pandas as pd

VOL_MA_LEN = 20
SPREAD_MA_LEN = 20
RANGE_LOOKBACK = 20
_MIN_PERIODS = 3


def compute_features(
    df: pd.DataFrame,
    vol_ma_len: int = VOL_MA_LEN,
    spread_ma_len: int = SPREAD_MA_LEN,
    range_lookback: int = RANGE_LOOKBACK,
) -> pd.DataFrame:
    """Add derived columns used by the event detectors.

    Adds: spread, vol_ma, spread_ma, vol_ratio, spread_ratio, close_loc,
    prev_close, support, resistance. ``support``/``resistance`` are the rolling
    min-low / max-high of *prior* bars (shifted by 1) so the current bar can
    break the established range (spring / upthrust).
    """
    out = df.copy().reset_index(drop=True)

    out["spread"] = out["high"] - out["low"]
    out["vol_ma"] = out["volume"].rolling(vol_ma_len, min_periods=_MIN_PERIODS).mean()
    out["spread_ma"] = out["spread"].rolling(spread_ma_len, min_periods=_MIN_PERIODS).mean()
    out["vol_ratio"] = out["volume"] / out["vol_ma"]
    out["spread_ratio"] = out["spread"] / out["spread_ma"]

    # Where a bar has zero range, treat close as mid-bar to avoid div-by-zero.
    rng = out["spread"].replace(0, pd.NA)
    out["close_loc"] = ((out["close"] - out["low"]) / rng).fillna(0.5)

    out["prev_close"] = out["close"].shift(1)
    out["support"] = out["low"].rolling(range_lookback, min_periods=_MIN_PERIODS).min().shift(1)
    out["resistance"] = out["high"].rolling(range_lookback, min_periods=_MIN_PERIODS).max().shift(1)

    return out


def latest_levels(df: pd.DataFrame, range_lookback: int = RANGE_LOOKBACK) -> tuple[float, float]:
    """Support/resistance of the most recent ``range_lookback`` bars (inclusive)."""
    window = df.iloc[-range_lookback:]
    return float(window["low"].min()), float(window["high"].max())
