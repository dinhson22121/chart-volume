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
    # Kaufman-style efficiency ratio (net move / total path length) over the
    # `range_lookback` bars *preceding* the current one -- <= this counts as
    # "ranging" (a real trading range, not a trend). All 8 base detectors
    # require this before matching: SC/BC/Spring/Upthrust/SOS/SOW/NoDemand/
    # NoSupply are only meaningful against an established range, and without
    # this gate a rolling 20-bar min/max fires on ordinary trend pullbacks too
    # -- that part of the reasoning is architectural, not backtest-derived,
    # and still holds regardless of the exact threshold value below.
    #
    # A same-day sweep (scripts/optimize_wyckoff_range_gate.py) initially
    # reported 0.15 as clearly better than this 0.35 starting guess -- but
    # that sweep (like every optimize_wyckoff*.py script run that day) scored
    # scenario_backtest.walk_events() output WITHOUT filtering to
    # scenario.is_bullish, silently mixing in untradeable bearish/short
    # R-multiples (the live app is spot/long-only; trade_scenario.
    # _create_scenarios never creates a bearish scenario). Re-measured
    # bullish-only (scripts/diagnose_wyckoff_bullish_only.py) on the SAME
    # VN30 data: 0.35 holdout mean_r=-0.513 win_rate=22.6% n=62 vs 0.15
    # mean_r=-0.484 win_rate=21.6% n=37 -- statistically indistinguishable,
    # and 0.15 has ~40% fewer trading opportunities for no real benefit. The
    # apparent improvement was entirely an artifact of that bug. Reverted to
    # 0.35 as the neutral, non-overfit starting value; don't re-adopt a
    # different threshold without re-sweeping through a script that filters
    # `scenario.is_bullish` before scoring.
    trend_efficiency_max: float = 0.35
    vp_lookback_bars: int = 50  # bars spanning the volume profile window
    vp_bins: int = 24  # number of equal-width price bins across that window
    vp_value_area_pct: float = 0.7  # fraction of window volume the value area must cover


DEFAULT_CONFIG = WyckoffConfig()
