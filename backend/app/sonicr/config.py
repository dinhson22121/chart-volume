"""User-tunable thresholds for the Sonic R detectors."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SonicRConfig:
    dragon_period: int = 34  # EMA period for the main trend filter ("Dragon")
    t3_fast_period: int = 5
    t3_slow_period: int = 8
    t3_vfactor: float = 0.7
    cci_fast_period: int = 6
    cci_slow_period: int = 14
    pullback_lookback_bars: int = 10  # bars to wait after a Sonic cross for a pullback entry (mirrors lps_lookback_bars)


DEFAULT_CONFIG = SonicRConfig()
