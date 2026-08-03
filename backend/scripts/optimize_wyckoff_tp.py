"""One-off follow-up to optimize_wyckoff.py: does the take-profit distance
cap (MAX_RANGE_HEIGHT_PCT) explain the persistently negative edge that sweep
found?

optimize_wyckoff.py varied detection sensitivity (climax_vol_mult,
sos_vol_mult) and stop placement (SL_BUFFER_PCT) across 27 configs -- median
R stayed ~-1.05 to -1.13 in EVERY single one (VN30 and the broader HOSE/HNX
set alike), meaning the large majority of trades hit their stop almost
exactly regardless of how entry/stop were tuned. That points at the reward
side instead: the measured-move take-profit (capped at
entry * MAX_RANGE_HEIGHT_PCT, currently 0.5) may simply be too far to reach
within max_bars for most setups, structurally capping the win rate no matter
how detection/stop are tuned.

Detection (climax_vol_mult, sos_vol_mult) and stop (SL_BUFFER_PCT) are held
at their current live defaults here -- only MAX_RANGE_HEIGHT_PCT varies, to
isolate its effect instead of re-running the full combinatorial grid.

Same optimization/holdout split and in-memory-only methodology as
optimize_wyckoff.py (see that module's docstring) -- read-only, writes
nothing. Scoped to VN30 for now (fast, ~30 tickers); rerun against the full
HOSE/HNX universe once/if a promising cap is found here.

Run from backend/: `python scripts/optimize_wyckoff_tp.py`.
"""

from __future__ import annotations

import sys
from itertools import product
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlmodel import Session  # noqa: E402

import app.wyckoff as wyckoff_module  # noqa: E402
from app.db import get_engine  # noqa: E402
from app.models import Symbol, Timeframe  # noqa: E402
from app.services import scenario_backtest, settings_service, trade_scenario  # noqa: E402
from app.wyckoff import BEARISH_EVENTS, BULLISH_EVENTS, RANGING_PHASES  # noqa: E402
from app.wyckoff.config import WyckoffConfig  # noqa: E402
from scripts import parallel_tickers  # noqa: E402
from scripts.optimize_wyckoff import (  # noqa: E402
    OPT_HOLDOUT_CUTOFF,
    _format_window,
    _load_daily_candles,
    _r_multiple,
    _score_window,
    _stock_tickers_with_enough_history,
)

STRATEGY = "wyckoff"

# Current live defaults -- held fixed so this sweep isolates MAX_RANGE_HEIGHT_PCT's
# own effect instead of re-testing detection/stop again.
CLIMAX_VOL_MULT = 2.0
SOS_VOL_MULT = 1.5
SL_BUFFER_PCT = 0.003

# 0.5 is the current default (see trade_scenario.MAX_RANGE_HEIGHT_PCT); the
# rest test whether a much closer, more reachable target changes anything.
MAX_RANGE_HEIGHT_PCT_GRID = (0.08, 0.12, 0.16, 0.20, 0.30, 0.40, 0.50)


