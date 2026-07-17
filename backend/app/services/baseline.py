"""Unconditional forward-return baseline: "what happens if you entered on
literally any bar, no signal required" -- the reference point a raw event
win rate needs before it means anything (see signal_outcomes.get_stats's
baseline_win_rate_N/edge_N fields).

Every bar with enough future bars is treated as a hypothetical entry, so
adjacent windows overlap heavily (bar i and i+1 share almost the same future
window) -- this is NOT an i.i.d. sample and isn't a formal significance test.
It's still the right reference for "does this event type beat doing nothing
in particular", which a win rate alone can't answer on its own.
"""

from __future__ import annotations

from app.models import Candle
from app.services.signal_outcomes import HORIZONS, is_win


def compute_baseline(candles: list[Candle], horizons=HORIZONS) -> dict[int, dict]:
    """``candles`` must be ONE ticker's own series, ordered by bucket_start --
    forward return is computed as closes[i+horizon] vs closes[i] within this
    series, so mixing tickers here would divide one instrument's price by
    another's."""
    closes = [c.close for c in candles]
    n = len(closes)
    result: dict[int, dict] = {}
    for horizon in horizons:
        long_wins = 0
        short_wins = 0
        count = 0
        for i in range(n - horizon):
            entry = closes[i]
            if not entry:
                continue
            ret = (closes[i + horizon] - entry) / entry
            count += 1
            if is_win(ret, True):
                long_wins += 1
            if is_win(ret, False):
                short_wins += 1
        result[horizon] = {
            "long_win_rate": round(long_wins / count, 3) if count else None,
            "short_win_rate": round(short_wins / count, 3) if count else None,
            "long_wins": long_wins,
            "short_wins": short_wins,
            "n": count,
        }
    return result


def wilson_ci(wins: int, n: int, z: float = 1.96) -> tuple[float, float] | None:
    """95% Wilson score confidence interval for a binomial proportion --
    tighter and better-behaved than a normal approximation at small n or
    when p is near 0/1 (both common here: some event types have only a
    few dozen resolved samples, and win rates run well under 50%)."""
    if n == 0:
        return None
    p = wins / n
    denom = 1 + z**2 / n
    centre = p + z**2 / (2 * n)
    spread = z * ((p * (1 - p) / n + z**2 / (4 * n**2)) ** 0.5)
    low = (centre - spread) / denom
    high = (centre + spread) / denom
    return (round(max(0.0, low), 3), round(min(1.0, high), 3))
