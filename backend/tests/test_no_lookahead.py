"""Point-in-time / no-repaint regression guard for all 3 analysis engines.

Investigated after a user question: "is the win-rate/expectancy stats
trustworthy, or could a detector be scoring itself with information it
wouldn't have had in real time?" (signal_outcomes/trade_scenario compute
forward returns from whatever `events` a run produces, so a detector that
silently changes its mind about a *past* bar once more future candles exist
would inflate every stat derived from it.)

By-hand analysis of the one detector that looked suspicious --
app.smc.indicators._is_swing_high/_is_swing_low, which confirms a swing using
a lookback window on BOTH sides (including bars after the pivot) -- showed
this is actually safe: a breakout can only reference a swing at index j once
real time has reached at least j + swing_lookback + 1, because any earlier bar
is *itself* part of the swing's own confirmation window and is therefore
constrained to be below/above the pivot, which is incompatible with also being
the bar that breaks past it. This test turns that by-hand proof into an
executable invariant so a future refactor (different rolling window, a changed
confirmation condition, a new lagging construct) can't silently reintroduce
repaint without a test failing.
"""

from __future__ import annotations

import random
import types

import pandas as pd
import pytest

from app.smc.config import SMCConfig
from app.sonicr.config import SonicRConfig
from app.strategies import registry as strategy_registry
from app.wyckoff.config import WyckoffConfig

N_BARS = 80
# Comfortably covers every strategy's own lagging-confirmation window (Wyckoff
# LPS/LPSY lps_lookback_bars=10 default, SMC swing_lookback=2 default, SonicR
# pullback_lookback_bars=10 default) -- events at or before this many bars shy
# of a prefix's own end are expected to already be fully settled.
SAFE_MARGIN = 15

_STRATEGY_CONFIGS = {
    "wyckoff": WyckoffConfig(),
    "smc": SMCConfig(),
    "sonicr": SonicRConfig(),
}


def _make_candles(n: int, seed: int = 7) -> list:
    """Deterministic pseudo-random walk with enough volatility to trigger a
    variety of events across all 3 engines (climaxes, springs/upthrusts,
    BOS/CHoCH, Dragon/T3 crosses...)."""
    rng = random.Random(seed)
    t0 = pd.Timestamp("2024-01-01")
    price = 100.0
    candles = []
    for i in range(n):
        change = rng.uniform(-4.0, 4.0)
        # Occasional climactic bar so Wyckoff's climax/SOS/SOW detectors
        # actually have something to fire on, not just a smooth random walk.
        if rng.random() < 0.15:
            change *= 3
        open_ = price
        close = max(1.0, price + change)
        high = max(open_, close) + rng.uniform(0.0, 2.0)
        low = max(0.1, min(open_, close) - rng.uniform(0.0, 2.0))
        volume = rng.uniform(500.0, 3000.0) * (3.0 if rng.random() < 0.15 else 1.0)
        candles.append(
            types.SimpleNamespace(
                bucket_start=(t0 + pd.Timedelta(days=i)).to_pydatetime(),
                open=open_,
                high=high,
                low=low,
                close=close,
                volume=volume,
            )
        )
        price = close
    return candles


@pytest.mark.parametrize("strategy_key", sorted(strategy_registry.REGISTRY.keys()))
def test_events_before_the_safe_margin_never_change_as_more_candles_arrive(strategy_key):
    strategy_module = strategy_registry.get_strategy(strategy_key)
    cfg = _STRATEGY_CONFIGS[strategy_key]
    candles = _make_candles(N_BARS)

    full_events = strategy_module.analyze(candles, cfg).events
    assert full_events, f"{strategy_key}: fixture produced zero events, test can't prove anything"

    for cutoff in range(30, N_BARS - 5, 10):
        prefix_events = strategy_module.analyze(candles[: cutoff + 1], cfg).events
        boundary = cutoff - SAFE_MARGIN

        full_settled = {(e.type, e.index, round(e.price, 6)) for e in full_events if e.index <= boundary}
        prefix_settled = {(e.type, e.index, round(e.price, 6)) for e in prefix_events if e.index <= boundary}

        assert prefix_settled == full_settled, (
            f"{strategy_key}: events up to index {boundary} differ between a run truncated at "
            f"{cutoff} and the full-history run -- a detector is using data from the future "
            f"relative to the bar it's classifying (repaint)."
        )