def _sweep_one_ticker(session: Session, ticker: str) -> dict[float, dict[str, list[float]]]:
    """One ticker across every MAX_RANGE_HEIGHT_PCT candidate -- the unit
    parallel_tickers farms out to a worker process. Returns a slice shaped
    exactly like ``results``: pct -> {"opt": [r, ...], "holdout": [...]}.

    Same reasoning as optimize_wyckoff.py's own worker for why this ISN'T a
    flat (event_ts, r) list re-sorted by the parent: this script never sorted
    by event_ts before scoring either, and walk_forward_analysis needs
    chronological input, so the parent must extend per-ticker in
    parallel_tickers.map_tickers' own guaranteed ticker order instead."""
    risk_cfg = settings_service.get_risk_config(session)
    candles = _load_daily_candles(session, ticker)
    symbol = session.get(Symbol, ticker)
    cfg = WyckoffConfig(climax_vol_mult=CLIMAX_VOL_MULT, sos_vol_mult=SOS_VOL_MULT)

    result = wyckoff_module.analyze(candles, cfg, None, "vi")

    # Same reasoning as optimize_wyckoff.py's cache: the phase-before-event
    # gate doesn't depend on MAX_RANGE_HEIGHT_PCT either, so memoize it
    # across the 7 variants below.
    analyze_cache: dict[int, object] = {}
    original_analyze = wyckoff_module.analyze

    def _cached_analyze(candles_arg, *a, **k):
        key = len(candles_arg)
        if key not in analyze_cache:
            analyze_cache[key] = original_analyze(candles_arg, *a, **k)
        return analyze_cache[key]

    ticker_results: dict[float, dict[str, list[float]]] = {
        pct: {"opt": [], "holdout": []} for pct in MAX_RANGE_HEIGHT_PCT_GRID
    }

    wyckoff_module.analyze = _cached_analyze
    original_sl_buffer = trade_scenario.SL_BUFFER_PCT
    trade_scenario.SL_BUFFER_PCT = SL_BUFFER_PCT
    try:
        for range_height_pct in MAX_RANGE_HEIGHT_PCT_GRID:
            original_range_height = trade_scenario.MAX_RANGE_HEIGHT_PCT
            trade_scenario.MAX_RANGE_HEIGHT_PCT = range_height_pct
            try:
                scenarios = scenario_backtest.walk_events(
                    ticker, Timeframe.DAILY, STRATEGY, candles, result.events,
                    BULLISH_EVENTS, BEARISH_EVENTS, result.levels, wyckoff_module,
                    cfg, None, RANGING_PHASES, symbol, risk_cfg,
                )
            finally:
                trade_scenario.MAX_RANGE_HEIGHT_PCT = original_range_height

            bucket = ticker_results[range_height_pct]
            for scenario in scenarios:
                if scenario.status not in ("hit_tp", "hit_sl", "expired") or scenario.exit_price is None:
                    continue
                r = _r_multiple(scenario, risk_cfg)
                if r is None:
                    continue
                window = "opt" if scenario.event_ts < OPT_HOLDOUT_CUTOFF else "holdout"
                bucket[window].append(r)
    finally:
        trade_scenario.SL_BUFFER_PCT = original_sl_buffer
        wyckoff_module.analyze = original_analyze

    return ticker_results


def main() -> None:
    engine = get_engine()
    with Session(engine) as session:
        risk_cfg = settings_service.get_risk_config(session)
        risk_amount = risk_cfg["notional_capital"] * risk_cfg["risk_pct_per_trade"] / 100
        tickers = _stock_tickers_with_enough_history(session)

    results: dict[float, dict[str, list[float]]] = {
        pct: {"opt": [], "holdout": []} for pct in MAX_RANGE_HEIGHT_PCT_GRID
    }
    workers = parallel_tickers.default_workers()
    print(f"{len(tickers)} VN30 ticker(s) with enough history, {workers} process(es)\n")

    for i, ticker, ticker_results in parallel_tickers.map_tickers(tickers, _sweep_one_ticker, workers=workers):
        print(f"[{i}/{len(tickers)}] {ticker}", file=sys.stderr)
        for pct, windows in ticker_results.items():
            for window, rs in windows.items():
                results[pct][window].extend(rs)

    print("\n=== MAX_RANGE_HEIGHT_PCT sweep (climax=2.0 sos=1.5 SL_buffer=0.003 fixed) ===")
    for pct in MAX_RANGE_HEIGHT_PCT_GRID:
        marker = "  <- current default" if pct == 0.5 else ""
        print(f"\nMAX_RANGE_HEIGHT_PCT={pct}{marker}")
        print(_format_window("opt window    ", _score_window(results[pct]["opt"], risk_amount, risk_cfg["notional_capital"])))
        print(_format_window("holdout window", _score_window(results[pct]["holdout"], risk_amount, risk_cfg["notional_capital"])))


if __name__ == "__main__":
    main()
