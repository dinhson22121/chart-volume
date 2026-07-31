"""User-tunable thresholds for the Smart Money Concept (SMC) detectors."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SMCConfig:
    # bars on each side required to confirm a swing high/low (fractal).
    # Swept against this app's own train/holdout data (scripts/optimize_smc.py,
    # VN30, 2026-07-31): every one of the top 5 opt-window candidates raised
    # this from the original 2 to 3-4, each with a bootstrap CI entirely
    # positive at BOTH opt and holdout windows (the 2-default's own opt-window
    # CI crossed zero) -- consistent enough across candidates to not be one
    # lucky combination. 4 (paired with major_swing_lookback=20 below) was the
    # safer of the two similarly-strong picks, changing only one parameter.
    swing_lookback: int = 4
    # Second, longer-horizon structure tier (see app.smc.events' module
    # docstring): LuxAlgo runs two independent structure passes -- a fast
    # "internal" one (its own default: 5) and a much slower "swing"/major one
    # (its own default: 50). This app's ORIGINAL swing_lookback=2 default was
    # already closer to LuxAlgo's internal tier than to a real major-structure
    # one, so rather than change its long-tested meaning, this adds the
    # missing MAJOR tier on top -- 20 matches this app's own established
    # "swing lookback" convention used elsewhere (trade_scenario.
    # LEVELS_LOOKBACK, app.wyckoff.indicators.RANGE_LOOKBACK, this module's
    # own _SWING_LOOKBACK_LEVELS), not LuxAlgo's 50.
    major_swing_lookback: int = 20
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
