"""User-tunable thresholds for the Smart Money Concept (SMC) detectors."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SMCConfig:
    swing_lookback: int = 2  # bars on each side required to confirm a swing high/low (fractal)
    ob_lookback_bars: int = 10  # max bars to look back for the order-block candle at a BOS
    fvg_min_gap_mult: float = 0.3  # a fair value gap must be >= x * average spread to count
    # An order-block anchor candle whose own (high-low) spread is >= this many
    # times the rolling spread_ma is skipped -- a wick/spike outlier bar makes
    # a nonsensical, far-too-wide OB zone (matches LuxAlgo's own
    # highVolatilityBar filter, reusing spread_ma rather than introducing a
    # second volatility measure).
    ob_volatility_mult: float = 2.0
    # Equal Highs/Lows: two consecutive same-type swing pivots within this
    # many times spread_ma of each other are flagged as one liquidity pool
    # (matches LuxAlgo's equalHighsLowsThreshold default of 0.1). Reuses the
    # main swing_lookback/spread_ma rather than LuxAlgo's separate
    # shorter-window pivot pass + true ATR(200) -- one fewer independent
    # parameter pair, at the cost of not being a perfectly faithful port.
    eq_threshold_mult: float = 0.1


DEFAULT_CONFIG = SMCConfig()
