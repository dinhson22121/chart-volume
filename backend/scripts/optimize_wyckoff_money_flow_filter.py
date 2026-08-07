"""One-off test of a money-flow entry filter: only trust a bullish Wyckoff
entry event if a real MoneyFlowIn day (app.services.money_flow -- volume >2x
its trailing 10-session average AND price up >1%) occurred within a recent
lookback window. Unlike the Relative-Strength filter already tried this
session (which compares a stock against the broader market and made results
worse), this is a purely LOCAL confirmation: does this specific stock show
genuine capital committing to it recently, not just a price-pattern match.

Backtests the SAME (2.0, 1.5, 0.003) live-default config WITH vs WITHOUT the
filter, isolating just this one change, through the IDENTICAL walk_events()
the live app and every other script here uses -- only the input event list
differs (bullish events with no recent MoneyFlowIn are dropped before the
walk). Read-only, opens the real app DB but never commits. Run from
backend/: `python scripts/optimize_wyckoff_money_flow_filter.py`.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlmodel import Session  # noqa: E402

import app.wyckoff as wyckoff_module  # noqa: E402
from app.db import get_engine  # noqa: E402
from app.models import Symbol, Timeframe  # noqa: E402
from app.services import money_flow, scenario_backtest, settings_service  # noqa: E402
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

# How many bars back from (and including) the entry event a MoneyFlowIn day
# must have occurred within to count as "recent" confirmation.
LOOKBACK_GRID = (5, 10, 20)


def _money_flow_in_indices(candles: list) -> set[int]:
    events = money_flow.detect_money_flow_events(candles)
    return {e.index for e in events if e.type == money_flow.MONEY_FLOW_IN}


def _has_recent_inflow(event_index: int, in_indices: set[int], lookback: int) -> bool:
    return any(event_index - lookback <= idx <= event_index for idx in in_indices)


def _sweep_one_ticker(session: Session, ticker: str) -> dict:
    risk_cfg = settings_service.get_risk_config(session)
    candles = _load_daily_candles(session, ticker)
    symbol = session.get(Symbol, ticker)
    climax, sos, sl_buffer = DEFAULT_CANDIDATE
    cfg = WyckoffConfig(climax_vol_mult=climax, sos_vol_mult=sos)

    result = wyckoff_module.analyze(candles, cfg, None, "vi")
    in_indices = _money_flow_in_indices(candles)

    ticker_results: dict = {"baseline": {"opt": [], "holdout": []}}
    for lookback in LOOKBACK_GRID:
        ticker_results[lookback] = {"opt": [], "holdout": []}

    variants: list = ["baseline"] + list(LOOKBACK_GRID)
    for variant in variants:
        if variant == "baseline":
            events = result.events
        else:
            events = [
                e for e in result.events
                if e.type not in BULLISH_EVENTS or _has_recent_inflow(e.index, in_indices, variant)
            ]
        scenarios = scenario_backtest.walk_events(
            ticker, Timeframe.DAILY, STRATEGY, candles, events,
            BULLISH_EVENTS, BEARISH_EVENTS, result.levels, wyckoff_module,
            cfg, None, RANGING_PHASES, symbol, risk_cfg,
        )
        for s in scenarios:
            if not s.is_bullish or s.status not in ("hit_tp", "hit_sl", "expired") or s.exit_price is None:
                continue
            r = _r_multiple(s, risk_cfg)
            if r is None:
                continue
            window = "opt" if s.event_ts < OPT_HOLDOUT_CUTOFF else "holdout"
            ticker_results[variant][window].append(r)

    return ticker_results


def main() -> None:
    engine = get_engine()
    with Session(engine) as session:
        risk_cfg = settings_service.get_risk_config(session)
        risk_amount = risk_cfg["notional_capital"] * risk_cfg["risk_pct_per_trade"] / 100
        tickers = _stock_tickers_with_enough_history(session)

    variants: list = ["baseline"] + list(LOOKBACK_GRID)
    results: dict = {v: {"opt": [], "holdout": []} for v in variants}
    workers = parallel_tickers.default_workers()
    print(f"{len(tickers)} stock ticker(s), {workers} process(es)\n")

    for i, ticker, ticker_results in parallel_tickers.map_tickers(tickers, _sweep_one_ticker, workers=workers):
        print(f"[{i}/{len(tickers)}] {ticker}", file=sys.stderr)
        for v, windows in ticker_results.items():
            for window, rs in windows.items():
                results[v][window].extend(rs)

    print("\n=== Money-flow entry filter (require a recent MoneyFlowIn day) ===")
    for v in variants:
        label = "baseline (no filter)" if v == "baseline" else f"lookback={v} bars"
        print(f"\n{label}")
        print(_format_window("opt window    ", _score_window(results[v]["opt"], risk_amount, risk_cfg["notional_capital"])))
        print(_format_window("holdout window", _score_window(results[v]["holdout"], risk_amount, risk_cfg["notional_capital"])))


if __name__ == "__main__":
    main()
