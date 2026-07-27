"""One-off Wyckoff parameter sweep against real historical daily candles
already sitting in the app's DB -- an attempt to answer "can historical data
help find better thresholds" without falling into the trap it's designed to
catch: overfitting a config to the exact data it's scored on.

Method: for each candidate (climax_vol_mult, sos_vol_mult, SL_BUFFER_PCT),
replay every tracked stock ticker's full daily history through the SAME
detection/gating/outcome logic live tracking and the real backtest engine
use (app.services.scenario_backtest.walk_events) -- entirely in memory, never
written to the DB. Each resulting R-multiple is bucketed by its event's
timestamp into an OPTIMIZATION window or a HOLDOUT window (see
OPT_HOLDOUT_CUTOFF) that is never touched while ranking candidates. Only
after ranking on the optimization window are the top candidates' HOLDOUT
numbers revealed -- if a candidate's edge doesn't show up there too, it was
never real, just curve-fit to the optimization window.

Read-only: opens the real app DB (app.db.get_engine()) but never commits or
writes a row. Run from backend/: `python scripts/optimize_wyckoff.py`.
"""

from __future__ import annotations

import sys
from datetime import datetime
from itertools import product
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlmodel import Session, func, select  # noqa: E402

import app.wyckoff as wyckoff_module  # noqa: E402
from app.db import get_engine  # noqa: E402
from app.models import AssetClass, Candle, Symbol, Timeframe  # noqa: E402
from app.services import scenario_backtest, settings_service, stats_significance, trade_scenario  # noqa: E402
from app.wyckoff import BEARISH_EVENTS, BULLISH_EVENTS, RANGING_PHASES  # noqa: E402
from app.wyckoff.config import WyckoffConfig  # noqa: E402

STRATEGY = "wyckoff"

# ~1.5 years of daily bars -- enough for a real Volume Profile window
# (vp_lookback_bars=50) plus a meaningful stretch of signal history; a
# freshly-listed stock with only a few months of candles isn't worth
# including (too little history to say anything about any config).
MIN_DAILY_BARS = 300

# Events before this date score the optimization window; on/after score the
# holdout window. ~18 months of holdout out of ~6 years of history (2020-07
# to 2026-07) -- see the module docstring on why this split is never
# collapsed into one pooled window.
OPT_HOLDOUT_CUTOFF = datetime(2025, 1, 1)

CLIMAX_VOL_MULT_GRID = (1.5, 2.0, 2.5)
SOS_VOL_MULT_GRID = (1.2, 1.5, 2.0)
SL_BUFFER_PCT_GRID = (0.002, 0.003, 0.005)

# Matches WyckoffConfig()'s and trade_scenario.SL_BUFFER_PCT's current
# defaults -- always reported alongside the sweep so "did anything actually
# beat what's live today" has a concrete baseline, not just a ranking among
# candidates.
DEFAULT_CANDIDATE = (2.0, 1.5, 0.003)


def _stock_tickers_with_enough_history(session: Session, vn30_only: bool = True) -> list[str]:
    # vn30_only=True by default -- VN30 was this session's first, faster
    # pass (30 tickers) to sanity-check a candidate before spending the time
    # on the full HOSE/HNX universe (~200+ tickers, several times slower).
    conditions = [Candle.timeframe == Timeframe.DAILY, Symbol.asset_class == AssetClass.STOCK]
    if vn30_only:
        conditions.append(Symbol.is_vn30 == True)  # noqa: E712
    rows = session.exec(
        select(Candle.ticker, func.count(Candle.id))
        .join(Symbol, Symbol.ticker == Candle.ticker)
        .where(*conditions)
        .group_by(Candle.ticker)
        .having(func.count(Candle.id) >= MIN_DAILY_BARS)
    ).all()
    return [r[0] for r in rows]


def _load_daily_candles(session: Session, ticker: str) -> list[Candle]:
    return session.exec(
        select(Candle)
        .where(Candle.ticker == ticker, Candle.timeframe == Timeframe.DAILY)
        .order_by(Candle.bucket_start)
    ).all()


