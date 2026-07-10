"""User-tunable thresholds for the Wyckoff detectors."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WyckoffConfig:
    climax_vol_mult: float = 2.0  # volume >= x * average -> climactic
    wide_spread_mult: float = 1.5  # spread >= x * average -> wide bar
    narrow_spread_mult: float = 0.7  # spread <= x * average -> narrow bar
    low_vol_mult: float = 0.7  # volume <= x * average -> low volume
    sos_vol_mult: float = 1.5  # breakout volume threshold for SOS/SOW
    lps_lookback_bars: int = 10  # bars to wait after SOS/SOW for a pullback (LPS/LPSY)


DEFAULT_CONFIG = WyckoffConfig()
