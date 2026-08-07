"""One-off test of the two genuine (if modest) leads found this session,
combined: the stricter trend_efficiency_max=0.15 range-gate (the only
change so far whose holdout bootstrap CI crossed into positive territory --
see scripts/optimize_wyckoff_range_gate.py) plus the money-flow entry
filter (a smaller but real improvement on its own -- see
scripts/optimize_wyckoff_money_flow_filter.py). Tests whether they stack.

Backtests against the SAME opt/holdout split as optimize_wyckoff.py.
Read-only, opens the real app DB but never commits. Run from backend/:
`python scripts/optimize_wyckoff_combined.py`.
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
STRICT_TREND_EFFICIENCY_MAX = 0.15
LOOKBACK_GRID = (5, 10, 20)


def _money_flow_in_indices(candles: list) -> set[int]:
    return {e.index for e in money_flow.detect_money_flow_events(candles) if e.type == money_flow.MONEY_FLOW_IN}


def _has_recent_inflow(event_index: int, in_indices: set[int], lookback: int) -> bool:
    return any(event_index - lookback <= idx <= event_index for idx in in_indices)


def _sweep_one_ticker(session: Session, ticker: str) -> dict:
    risk_cfg = settings_service.get_risk_config(session)
    candles = _load_daily_candles(session, ticker)
    symbol = session.get(Symbol, ticker)
    climax, sos, _ = DEFAULT_CANDIDATE
    cfg = WyckoffConfig(climax_vol_mult=climax, sos_vol_mult=sos, trend_efficiency_max=STRICT_TREND_EFFICIENCY_MAX)

    result = wyckoff_module.analyze(candles, cfg, None, "vi")
    in_indices = _money_flow_in_indices(candles)

    variants: list = ["range_gate_only"] + list(LOOKBACK_GRID)
    ticker_results: dict = {v: {"opt": [], "holdout": []} for v in variants}

    for variant in variants:
        if variant == "range_gate_only":
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

    variants: list = ["range_gate_only"] + list(LOOKBACK_GRID)
    results: dict = {v: {"opt": [], "holdout": []} for v in variants}
    workers = parallel_tickers.default_workers()
    print(f"{len(tickers)} stock ticker(s), {workers} process(es)\n")

    for i, ticker, ticker_results in parallel_tickers.map_tickers(tickers, _sweep_one_ticker, workers=workers):
        print(f"[{i}/{len(tickers)}] {ticker}", file=sys.stderr)
        for v, windows in ticker_results.items():
            for window, rs in windows.items():
                results[v][window].extend(rs)

    print(f"\n=== trend_efficiency_max={STRICT_TREND_EFFICIENCY_MAX} + money-flow filter combined ===")
    for v in variants:
        label = (
            f"trend_efficiency_max={STRICT_TREND_EFFICIENCY_MAX} alone (no money-flow filter)"
            if v == "range_gate_only" else f"+ money-flow filter, lookback={v} bars"
        )
        print(f"\n{label}")
        print(_format_window("opt window    ", _score_window(results[v]["opt"], risk_amount, risk_cfg["notional_capital"])))
        print(_format_window("holdout window", _score_window(results[v]["holdout"], risk_amount, risk_cfg["notional_capital"])))


if __name__ == "__main__":
    main()
