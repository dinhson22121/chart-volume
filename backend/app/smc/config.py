"""User-tunable thresholds for the Smart Money Concept (SMC) detectors."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SMCConfig:
    swing_lookback: int = 2  # bars on each side required to confirm a swing high/low (fractal)
    ob_lookback_bars: int = 10  # max bars to look back for the order-block candle at a BOS
    fvg_min_gap_mult: float = 0.3  # a fair value gap must be >= x * average spread to count


DEFAULT_CONFIG = SMCConfig()
