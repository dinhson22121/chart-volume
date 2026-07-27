"""One-off backtest: the experimental RSI-momentum-turn signal
(app/wyckoff/rsi_spring.py) against real HOSE/HNX daily history, using the
same walk_events()/opt-holdout-split discipline as optimize_wyckoff.py. A
first pass on VN30 alone (29 tickers) showed the first positive-mean-R
result across both windows this session found -- this reruns it against the
full universe to see if that holds up on a much larger sample.

Bullish-only by design intent (spot trading has no short-selling -- see this
session's is_bullish Trust Layer filter), but reports the bearish side
(RSI Upthrust) too for reference since the detector is symmetric.

Read-only, writes nothing. Run from backend/: `python scripts/backtest_rsi_spring.py`.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlmodel import Session  # noqa: E402

from app.db import get_engine  # noqa: E402
from app.models import Symbol, Timeframe  # noqa: E402
from app.services import scenario_backtest, settings_service  # noqa: E402
from app.wyckoff import rsi_spring  # noqa: E402
from scripts.optimize_wyckoff import (  # noqa: E402
    OPT_HOLDOUT_CUTOFF,
    _format_window,
    _load_daily_candles,
    _r_multiple,
    _score_window,
    _stock_tickers_with_enough_history,
)

STRATEGY = "rsi_spring"

# How many equal, chronologically-ordered slices to break the holdout window
# into for the "is this concentrated in one stretch" check -- separate from
# stats_significance.walk_forward_analysis's own default of 5, so the report
# below can show real calendar dates per slice, not just a single ratio.
N_TIME_SLICES = 6


def _chronological_breakdown(dated_r: list[tuple], n_slices: int) -> None:
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

        tickers = _stock_tickers_with_enough_history(session, vn30_only=False)
        print(f"{len(tickers)} HOSE/HNX ticker(s)\n")

        buckets = {
            "bullish": {"opt": [], "holdout": []},
            "bearish": {"opt": [], "holdout": []},
        }

        for i, ticker in enumerate(tickers, 1):
            candles = _load_daily_candles(session, ticker)
            symbol = session.get(Symbol, ticker)
            print(f"[{i}/{len(tickers)}] {ticker} ({len(candles)} bars)", file=sys.stderr)

            result = rsi_spring.analyze(candles, None, None, "vi")

            scenarios = scenario_backtest.walk_events(
                ticker, Timeframe.DAILY, STRATEGY, candles, result.events,
                rsi_spring.BULLISH_EVENTS, rsi_spring.BEARISH_EVENTS, result.levels, rsi_spring,
                None, None, rsi_spring.RANGING_PHASES, symbol, risk_cfg,
            )

            for s in scenarios:
                if s.status not in ("hit_tp", "hit_sl", "expired") or s.exit_price is None:
                    continue
                r = _r_multiple(s, risk_cfg)
                if r is None:
                    continue
                direction = "bullish" if s.is_bullish else "bearish"
                window = "opt" if s.event_ts < OPT_HOLDOUT_CUTOFF else "holdout"
                # (event_ts, r) -- NOT bare r -- so chronological order can be
                # reconstructed later. Appending in per-ticker processing
                # order (as this loop does) is NOT chronological order across
                # the whole universe; sorting happens below, right before
                # anything time-sensitive (walk-forward, the slice
                # breakdown) touches these lists.
                buckets[direction][window].append((s.event_ts, r))

        for direction in ("bullish", "bearish"):
            print(f"\n=== RSI {'Spring' if direction == 'bullish' else 'Upthrust'} ({direction}) ===")
            for label, key in (("opt window    ", "opt"), ("holdout window", "holdout")):
                dated = sorted(buckets[direction][key], key=lambda pair: pair[0])
                r_multiples = [r for _, r in dated]
                print(_format_window(label, _score_window(r_multiples, risk_amount, risk_cfg["notional_capital"])))
            print("  holdout window, chronological breakdown (is the result concentrated in one stretch?):")
            _chronological_breakdown(buckets[direction]["holdout"], N_TIME_SLICES)


if __name__ == "__main__":
    main()
