"""One-off backtest: validate the Sonic R strategy's own standalone edge --
this has never had a dedicated train/holdout backtest before, unlike Wyckoff
(optimize_wyckoff.py) and SMC (backtest_smc.py). The only prior evidence was
a side effect of the Accumulation confluence-filter experiment's "no
confluence" bucket on VN30, which showed NO statistically significant edge
(bootstrap CI crossing zero) -- but that was never a deliberate, dedicated
Sonic R validation run.

Same train/holdout split + chronological-breakdown discipline as every
other backtest this session: a pooled holdout number alone is never trusted
(see the removed RSI Spring experiment -- it looked like a real edge pooled,
but was concentrated in one stretch).

Bullish-only (spot-only, matches every other backtest this session's
is_bullish filter).

VN30-only by default (fast sanity check); pass --full for the full
HOSE/HNX universe once VN30 looks good.

Read-only, writes nothing. Run from backend/: `python scripts/backtest_sonicr.py [--full]`.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlmodel import Session  # noqa: E402

from app import sonicr  # noqa: E402
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

STRATEGY = "sonicr"


def _backtest_one_ticker(session, ticker: str) -> list[tuple]:
    """One ticker's scored trades as (event_type, event_ts, r) -- the unit
    parallel_tickers farms out to a worker process. The parent buckets them
    by event type and window."""
    risk_cfg = settings_service.get_risk_config(session)
    candles = _load_daily_candles(session, ticker)
    symbol = session.get(Symbol, ticker)

    result = sonicr.analyze(candles, sonicr.DEFAULT_CONFIG, None, "vi")
    scenarios = scenario_backtest.walk_events(
        ticker, Timeframe.DAILY, STRATEGY, candles, result.events,
        sonicr.BULLISH_EVENTS, sonicr.BEARISH_EVENTS, result.levels, sonicr,
        sonicr.DEFAULT_CONFIG, None, sonicr.RANGING_PHASES, symbol, risk_cfg,
    )

    rows: list[tuple] = []
    for s in scenarios:
        if not s.is_bullish or s.status not in ("hit_tp", "hit_sl", "expired") or s.exit_price is None:
            continue
        if s.event_type not in sonicr.BULLISH_EVENTS:
            continue
        r = _r_multiple(s, risk_cfg)
        if r is not None:
            rows.append((s.event_type, s.event_ts, r))
    return rows


def main() -> None:
    engine = get_engine()
    with Session(engine) as session:
        risk_cfg = settings_service.get_risk_config(session)
        risk_amount = risk_cfg["notional_capital"] * risk_cfg["risk_pct_per_trade"] / 100

        # VN30-only first pass by default (matches optimize_wyckoff.py's own
        # precedent): a fast sanity check before spending the time on the
        # full HOSE/HNX universe. `--full` runs the full universe instead;
        # `--limit=N` caps how many of those tickers are actually walked.
        vn30_only = "--full" not in sys.argv
        tickers = _stock_tickers_with_enough_history(session, vn30_only=vn30_only)
        limit_arg = next((a for a in sys.argv if a.startswith("--limit=")), None)
        if limit_arg:
            tickers = tickers[: int(limit_arg.split("=", 1)[1])]

    # Broken down per bullish event_type -- the docstring in
    # app.sonicr.events claims DragonCrossUp/SonicCrossUp are "raw signals
    # (informational, always emitted)" while SonicEntryLong is "the actual
    # optimized entry signal" (Dragon+CCI+MTF+pullback confirmed). All 3
    # currently qualify identically for trade creation (see BULLISH_EVENTS),
    # which may be diluting the fully-confirmed signal's own edge with the
    # two weaker raw ones pooled in.
    # sorted() because BULLISH_EVENTS is a set: without it the report's
    # section order changed on every run (Python randomizes string hashing
    # per process), which made two runs impossible to diff.
    buckets: dict[str, dict[str, list[tuple]]] = {
        event_type: {"opt": [], "holdout": []} for event_type in sorted(sonicr.BULLISH_EVENTS)
    }
    workers = parallel_tickers.default_workers()
    print(f"{len(tickers)} ticker(s), {workers} process(es)\n")

    for i, ticker, rows in parallel_tickers.map_tickers(tickers, _backtest_one_ticker, workers=workers):
        print(f"[{i}/{len(tickers)}] {ticker}", file=sys.stderr)
        for event_type, event_ts, r in rows:
            window = "opt" if event_ts < OPT_HOLDOUT_CUTOFF else "holdout"
            buckets[event_type][window].append((event_ts, r))

    for event_type in buckets:
        print(f"\n=== Sonic R -- {event_type} (bullish only) ===")
        for label, key in (("opt window    ", "opt"), ("holdout window", "holdout")):
            dated = sorted(buckets[event_type][key], key=lambda pair: pair[0])
            r_multiples = [r for _, r in dated]
            print(_format_window(label, _score_window(r_multiples, risk_amount, risk_cfg["notional_capital"])))
        print("  holdout window, chronological breakdown (is the result concentrated in one stretch?):")
        _chronological_breakdown(buckets[event_type]["holdout"], N_TIME_SLICES)


if __name__ == "__main__":
    main()
