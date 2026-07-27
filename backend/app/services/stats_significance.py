"""Statistical significance for signal-quality stats, corrected for testing
many event types/strategies/timeframes at once.

A win rate's Wilson CI (see app.services.baseline) answers "is this one event
type's win rate distinguishable from its baseline". It does NOT answer "is
this true after accounting for how many event types I looked at before
finding this one" -- with enough event types x strategies x timeframes
compared side by side, some will clear an uncorrected significance bar by
chance alone, entirely independent of whether they have real edge. This
module answers the second question via Benjamini-Hochberg false-discovery-
rate control, the standard fix for testing many hypotheses at once.
"""

from __future__ import annotations

import math
import random
import statistics
from datetime import datetime, timedelta
from typing import Sequence


def effective_n(
    timestamps_by_group: dict[str, list[datetime]], horizon_bars: int, bar_duration: timedelta
) -> int:
    """Declustered observation count for a one_sample_p_value SE, correcting
    for the same problem Benjamini-Hochberg does NOT cover: two observations
    whose forward-return windows overlap (event B falls within `horizon_bars`
    of event A, on the SAME underlying price series) aren't independent
    Bernoulli trials -- they partly share the same future price move. Treating
    the raw count as `n` understates the true standard error and makes
    p-values look smaller than they are.

    Each ``timestamps_by_group`` key is a series that can overlap with itself
    (e.g. one ticker's events) but never with another key's (a different
    ticker's price path is independent) -- so declustering runs per group,
    sorted by time, keeping an event only when it's at least
    ``horizon_bars * bar_duration`` after the last kept one, then the kept
    counts are summed across groups."""
    min_gap = horizon_bars * bar_duration
    total = 0
    for timestamps in timestamps_by_group.values():
        ordered = sorted(timestamps)
        if not ordered:
            continue
        kept = 1
        last_kept = ordered[0]
        for ts in ordered[1:]:
            if ts - last_kept >= min_gap:
                kept += 1
                last_kept = ts
        total += kept
    return total


def one_sample_p_value(win_rate: float | None, baseline_rate: float | None, n: int) -> float | None:
    """Two-sided p-value for "this event type's observed win rate differs
    from the baseline rate", treating baseline_rate as a fixed reference
    proportion (its own sample is far larger than any single event type's,
    so its own sampling error is negligible by comparison) and win_rate as
    estimated from ``n`` trials. Uses a normal approximation to the binomial
    (``math.erf`` for the standard normal CDF -- no scipy/numpy dependency
    needed for this). None when there's nothing to test (no samples, or
    either input missing)."""
    if win_rate is None or baseline_rate is None or n <= 0:
        return None
    if baseline_rate <= 0 or baseline_rate >= 1:
        return None  # degenerate reference proportion, z-test undefined
    se = math.sqrt(baseline_rate * (1 - baseline_rate) / n)
    if se == 0:
        return None
    z = (win_rate - baseline_rate) / se
    # Two-sided p-value from the standard normal CDF via erf.
    return 2 * (1 - 0.5 * (1 + math.erf(abs(z) / math.sqrt(2))))


def _equity_curve_returns(
    r_multiples: Sequence[float], risk_amount: float, notional_capital: float
) -> list[float]:
    """Percentage returns of a notional_capital account as each trade's
    dollar P&L (risk_amount * r) compounds through it, IN THE GIVEN ORDER.
    Unlike the raw R-multiples' own mean/std (order-invariant -- shuffling
    can never change a set's average), these returns depend on account
    level at each step, which depends on what happened before it -- this is
    what makes a permutation test of trade ORDER meaningful at all."""
    equity = notional_capital
    returns = []
    for r in r_multiples:
        pnl = risk_amount * r
        new_equity = equity + pnl
        returns.append((new_equity - equity) / equity if equity else 0.0)
        equity = new_equity
    return returns


def _max_drawdown_r(r_multiples: Sequence[float]) -> float:
    """Peak-to-trough on cumulative R, in the given order -- same
    calculation as app.services.trade_scenario.get_scenario_stats'
    max_drawdown_r, reused here so the permutation test's "actual" value
    matches exactly what the stats page already shows."""
    cum_r = 0.0
    peak_r = 0.0
    max_dd = 0.0
    for r in r_multiples:
        cum_r += r
        peak_r = max(peak_r, cum_r)
        max_dd = max(max_dd, peak_r - cum_r)
    return max_dd


