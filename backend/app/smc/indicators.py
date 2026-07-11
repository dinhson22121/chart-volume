"""Feature engineering for the SMC (Smart Money Concept) engine: swing
points (fractals) + spread stats used to size a Fair Value Gap.

All functions are pure and operate on a DataFrame with columns
``time, open, high, low, close, volume`` (same shape as the other engines).
"""

from __future__ import annotations

import pandas as pd

from app.smc.config import DEFAULT_CONFIG, SMCConfig

SPREAD_MA_LEN = 20
_MIN_PERIODS = 3


def _is_swing_high(highs: pd.Series, i: int, lookback: int) -> bool:
    pivot = highs.iloc[i]
    left = highs.iloc[i - lookback : i]
    right = highs.iloc[i + 1 : i + 1 + lookback]
    return bool((pivot > left).all() and (pivot > right).all())


def _is_swing_low(lows: pd.Series, i: int, lookback: int) -> bool:
    pivot = lows.iloc[i]
    left = lows.iloc[i - lookback : i]
    right = lows.iloc[i + 1 : i + 1 + lookback]
    return bool((pivot < left).all() and (pivot < right).all())


def compute_features(df: pd.DataFrame, cfg: SMCConfig = DEFAULT_CONFIG) -> pd.DataFrame:
    """Adds: spread, spread_ma, swing_high, swing_low.

    A swing high/low at bar ``i`` is only confirmed once ``cfg.swing_lookback``
    bars exist on both sides of it (a fractal) -- like LPS/LPSY or an FVG's
    middle candle, this is a naturally lagging signal: it can't be known in
    real time until later bars close.
    """
    out = df.copy().reset_index(drop=True)
    n = len(out)
    lookback = cfg.swing_lookback

    out["spread"] = out["high"] - out["low"]
    out["spread_ma"] = out["spread"].rolling(SPREAD_MA_LEN, min_periods=_MIN_PERIODS).mean()

    swing_high = [False] * n
    swing_low = [False] * n
    for i in range(lookback, n - lookback):
        swing_high[i] = _is_swing_high(out["high"], i, lookback)
        swing_low[i] = _is_swing_low(out["low"], i, lookback)
    out["swing_high"] = swing_high
    out["swing_low"] = swing_low

    return out
