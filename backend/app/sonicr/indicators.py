"""Sonic R feature engineering: Dragon EMA, T3 (Tillson), CCI.

All functions are pure and operate on a DataFrame with columns
``time, open, high, low, close, volume`` (same shape as the Wyckoff engine).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from app.sonicr.config import DEFAULT_CONFIG, SonicRConfig


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def t3(series: pd.Series, period: int, vfactor: float) -> pd.Series:
    """Tillson's T3: a 6-pass EMA chain blended to reduce lag vs a plain EMA.

    e1..e6 are successive EMAs of the same period, each applied to the
    previous pass's output; the blend coefficients come from expanding
    ``GD(x) = EMA(x)*(1+vfactor) - EMA(EMA(x))*vfactor`` applied 3 times.
    """
    e1 = ema(series, period)
    e2 = ema(e1, period)
    e3 = ema(e2, period)
    e4 = ema(e3, period)
    e5 = ema(e4, period)
    e6 = ema(e5, period)

    a = vfactor
    c1 = -(a**3)
    c2 = 3 * a**2 + 3 * a**3
    c3 = -6 * a**2 - 3 * a - 3 * a**3
    c4 = 1 + 3 * a + a**3 + 3 * a**2

    return c1 * e6 + c2 * e5 + c3 * e4 + c4 * e3


def cci(df: pd.DataFrame, period: int) -> pd.Series:
    """Woody-style CCI: (typical price - SMA) / (0.015 * mean absolute deviation)."""
    tp = (df["high"] + df["low"] + df["close"]) / 3
    sma = tp.rolling(period, min_periods=period).mean()
    mad = tp.rolling(period, min_periods=period).apply(lambda x: np.abs(x - x.mean()).mean(), raw=True)
    return (tp - sma) / (0.015 * mad)


def compute_features(df: pd.DataFrame, cfg: SonicRConfig = DEFAULT_CONFIG) -> pd.DataFrame:
    """Add derived columns used by the event detectors: dragon, t3_fast,
    t3_slow, cci_fast, cci_slow."""
    out = df.copy().reset_index(drop=True)

    out["dragon"] = ema(out["close"], cfg.dragon_period)
    out["t3_fast"] = t3(out["close"], cfg.t3_fast_period, cfg.t3_vfactor)
    out["t3_slow"] = t3(out["close"], cfg.t3_slow_period, cfg.t3_vfactor)
    out["cci_fast"] = cci(out, cfg.cci_fast_period)
    out["cci_slow"] = cci(out, cfg.cci_slow_period)

    return out