def _r_sharpe(returns: Sequence[float]) -> float:
    """Mean/stdev of per-trade account returns -- not annualized, since
    trade cadence is irregular across a mixed portfolio of tickers/
    timeframes/strategies (no single "trades per year" figure would be
    honest here). 0.0 when there's no variance to divide by (e.g. every
    trade the exact same size) rather than raising or returning inf."""
    if len(returns) < 2:
        return 0.0
    stdev = statistics.pstdev(returns)
    if stdev == 0:
        return 0.0
    return statistics.fmean(returns) / stdev


def monte_carlo_permutation_test(
    r_multiples: Sequence[float],
    risk_amount: float,
    notional_capital: float,
    n_simulations: int = 1000,
    seed: int = 42,
) -> dict | None:
    """Shuffles the ORDER of the same set of closed trades' R-multiples to
    test whether the REALIZED equity path (smoothness, max drawdown) is
    unusually favorable or unfavorable compared to a random reshuffle of the
    exact same wins/losses.

    This does NOT test whether the strategy's average edge is real --
    shuffling can never change the mean R-multiple (it's order-invariant),
    so that question is answered elsewhere (see
    signal_outcomes.get_stats' baseline win-rate comparison). What this DOES
    catch: real trading losses often cluster together in bad market regimes
    rather than landing independently at random -- if the actual drawdown is
    far worse than nearly every random reshuffle produces, that clustering
    is a real property of this trade sequence, not a coincidence, and the
    realistic worst case is worse than the average-trade stats alone would
    suggest.

    Both p-values use the SAME convention: the fraction of random reshuffles
    that did AT LEAST AS WELL as the actual order (higher r_sharpe counts as
    better; smaller max_drawdown_r counts as better). A LOW p-value here
    means the actual order outperformed almost all random shuffles -- read
    it as a REASSURING sign for both metrics, not a red flag the way a
    classic significance test's low p-value would be; a HIGH p-value means
    the realized path is unremarkable (or, for drawdown specifically, worse
    than most reshuffles -- inspect actual_max_drawdown_r itself alongside
    the p-value, not the p-value alone).

    None when there are fewer than 3 trades -- a permutation test over that
    few orderings can't say anything."""
    ordered = list(r_multiples)
    if len(ordered) < 3:
        return None

    actual_sharpe = _r_sharpe(_equity_curve_returns(ordered, risk_amount, notional_capital))
    actual_dd = _max_drawdown_r(ordered)

    rng = random.Random(seed)
    working = list(ordered)
    sharpe_at_least_as_good = 0
    dd_at_least_as_good = 0
    sim_sharpes: list[float] = []
    for _ in range(n_simulations):
        rng.shuffle(working)
        sim_sharpe = _r_sharpe(_equity_curve_returns(working, risk_amount, notional_capital))
        sim_dd = _max_drawdown_r(working)
        sim_sharpes.append(sim_sharpe)
        if sim_sharpe >= actual_sharpe:
            sharpe_at_least_as_good += 1
        if sim_dd <= actual_dd:
            dd_at_least_as_good += 1

    sim_sharpes.sort()
    return {
        "actual_r_sharpe": round(actual_sharpe, 4),
        "actual_max_drawdown_r": round(actual_dd, 4),
        "p_value_r_sharpe": round(sharpe_at_least_as_good / n_simulations, 4),
        "p_value_max_drawdown_r": round(dd_at_least_as_good / n_simulations, 4),
        "simulated_r_sharpe_mean": round(statistics.fmean(sim_sharpes), 4),
        "simulated_r_sharpe_p5": round(_percentile(sim_sharpes, 5), 4),
        "simulated_r_sharpe_p95": round(_percentile(sim_sharpes, 95), 4),
        "n_simulations": n_simulations,
        "n_trades": len(ordered),
    }


def _percentile(sorted_values: Sequence[float], pct: float) -> float:
    """Linear-interpolated percentile of an already-sorted sequence (no
    numpy dependency, consistent with the rest of this module)."""
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return sorted_values[0]
    rank = (pct / 100) * (len(sorted_values) - 1)
    lo = math.floor(rank)
    hi = math.ceil(rank)
    if lo == hi:
        return sorted_values[int(rank)]
    frac = rank - lo
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * frac


