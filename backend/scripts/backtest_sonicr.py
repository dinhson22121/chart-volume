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
        print(f"{len(tickers)} ticker(s)\n")

        # Broken down per bullish event_type -- the docstring in
        # app.sonicr.events claims DragonCrossUp/SonicCrossUp are "raw
        # signals (informational, always emitted)" while SonicEntryLong is
        # "the actual optimized entry signal" (Dragon+CCI+MTF+pullback
        # confirmed). All 3 currently qualify identically for trade creation
        # (see BULLISH_EVENTS), which may be diluting the fully-confirmed
        # signal's own edge with the two weaker raw ones pooled in.
        buckets: dict[str, dict[str, list[tuple]]] = {
            event_type: {"opt": [], "holdout": []} for event_type in sonicr.BULLISH_EVENTS
        }

        for i, ticker in enumerate(tickers, 1):
            candles = _load_daily_candles(session, ticker)
            symbol = session.get(Symbol, ticker)
            print(f"[{i}/{len(tickers)}] {ticker} ({len(candles)} bars)", file=sys.stderr)

            result = sonicr.analyze(candles, sonicr.DEFAULT_CONFIG, None, "vi")
            scenarios = scenario_backtest.walk_events(
                ticker, Timeframe.DAILY, STRATEGY, candles, result.events,
                sonicr.BULLISH_EVENTS, sonicr.BEARISH_EVENTS, result.levels, sonicr,
                sonicr.DEFAULT_CONFIG, None, sonicr.RANGING_PHASES, symbol, risk_cfg,
            )

            for s in scenarios:
                if not s.is_bullish or s.status not in ("hit_tp", "hit_sl", "expired") or s.exit_price is None:
                    continue
                if s.event_type not in buckets:
                    continue
                r = _r_multiple(s, risk_cfg)
                if r is None:
                    continue
                window = "opt" if s.event_ts < OPT_HOLDOUT_CUTOFF else "holdout"
                buckets[s.event_type][window].append((s.event_ts, r))

        for event_type in sonicr.BULLISH_EVENTS:
            print(f"\n=== Sonic R -- {event_type} (bullish only) ===")
            for label, key in (("opt window    ", "opt"), ("holdout window", "holdout")):
                dated = sorted(buckets[event_type][key], key=lambda pair: pair[0])
                r_multiples = [r for _, r in dated]
                print(_format_window(label, _score_window(r_multiples, risk_amount, risk_cfg["notional_capital"])))
            print("  holdout window, chronological breakdown (is the result concentrated in one stretch?):")
            _chronological_breakdown(buckets[event_type]["holdout"], N_TIME_SLICES)


if __name__ == "__main__":
    main()
