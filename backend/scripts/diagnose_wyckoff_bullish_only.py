"""Corrected re-check: every optimize_wyckoff*.py script this session used
scenario_backtest.walk_events()'s scenario list WITHOUT filtering
is_bullish -- but the live app's real scenario-creation path
(trade_scenario._create_scenarios) ONLY ever creates BULLISH scenarios
(spot-only, no short-selling). walk_events (built for backtesting/stats) has
NO such restriction and happily resolves bearish candidates too, so every
mean_r/win_rate number reported this session was contaminated with
untradeable short R-multiples.

This re-checks, BULLISH-ONLY: the ORIGINAL baseline (trend_efficiency_max=
0.35, today's starting point) against the range-gate change alone (0.15,
MIN_RR_RATIO already reverted -- see app/wyckoff/__init__.py's note on why).

Read-only, opens the real app DB but never commits. Run from backend/:
`python scripts/diagnose_wyckoff_bullish_only.py [--vn30]`.
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
    OPT_HOLDOUT_CUTOFF,
    _format_window,
    _load_daily_candles,
    _r_multiple,
    _score_window,
    _stock_tickers_with_enough_history,
)

STRATEGY = "wyckoff"

VARIANTS = {
    "original_baseline_035": WyckoffConfig(climax_vol_mult=2.0, sos_vol_mult=1.5, trend_efficiency_max=0.35),
    "range_gate_015_only": WyckoffConfig(climax_vol_mult=2.0, sos_vol_mult=1.5, trend_efficiency_max=0.15),
}


def _sweep_one_ticker(session: Session, ticker: str) -> dict:
    risk_cfg = settings_service.get_risk_config(session)
    candles = _load_daily_candles(session, ticker)
    symbol = session.get(Symbol, ticker)

    ticker_results: dict = {name: {"opt": [], "holdout": []} for name in VARIANTS}
    for name, cfg in VARIANTS.items():
        result = wyckoff_module.analyze(candles, cfg, None, "vi")
        scenarios = scenario_backtest.walk_events(
            ticker, Timeframe.DAILY, STRATEGY, candles, result.events,
            BULLISH_EVENTS, BEARISH_EVENTS, result.levels, wyckoff_module,
            cfg, None, RANGING_PHASES, symbol, risk_cfg,
        )
        bucket = ticker_results[name]
        for s in scenarios:
            if not s.is_bullish or s.status not in ("hit_tp", "hit_sl", "expired") or s.exit_price is None:
                continue
            r = _r_multiple(s, risk_cfg)
            if r is None:
                continue
            window = "opt" if s.event_ts < OPT_HOLDOUT_CUTOFF else "holdout"
            bucket[window].append(r)

    return ticker_results


def main() -> None:
    vn30_only = "--vn30" in sys.argv
    engine = get_engine()
    with Session(engine) as session:
        risk_cfg = settings_service.get_risk_config(session)
        risk_amount = risk_cfg["notional_capital"] * risk_cfg["risk_pct_per_trade"] / 100
        tickers = _stock_tickers_with_enough_history(session, vn30_only=vn30_only)

    results: dict = {name: {"opt": [], "holdout": []} for name in VARIANTS}
    workers = parallel_tickers.default_workers()
    scope = "VN30" if vn30_only else "full HOSE/HNX"
    print(f"{len(tickers)} stock ticker(s) ({scope}), {workers} process(es)\n")

    for i, ticker, ticker_results in parallel_tickers.map_tickers(tickers, _sweep_one_ticker, workers=workers):
        print(f"[{i}/{len(tickers)}] {ticker}", file=sys.stderr)
        for name, windows in ticker_results.items():
            for window, rs in windows.items():
                results[name][window].extend(rs)

    print(f"\n=== BULLISH-ONLY (tradeable) comparison, corrected methodology, {scope} ===")
    for name in VARIANTS:
        print(f"\n{name}")
        print(_format_window("opt window    ", _score_window(results[name]["opt"], risk_amount, risk_cfg["notional_capital"])))
        print(_format_window("holdout window", _score_window(results[name]["holdout"], risk_amount, risk_cfg["notional_capital"])))


if __name__ == "__main__":
    main()
