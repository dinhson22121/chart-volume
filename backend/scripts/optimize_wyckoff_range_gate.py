"""!!! ITS NUMBERS ARE NOT TRUSTWORTHY -- READ THIS FIRST !!!

This script scores scenario_backtest.walk_events() output WITHOUT filtering
``scenario.is_bullish``, so it averages in bearish/short R-multiples the live
app can never trade (it is spot/long-only -- trade_scenario._create_scenarios
only ever creates bullish scenarios). Its original conclusion ("0.15 clearly
beats 0.35") did not survive re-measurement: bullish-only, the two are
statistically indistinguishable and 0.15 just trades ~40% less (see
scripts/diagnose_wyckoff_bullish_only.py, and the reverted default in
app/wyckoff/config.py). Kept only as a record of the sweep that was run.
Copy the is_bullish filter from diagnose_wyckoff_bullish_only.py before
reusing any of this.

One-off sweep of trend_efficiency_max, the range-context gate threshold
added this session (app.wyckoff.indicators.efficiency_ratio) to stop Spring/
Upthrust/SC/BC/SOS/SOW/NoDemand/NoSupply firing on ordinary trend noise. It
has only ever run at its starting default (0.35) -- this checks whether a
stricter or looser threshold changes the outcome. Holds
climax_vol_mult/sos_vol_mult/SL_BUFFER_PCT at today's live defaults so this
isolates just this one threshold's effect, same opt/holdout split as
optimize_wyckoff.py. Read-only, opens the real app DB but never commits.
Run from backend/: `python scripts/optimize_wyckoff_range_gate.py`.
"""

from __future__ import annotations

import sys
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
    DEFAULT_CANDIDATE,
    OPT_HOLDOUT_CUTOFF,
    _format_window,
    _load_daily_candles,
    _r_multiple,
    _score_window,
    _stock_tickers_with_enough_history,
)

STRATEGY = "wyckoff"

TREND_EFFICIENCY_MAX_GRID = (0.05, 0.08, 0.10, 0.12, 0.15, 0.20)


def _sweep_one_ticker(session: Session, ticker: str) -> dict[float, dict[str, list[float]]]:
    risk_cfg = settings_service.get_risk_config(session)
    candles = _load_daily_candles(session, ticker)
    symbol = session.get(Symbol, ticker)
    climax, sos, sl_buffer = DEFAULT_CANDIDATE

    ticker_results: dict[float, dict[str, list[float]]] = {
        eff: {"opt": [], "holdout": []} for eff in TREND_EFFICIENCY_MAX_GRID
    }

    original_sl_buffer = trade_scenario.SL_BUFFER_PCT
    trade_scenario.SL_BUFFER_PCT = sl_buffer
    try:
        for eff in TREND_EFFICIENCY_MAX_GRID:
            cfg = WyckoffConfig(climax_vol_mult=climax, sos_vol_mult=sos, trend_efficiency_max=eff)
            result = wyckoff_module.analyze(candles, cfg, None, "vi")
            scenarios = scenario_backtest.walk_events(
                ticker, Timeframe.DAILY, STRATEGY, candles, result.events,
                BULLISH_EVENTS, BEARISH_EVENTS, result.levels, wyckoff_module,
                cfg, None, RANGING_PHASES, symbol, risk_cfg,
            )
            bucket = ticker_results[eff]
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

    return ticker_results


def main() -> None:
    engine = get_engine()
    with Session(engine) as session:
        risk_cfg = settings_service.get_risk_config(session)
        risk_amount = risk_cfg["notional_capital"] * risk_cfg["risk_pct_per_trade"] / 100
        tickers = _stock_tickers_with_enough_history(session)

    results: dict[float, dict[str, list[float]]] = {eff: {"opt": [], "holdout": []} for eff in TREND_EFFICIENCY_MAX_GRID}
    workers = parallel_tickers.default_workers()
    print(f"{len(tickers)} stock ticker(s), {workers} process(es)\n")

    for i, ticker, ticker_results in parallel_tickers.map_tickers(tickers, _sweep_one_ticker, workers=workers):
        print(f"[{i}/{len(tickers)}] {ticker}", file=sys.stderr)
        for eff, windows in ticker_results.items():
            for window, rs in windows.items():
                results[eff][window].extend(rs)

    print("\n=== trend_efficiency_max sweep (climax=2.0 sos=1.5 SL_BUFFER=0.003, live defaults) ===")
    for eff in TREND_EFFICIENCY_MAX_GRID:
        marker = "  <- current live default" if eff == 0.35 else ""
        print(f"\ntrend_efficiency_max={eff}{marker}")
        print(_format_window("opt window    ", _score_window(results[eff]["opt"], risk_amount, risk_cfg["notional_capital"])))
        print(_format_window("holdout window", _score_window(results[eff]["holdout"], risk_amount, risk_cfg["notional_capital"])))


if __name__ == "__main__":
    main()
