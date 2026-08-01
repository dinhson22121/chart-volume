"""One-off backtest: validate the SMC (Smart Money Concept) strategy's own
standalone edge -- this has never had a dedicated train/holdout backtest
before, unlike Wyckoff (optimize_wyckoff.py) and the removed Accumulation
experiment (backtest_accumulation.py). The only prior evidence was a side
effect of the Accumulation confluence-filter experiment's "no confluence"
bucket, which happened to be close to SMC's own unfiltered baseline and
looked strong (VN30 holdout mean_r=+1.39, bootstrap CI entirely positive,
walk_forward_consistency=1.00) -- but that was never a deliberate,
dedicated SMC validation run.

Same train/holdout split + chronological-breakdown discipline as every
other backtest this session: a pooled holdout number alone is never trusted
(see the removed RSI Spring experiment -- it looked like a real edge pooled,
but was concentrated in one stretch).

Bullish-only (spot-only, matches every other backtest this session's
is_bullish filter).

VN30-only by default (fast sanity check); pass --full for the full
HOSE/HNX universe once VN30 looks good.

Read-only, writes nothing. Run from backend/: `python scripts/backtest_smc.py [--full]`.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlmodel import Session  # noqa: E402

from app import smc  # noqa: E402
from app.db import get_engine  # noqa: E402
from app.models import Symbol, Timeframe  # noqa: E402
from app.services import scenario_backtest, settings_service  # noqa: E402
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

STRATEGY = "smc"


def _backtest_one_ticker(session, ticker: str) -> list[tuple]:
    """One ticker's scored trades -- the unit parallel_tickers farms out to a
    worker process. The parent decides which window each row lands in."""
    risk_cfg = settings_service.get_risk_config(session)
    candles = _load_daily_candles(session, ticker)
    symbol = session.get(Symbol, ticker)

    result = smc.analyze(candles, smc.DEFAULT_CONFIG, None, "vi")
    scenarios = scenario_backtest.walk_events(
        ticker, Timeframe.DAILY, STRATEGY, candles, result.events,
        smc.BULLISH_EVENTS, smc.BEARISH_EVENTS, result.levels, smc,
        smc.DEFAULT_CONFIG, None, smc.RANGING_PHASES, symbol, risk_cfg,
    )

    rows: list[tuple] = []
    for s in scenarios:
        if not s.is_bullish or s.status not in ("hit_tp", "hit_sl", "expired") or s.exit_price is None:
            continue
        r = _r_multiple(s, risk_cfg)
        if r is not None:
            rows.append((s.event_ts, r))
    return rows


def main() -> None:
    engine = get_engine()
    with Session(engine) as session:
        risk_cfg = settings_service.get_risk_config(session)
        risk_amount = risk_cfg["notional_capital"] * risk_cfg["risk_pct_per_trade"] / 100

        # VN30-only first pass by default (matches optimize_wyckoff.py's own
        # precedent): a fast sanity check before spending the time on the
        # full HOSE/HNX universe. `--full` runs the full universe instead.
        vn30_only = "--full" not in sys.argv
        tickers = _stock_tickers_with_enough_history(session, vn30_only=vn30_only)

    buckets: dict[str, list[tuple]] = {"opt": [], "holdout": []}
    workers = parallel_tickers.default_workers()
    print(f"{len(tickers)} ticker(s), {workers} process(es)\n")

    for i, ticker, rows in parallel_tickers.map_tickers(tickers, _backtest_one_ticker, workers=workers):
        print(f"[{i}/{len(tickers)}] {ticker}", file=sys.stderr)
        for event_ts, r in rows:
            window = "opt" if event_ts < OPT_HOLDOUT_CUTOFF else "holdout"
            buckets[window].append((event_ts, r))

    print("\n=== SMC (bullish only) ===")
    for label, key in (("opt window    ", "opt"), ("holdout window", "holdout")):
        dated = sorted(buckets[key], key=lambda pair: pair[0])
        r_multiples = [r for _, r in dated]
        print(_format_window(label, _score_window(r_multiples, risk_amount, risk_cfg["notional_capital"])))
    print("  holdout window, chronological breakdown (is the result concentrated in one stretch?):")
    _chronological_breakdown(buckets["holdout"], N_TIME_SLICES)


if __name__ == "__main__":
    main()
