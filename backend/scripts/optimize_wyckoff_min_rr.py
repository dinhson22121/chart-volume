"""!!! ITS NUMBERS ARE NOT TRUSTWORTHY -- READ THIS FIRST !!!

This script scores scenario_backtest.walk_events() output WITHOUT filtering
``scenario.is_bullish``, so it averages in bearish/short R-multiples the live
app can never trade (it is spot/long-only -- trade_scenario._create_scenarios
only ever creates bullish scenarios). Its headline result (holdout mean_r
rising to +0.237 at MIN_RR_RATIO=3.0) was an artifact of that: bullish-only,
the same filter crushes the VN30 holdout sample from n=62 to n=6, far too
small to trust either way, so the MIN_RR_RATIO it motivated was reverted (see
app/wyckoff/__init__.py). Kept only as a record of the sweep that was run.
Copy the is_bullish filter from diagnose_wyckoff_bullish_only.py before
reusing any of this.

One-off test of a minimum reward:risk filter for Wyckoff entries -- SMC
already has one (app.smc.phase.MIN_RR_RATIO = 3.0); Wyckoff has none, so
every structurally weak setup (small measured-move relative to its stop)
currently gets a trade plan just like a strong one. trade_scenario.
_build_scenario_candidate already reads MIN_RR_RATIO generically via
getattr(strategy_module, "MIN_RR_RATIO", _NO_MIN_RR) -- this just sets that
attribute on app.wyckoff and sweeps it, no new gating logic needed.

Backtests against the SAME opt/holdout split as optimize_wyckoff.py, using
the current live defaults (climax=2.0, sos=1.5, SL_BUFFER=0.003,
trend_efficiency_max=0.15). Read-only, opens the real app DB but never
commits. Run from backend/: `python scripts/optimize_wyckoff_min_rr.py`.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlmodel import Session  # noqa: E402

import app.wyckoff as wyckoff_module  # noqa: E402
from app.db import get_engine  # noqa: E402
from app.models import Symbol, Timeframe  # noqa: E402
from app.services import scenario_backtest, settings_service  # noqa: E402
from app.wyckoff import BEARISH_EVENTS, BULLISH_EVENTS, RANGING_PHASES  # noqa: E402
from app.wyckoff.config import WyckoffConfig  # noqa: E402
from scripts import parallel_tickers  # noqa: E402
from scripts.optimize_wyckoff import (  # noqa: E402
    DEFAULT_CANDIDATE,
    OPT_HOLDOUT_CUTOFF,
    _format_window,
    _load_daily_candles,
    _r_multiple,
    _score_window,
    _stock_tickers_with_enough_history,
)

STRATEGY = "wyckoff"

# None = baseline (today's live behavior, no R:R filter at all).
MIN_RR_GRID = (None, 2.0, 2.5, 3.0, 3.5, 4.0, 5.0)


def _sweep_one_ticker(session: Session, ticker: str) -> dict:
    risk_cfg = settings_service.get_risk_config(session)
    candles = _load_daily_candles(session, ticker)
    symbol = session.get(Symbol, ticker)
    climax, sos, _ = DEFAULT_CANDIDATE
    cfg = WyckoffConfig(climax_vol_mult=climax, sos_vol_mult=sos)

    result = wyckoff_module.analyze(candles, cfg, None, "vi")

    ticker_results: dict = {v: {"opt": [], "holdout": []} for v in MIN_RR_GRID}
    has_attr = hasattr(wyckoff_module, "MIN_RR_RATIO")
    original = getattr(wyckoff_module, "MIN_RR_RATIO", None)
    try:
        for min_rr in MIN_RR_GRID:
            if min_rr is None:
                if has_attr:
                    delattr(wyckoff_module, "MIN_RR_RATIO")
            else:
                wyckoff_module.MIN_RR_RATIO = min_rr

            scenarios = scenario_backtest.walk_events(
                ticker, Timeframe.DAILY, STRATEGY, candles, result.events,
                BULLISH_EVENTS, BEARISH_EVENTS, result.levels, wyckoff_module,
                cfg, None, RANGING_PHASES, symbol, risk_cfg,
            )
            bucket = ticker_results[min_rr]
            for s in scenarios:
                if s.status not in ("hit_tp", "hit_sl", "expired") or s.exit_price is None:
                    continue
                r = _r_multiple(s, risk_cfg)
                if r is None:
                    continue
                window = "opt" if s.event_ts < OPT_HOLDOUT_CUTOFF else "holdout"
                bucket[window].append(r)
    finally:
        if has_attr:
            wyckoff_module.MIN_RR_RATIO = original
        elif hasattr(wyckoff_module, "MIN_RR_RATIO"):
            delattr(wyckoff_module, "MIN_RR_RATIO")

    return ticker_results


def main() -> None:
    engine = get_engine()
    with Session(engine) as session:
        risk_cfg = settings_service.get_risk_config(session)
        risk_amount = risk_cfg["notional_capital"] * risk_cfg["risk_pct_per_trade"] / 100
        tickers = _stock_tickers_with_enough_history(session)

    results: dict = {v: {"opt": [], "holdout": []} for v in MIN_RR_GRID}
    workers = parallel_tickers.default_workers()
    print(f"{len(tickers)} stock ticker(s), {workers} process(es)\n")

    for i, ticker, ticker_results in parallel_tickers.map_tickers(tickers, _sweep_one_ticker, workers=workers):
        print(f"[{i}/{len(tickers)}] {ticker}", file=sys.stderr)
        for v, windows in ticker_results.items():
            for window, rs in windows.items():
                results[v][window].extend(rs)

    print("\n=== Minimum reward:risk filter (trend_efficiency_max=0.15 baseline) ===")
    for v in MIN_RR_GRID:
        label = "baseline (no R:R filter)" if v is None else f"MIN_RR_RATIO={v}"
        print(f"\n{label}")
        print(_format_window("opt window    ", _score_window(results[v]["opt"], risk_amount, risk_cfg["notional_capital"])))
        print(_format_window("holdout window", _score_window(results[v]["holdout"], risk_amount, risk_cfg["notional_capital"])))


if __name__ == "__main__":
    main()
