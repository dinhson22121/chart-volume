"""One-off backtest: does scaling out at a profit target beat the trailing
stop alone?

Until now a scenario could only close three ways: the (possibly trailed)
stop, max_bars expiry, or nothing. This sweeps a profit target running
alongside it -- an R multiple and/or the measured-move take_profit already
stored on every scenario -- which takes PARTIAL_EXIT_FRACTION off and pulls
the stop to breakeven, leaving the rest to run. Ships OFF; see
trade_scenario's scale-out block for the measured result and why.

Worth being suspicious of the target-based variants specifically: a fixed
take-profit exit model was already backtested and REJECTED once in favour
of the pure trail (see TRAIL_ATR_MULT's note). Reintroducing targets as one
racer among several is a different proposition, but it has to clear the
same bar, and "it protects profit" is a story, not evidence.

R-multiples blend both legs for scaled-out scenarios (see
trade_scenario.realized_r_multiple), so these numbers stay directly
comparable to every earlier sweep's.

Bullish-only (spot-only). VN30-only by default; --full for the whole
HOSE/HNX universe. --strategy=<key> to sweep a strategy other than SMC.
Read-only, writes nothing.
Run from backend/: `python scripts/backtest_exit_conditions.py [--full] [--strategy=wyckoff]`.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlmodel import Session  # noqa: E402

from app.db import get_engine  # noqa: E402
from app.models import Symbol, Timeframe  # noqa: E402
from app.services import scenario_backtest, settings_service, trade_scenario  # noqa: E402
from app.strategies import registry as strategy_registry  # noqa: E402
from scripts import parallel_tickers  # noqa: E402
from scripts.optimize_wyckoff import (  # noqa: E402
    N_TIME_SLICES,
    OPT_HOLDOUT_CUTOFF,
    _chronological_breakdown,
    _format_window,
    _load_daily_candles,
    _r_multiple,
    _score_window,
    _stock_tickers_with_enough_history,
)

# name -> (partial_fraction, partial_r_multiple, partial_at_take_profit)
#
# A fourth condition -- close the remainder on a bearish event -- was swept
# here too and came back byte-identical to the baseline on all 30 tickers:
# it never fired once, because a bearish signal only confirms at bar close
# while the stop fills intrabar. It was removed from the codebase rather
# than shipped inert; see trade_scenario's scale-out block.
VARIANTS: dict[str, tuple] = {
    "baseline (trailing stop only)": (0.0, 0.0, False),
    "scale 50% @ 2R":                (0.5, 2.0, False),
    "scale 50% @ 3R":                (0.5, 3.0, False),
    "scale 50% @ measured-move TP":  (0.5, 0.0, True),
    "scale 50% @ nearest(2R, TP)":   (0.5, 2.0, True),
}

_KNOBS = (
    "PARTIAL_EXIT_FRACTION",
    "PARTIAL_EXIT_R_MULTIPLE",
    "PARTIAL_EXIT_AT_TAKE_PROFIT",
)


def _sweep_one_ticker(session, ticker: str, strategy: str) -> dict[str, list]:
    """One ticker through every variant -- the unit parallel_tickers farms
    out to a worker process. ``strategy`` is passed in rather than re-parsed
    from argv because a spawned worker doesn't inherit the parent's."""
    module = strategy_registry.get_strategy(strategy)
    risk_cfg = settings_service.get_risk_config(session)
    cfg = settings_service.get_strategy_config(session, strategy)
    candles = _load_daily_candles(session, ticker)
    symbol = session.get(Symbol, ticker)

    collected: dict[str, list] = {name: [] for name in VARIANTS}
    originals = tuple(getattr(trade_scenario, k) for k in _KNOBS)

    # The phase-before-event gate re-runs analyze() per qualifying event --
    # the dominant cost, and identical across variants here (none of them
    # touch detection). Memoized by truncated length, exactly as
    # optimize_wyckoff.py does.
    analyze_cache: dict[int, object] = {}
    original_analyze = module.analyze

    def _cached_analyze(candles_arg, *a, **k):
        key = len(candles_arg)
        if key not in analyze_cache:
            analyze_cache[key] = original_analyze(candles_arg, *a, **k)
        return analyze_cache[key]

    module.analyze = _cached_analyze
    try:
        result = module.analyze(candles, cfg, None, "vi")

        for name, values in VARIANTS.items():
            for knob, value in zip(_KNOBS, values):
                setattr(trade_scenario, knob, value)
            try:
                scenarios = scenario_backtest.walk_events(
                    ticker, Timeframe.DAILY, strategy, candles, result.events,
                    module.BULLISH_EVENTS, module.BEARISH_EVENTS, result.levels, module,
                    cfg, None, module.RANGING_PHASES, symbol, risk_cfg,
                )
                # R-multiples must be read while the knobs are still set:
                # realized_r_multiple blends the two exit legs using
                # PARTIAL_EXIT_FRACTION as it stands right now.
                for s in scenarios:
                    if not s.is_bullish or s.status not in ("hit_tp", "hit_sl", "expired"):
                        continue
                    if s.exit_price is None:
                        continue
                    r = _r_multiple(s, risk_cfg)
                    if r is not None:
                        collected[name].append((s.event_ts, r))
            finally:
                for knob, value in zip(_KNOBS, originals):
                    setattr(trade_scenario, knob, value)
    finally:
        module.analyze = original_analyze

    return collected


def main() -> None:
    strategy_arg = next((a for a in sys.argv if a.startswith("--strategy=")), None)
    strategy = strategy_arg.split("=", 1)[1] if strategy_arg else "smc"

    engine = get_engine()
    with Session(engine) as session:
        risk_cfg = settings_service.get_risk_config(session)
        risk_amount = risk_cfg["notional_capital"] * risk_cfg["risk_pct_per_trade"] / 100

        vn30_only = "--full" not in sys.argv
        tickers = _stock_tickers_with_enough_history(session, vn30_only=vn30_only)

    buckets: dict[str, dict[str, list]] = {name: {"opt": [], "holdout": []} for name in VARIANTS}
    workers = parallel_tickers.default_workers()
    print(f"strategy={strategy}  {len(tickers)} ticker(s), {len(VARIANTS)} variant(s), {workers} process(es)\n")

    for i, ticker, rows in parallel_tickers.map_tickers(
        tickers, _sweep_one_ticker, args=(strategy,), workers=workers
    ):
        print(f"[{i}/{len(tickers)}] {ticker}", file=sys.stderr)
        for name, pairs in rows.items():
            for event_ts, r in pairs:
                window = "opt" if event_ts < OPT_HOLDOUT_CUTOFF else "holdout"
                buckets[name][window].append((event_ts, r))

    for name in VARIANTS:
        print(f"\n=== {name} ===")
        for win_label, key in (("opt window    ", "opt"), ("holdout window", "holdout")):
            dated = sorted(buckets[name][key], key=lambda pair: pair[0])
            r_multiples = [r for _, r in dated]
            print(_format_window(win_label, _score_window(r_multiples, risk_amount, risk_cfg["notional_capital"])))
        print("  holdout window, chronological breakdown:")
        _chronological_breakdown(buckets[name]["holdout"], N_TIME_SLICES)


if __name__ == "__main__":
    main()