def bootstrap_sharpe_ci(
    r_multiples: Sequence[float],
    risk_amount: float,
    notional_capital: float,
    n_bootstrap: int = 1000,
    confidence: float = 0.95,
    seed: int = 42,
) -> dict | None:
    """Resamples the per-trade account returns WITH replacement to estimate
    a confidence interval for r_sharpe.

    Different question from monte_carlo_permutation_test: that test
    reshuffles this EXACT fixed set of trades to check whether their
    particular order was fortunate or unfortunate. This one treats each
    observed return as one draw from an unknown underlying distribution and
    asks how much the r_sharpe estimate would wobble if a fresh sample the
    same size happened to look slightly different -- the closest thing to
    "does the edge itself look real" this codebase computes without a full
    backtest. A CI that stays above 0 across its whole range (``ci_lower >
    0``) is the reassuring case; one that straddles 0 means the data can't
    yet rule out "no real edge".

    None below 5 trades -- a resample of that few observations can't say
    anything about the shape of the underlying distribution."""
    ordered = list(r_multiples)
    if len(ordered) < 5:
        return None

    returns = _equity_curve_returns(ordered, risk_amount, notional_capital)
    observed = _r_sharpe(returns)

    rng = random.Random(seed)
    boot_sharpes = [
        _r_sharpe([rng.choice(returns) for _ in range(len(returns))]) for _ in range(n_bootstrap)
    ]
    boot_sharpes.sort()

    alpha = (1 - confidence) / 2
    lower = _percentile(boot_sharpes, alpha * 100)
    upper = _percentile(boot_sharpes, (1 - alpha) * 100)
    prob_positive = sum(1 for s in boot_sharpes if s > 0) / n_bootstrap

    return {
        "observed_r_sharpe": round(observed, 4),
        "ci_lower": round(lower, 4),
        "ci_upper": round(upper, 4),
        "median_r_sharpe": round(statistics.median(boot_sharpes), 4),
        "prob_positive": round(prob_positive, 4),
        "confidence": confidence,
        "n_bootstrap": n_bootstrap,
        "n_trades": len(ordered),
    }


def walk_forward_analysis(r_multiples: Sequence[float], n_windows: int = 5) -> dict | None:
    """Splits the CHRONOLOGICALLY ordered R-multiples into ``n_windows``
    consecutive, roughly-equal chunks and computes each window's own mean
    R-multiple -- checking whether the accumulated edge held up broadly
    across different calendar stretches, or was carried entirely by one
    lucky window.

    This is a simplified walk-forward check (sequential-window consistency),
    not the classic train-then-reoptimize/test variant from backtesting
    literature: chart-volume's signals are forward-tracked at detection
    time, never parameter-fit per window, so there's no in-sample/
    out-of-sample split to make here. ``consistency_ratio`` is the fraction
    of windows with a positive mean R; ``expectancy_std_across_windows`` is
    how much that mean varies window to window -- low means the edge (or
    lack of one) is stable over time, high means it's lumpy/period-dependent.

    None when there isn't at least 2 trades per window on average -- a
    window's own mean is meaningless from a single trade."""
    ordered = list(r_multiples)
    if n_windows < 1 or len(ordered) < n_windows * 2:
        return None

    size = len(ordered) / n_windows
    per_window = []
    for i in range(n_windows):
        start = round(i * size)
        end = round((i + 1) * size)
        chunk = ordered[start:end]
        per_window.append(
            {"n_trades": len(chunk), "expectancy_r": round(statistics.fmean(chunk), 4) if chunk else None}
        )

    expectancies = [w["expectancy_r"] for w in per_window if w["expectancy_r"] is not None]
    positive_windows = sum(1 for e in expectancies if e > 0)

    return {
        "per_window": per_window,
        "n_windows": n_windows,
        "positive_windows": positive_windows,
        "consistency_ratio": round(positive_windows / len(expectancies), 4) if expectancies else None,
        "expectancy_std_across_windows": (
            round(statistics.pstdev(expectancies), 4) if len(expectancies) >= 2 else None
        ),
    }


def benjamini_hochberg(p_values: list[float | None], alpha: float = 0.05) -> list[bool | None]:
    """Standard BH step-up procedure. Returns one flag per input p-value, in
    the SAME order as the input (not sorted) -- ``None`` in yields ``None``
    out (an untestable entry is skipped, not silently marked significant or
    not). Ranking and the step-up threshold are computed only over the
    non-None subset, since that's the actual family of hypotheses being
    tested together."""
    indexed = [(i, p) for i, p in enumerate(p_values) if p is not None]
    result: list[bool | None] = [None] * len(p_values)
    if not indexed:
        return result

    m = len(indexed)
    ranked = sorted(indexed, key=lambda ip: ip[1])

    # Largest rank k where p(k) <= (k/m) * alpha; every rank <= k is significant.
    cutoff_rank = 0
    for rank, (_, p) in enumerate(ranked, start=1):
        if p <= (rank / m) * alpha:
            cutoff_rank = rank

    for rank, (original_index, _) in enumerate(ranked, start=1):
        result[original_index] = rank <= cutoff_rank
    return result
