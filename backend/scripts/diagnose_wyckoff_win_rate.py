"""Validates the current live Wyckoff config (trend_efficiency_max=0.15,
MIN_RR_RATIO=3.0 -- both adopted this session on VN30-only samples as small
as n=25) against the FULL HOSE/HNX universe, and breaks results down by
event_type to see whether some entry-trigger types (Spring/SC/SOS/LPS) carry
a meaningfully higher win rate than others -- a further, data-driven lever
for raising win rate beyond the R:R filter alone, instead of continuing to
slice an already-small VN30 sample thinner.

Uses today's actual live app.wyckoff module state (no monkey-patching) --
this is a validation/breakdown pass, not a parameter sweep. Read-only, opens
the real app DB but never commits. Run from backend/:
`python scripts/diagnose_wyckoff_win_rate.py [--vn30]`.
"""

from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlmodel import Session  # noqa: E402

import app.wyckoff as wyckoff_module  # noqa: E402
from app.db import get_engine  # noqa: E402
from app.models import Symbol, Timeframe  # noqa: E402
from app.services import scenario_backtest, settings_service  # noqa: E402
from app.wyckoff import BEARISH_EVENTS, BULLISH_EVENTS, RANGING_PHASES  # noqa: E402
from app.wyckoff.config import DEFAULT_CONFIG  # noqa: E402
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


def _sweep_one_ticker(session: Session, ticker: str) -> dict:
    risk_cfg = settings_service.get_risk_config(session)
    candles = _load_daily_candles(session, ticker)
    symbol = session.get(Symbol, ticker)

    result = wyckoff_module.analyze(candles, DEFAULT_CONFIG, None, "vi")
    scenarios = scenario_backtest.walk_events(
        ticker, Timeframe.DAILY, STRATEGY, candles, result.events,
        BULLISH_EVENTS, BEARISH_EVENTS, result.levels, wyckoff_module,
        DEFAULT_CONFIG, None, RANGING_PHASES, symbol, risk_cfg,
    )

    by_type: dict = defaultdict(lambda: {"opt": [], "holdout": []})
    overall: dict = {"opt": [], "holdout": []}
    for s in scenarios:
        if not s.is_bullish or s.status not in ("hit_tp", "hit_sl", "expired") or s.exit_price is None:
            continue
        r = _r_multiple(s, risk_cfg)
        if r is None:
            continue
        window = "opt" if s.event_ts < OPT_HOLDOUT_CUTOFF else "holdout"
        by_type[s.event_type][window].append(r)
        overall[window].append(r)

    return {"overall": overall, "by_type": dict(by_type)}


def main() -> None:
    vn30_only = "--vn30" in sys.argv
    engine = get_engine()
    with Session(engine) as session:
        risk_cfg = settings_service.get_risk_config(session)
        risk_amount = risk_cfg["notional_capital"] * risk_cfg["risk_pct_per_trade"] / 100
        tickers = _stock_tickers_with_enough_history(session, vn30_only=vn30_only)

    overall_results: dict = {"opt": [], "holdout": []}
    by_type_results: dict = defaultdict(lambda: {"opt": [], "holdout": []})
    workers = parallel_tickers.default_workers()
    scope = "VN30" if vn30_only else "full HOSE/HNX"
    print(f"{len(tickers)} stock ticker(s) ({scope}), {workers} process(es)\n")

    for i, ticker, ticker_results in parallel_tickers.map_tickers(tickers, _sweep_one_ticker, workers=workers):
        print(f"[{i}/{len(tickers)}] {ticker}", file=sys.stderr)
        for window, rs in ticker_results["overall"].items():
            overall_results[window].extend(rs)
        for event_type, windows in ticker_results["by_type"].items():
            for window, rs in windows.items():
                by_type_results[event_type][window].extend(rs)

    print(f"\n=== Current live config validation ({scope}, trend_efficiency_max={DEFAULT_CONFIG.trend_efficiency_max}, MIN_RR_RATIO={wyckoff_module.MIN_RR_RATIO}) ===")
    print("\nOverall")
    print(_format_window("opt window    ", _score_window(overall_results["opt"], risk_amount, risk_cfg["notional_capital"])))
    print(_format_window("holdout window", _score_window(overall_results["holdout"], risk_amount, risk_cfg["notional_capital"])))

    print("\n=== Breakdown by event type (holdout window only) ===")
    ranked = sorted(
        by_type_results.items(),
        key=lambda kv: (_score_window(kv[1]["holdout"], risk_amount, risk_cfg["notional_capital"])["mean_r"] or -999),
        reverse=True,
    )
    for event_type, windows in ranked:
        print(f"\n{event_type}")
        print(_format_window("holdout window", _score_window(windows["holdout"], risk_amount, risk_cfg["notional_capital"])))


if __name__ == "__main__":
    main()