def _r_multiple(scenario, risk_cfg: dict) -> float | None:
    """Same cost-adjusted R-multiple formula as trade_scenario.get_scenario_stats,
    scoped to the stock cost model only (this sweep is stock-only) -- so
    numbers here are directly comparable to what the Trade History dashboard
    shows for real live/backtest scenarios."""
    risk_distance = abs(scenario.entry - scenario.stop_loss)
    if not risk_distance or scenario.exit_price is None:
        return None
    cost_pct = (
        risk_cfg["slippage_pct_stock"] + risk_cfg["broker_fee_pct_stock"] + risk_cfg["sell_tax_pct_stock"]
    ) / 100
    cost_amount = cost_pct * scenario.entry
    adjusted_exit = scenario.exit_price - cost_amount if scenario.is_bullish else scenario.exit_price + cost_amount
    raw = (adjusted_exit - scenario.entry) / risk_distance
    return raw if scenario.is_bullish else -raw


def _score_window(r_multiples: list[float], risk_amount: float, notional_capital: float) -> dict:
    n = len(r_multiples)
    bootstrap = stats_significance.bootstrap_sharpe_ci(r_multiples, risk_amount, notional_capital) if n else None
    walk_forward = stats_significance.walk_forward_analysis(r_multiples) if n else None
    return {
        "n_trades": n,
        "mean_r": sum(r_multiples) / n if n else None,
        "median_r": sorted(r_multiples)[n // 2] if n else None,
        "win_rate": sum(1 for r in r_multiples if r > 0) / n if n else None,
        "bootstrap_ci_lower": bootstrap["ci_lower"] if bootstrap else None,
        "bootstrap_ci_upper": bootstrap["ci_upper"] if bootstrap else None,
        "walk_forward_consistency": walk_forward["consistency_ratio"] if walk_forward else None,
    }


def _format_window(label: str, w: dict) -> str:
    if w["n_trades"] == 0:
        return f"  {label}: 0 trades"
    ci = (
        f"[{w['bootstrap_ci_lower']:.3f}, {w['bootstrap_ci_upper']:.3f}]"
        if w["bootstrap_ci_lower"] is not None
        else "n/a (<5 trades)"
    )
    wf = f"{w['walk_forward_consistency']:.2f}" if w["walk_forward_consistency"] is not None else "n/a (<10 trades)"
    return (
        f"  {label}: n={w['n_trades']:>4}  mean_r={w['mean_r']:+.3f}  median_r={w['median_r']:+.3f}  "
        f"win_rate={w['win_rate']:.1%}  bootstrap_ci={ci}  walk_forward_consistency={wf}"
    )


# How many equal, chronologically-ordered slices to break a holdout window
# into for the "is this concentrated in one stretch" check -- separate from
# stats_significance.walk_forward_analysis's own default of 5, so a report
# can show real calendar dates per slice, not just a single ratio. This is
# NOT optional: RSI Spring's pooled holdout number once looked like a real
# edge, but turned out to be concentrated in 2 of 6 slices, with the most
# recent 3 consecutive slices trending negative -- see git history for that
# experiment's own module (removed once this checked showed the same
# decay-over-time pattern every other signal tried this session had).
N_TIME_SLICES = 6


def _chronological_breakdown(dated_r: list[tuple], n_slices: int = N_TIME_SLICES) -> None:
    """dated_r: list of (event_ts, r) tuples. Prints n_slices equal,
    chronologically-ordered slices with their own date range/mean/win-rate,
    so a positive pooled result that's actually concentrated in one narrow
    stretch (vs. spread evenly across the window) is directly visible."""
    ordered = sorted(dated_r, key=lambda pair: pair[0])
    n = len(ordered)
    if n == 0:
        print("  (no trades)")
        return
    slice_size = max(1, n // n_slices)
    for i in range(0, n, slice_size):
        chunk = ordered[i : i + slice_size]
        if not chunk:
            continue
        rs = [r for _, r in chunk]
        start_ts, end_ts = chunk[0][0], chunk[-1][0]
        mean_r = sum(rs) / len(rs)
        win_rate = sum(1 for r in rs if r > 0) / len(rs)
        print(
            f"    {start_ts:%Y-%m-%d} .. {end_ts:%Y-%m-%d}  n={len(rs):>4}  "
            f"mean_r={mean_r:+.3f}  win_rate={win_rate:.1%}"
        )


def main() -> None:
    engine = get_engine()
    with Session(engine) as session:
        risk_cfg = settings_service.get_risk_config(session)
        risk_amount = risk_cfg["notional_capital"] * risk_cfg["risk_pct_per_trade"] / 100

        tickers = _stock_tickers_with_enough_history(session)
        print(f"{len(tickers)} stock ticker(s) with >= {MIN_DAILY_BARS} daily bars\n")

        # candidate -> {"opt": [r, ...], "holdout": [r, ...]}
        results: dict[tuple[float, float, float], dict[str, list[float]]] = {
            (c, s, b): {"opt": [], "holdout": []}
            for c, s, b in product(CLIMAX_VOL_MULT_GRID, SOS_VOL_MULT_GRID, SL_BUFFER_PCT_GRID)
        }

        for i, ticker in enumerate(tickers, 1):
            candles = _load_daily_candles(session, ticker)
            symbol = session.get(Symbol, ticker)
            print(f"[{i}/{len(tickers)}] {ticker} ({len(candles)} bars)", file=sys.stderr)

            for climax, sos in product(CLIMAX_VOL_MULT_GRID, SOS_VOL_MULT_GRID):
                cfg = WyckoffConfig(climax_vol_mult=climax, sos_vol_mult=sos)
                result = wyckoff_module.analyze(candles, cfg, None, "vi")

                # _build_scenario_candidate's phase-before-event gate re-runs
                # analyze() on the truncated pre-event window for EVERY
                # qualifying event -- the single most expensive step in the
                # whole walk. That gate result doesn't depend on
                # SL_BUFFER_PCT at all, so redoing it fresh for each of the 3
                # SL variants below is pure waste. Memoize it here (keyed by
                # truncated length, which is exactly event.index and
                # therefore unique per event for this fixed candles list) so
                # only the first SL variant pays for it.
                analyze_cache: dict[int, object] = {}
                original_analyze = wyckoff_module.analyze

                def _cached_analyze(candles_arg, *a, **k):
                    key = len(candles_arg)
                    if key not in analyze_cache:
                        analyze_cache[key] = original_analyze(candles_arg, *a, **k)
                    return analyze_cache[key]

                wyckoff_module.analyze = _cached_analyze
                try:
                    for sl_buffer in SL_BUFFER_PCT_GRID:
                        original_sl_buffer = trade_scenario.SL_BUFFER_PCT
                        trade_scenario.SL_BUFFER_PCT = sl_buffer
                        try:
                            scenarios = scenario_backtest.walk_events(
                                ticker, Timeframe.DAILY, STRATEGY, candles, result.events,
                                BULLISH_EVENTS, BEARISH_EVENTS, result.levels, wyckoff_module,
                                cfg, None, RANGING_PHASES, symbol, risk_cfg,
                            )
                        finally:
                            trade_scenario.SL_BUFFER_PCT = original_sl_buffer

                        bucket = results[(climax, sos, sl_buffer)]
                        for scenario in scenarios:
                            if scenario.status not in ("hit_tp", "hit_sl", "expired") or scenario.exit_price is None:
                                continue
                            r = _r_multiple(scenario, risk_cfg)
                            if r is None:
                                continue
                            window = "opt" if scenario.event_ts < OPT_HOLDOUT_CUTOFF else "holdout"
                            bucket[window].append(r)
                finally:
                    wyckoff_module.analyze = original_analyze

        scored = {
            cand: _score_window(data["opt"], risk_amount, risk_cfg["notional_capital"])
            for cand, data in results.items()
        }
        ranked = sorted(
            (c for c in scored if scored[c]["n_trades"] >= 10),
            key=lambda c: (scored[c]["bootstrap_ci_lower"] is not None, scored[c]["bootstrap_ci_lower"] or -999),
            reverse=True,
        )

        print("\n=== Baseline (current live defaults) ===")
        c, s, b = DEFAULT_CANDIDATE
        print(f"climax_vol_mult={c} sos_vol_mult={s} SL_BUFFER_PCT={b}")
        print(_format_window("opt window    ", _score_window(results[DEFAULT_CANDIDATE]["opt"], risk_amount, risk_cfg["notional_capital"])))
        print(_format_window("holdout window", _score_window(results[DEFAULT_CANDIDATE]["holdout"], risk_amount, risk_cfg["notional_capital"])))

        print("\n=== Top 5 candidates by opt-window bootstrap CI lower bound ===")
        for cand in ranked[:5]:
            c, s, b = cand
            print(f"\nclimax_vol_mult={c} sos_vol_mult={s} SL_BUFFER_PCT={b}")
            print(_format_window("opt window    ", scored[cand]))
            print(_format_window("holdout window", _score_window(results[cand]["holdout"], risk_amount, risk_cfg["notional_capital"])))


if __name__ == "__main__":
    main()
