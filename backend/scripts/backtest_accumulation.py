"""One-off backtest: the experimental volume-breakout signal
(app/wyckoff/accumulation.py, adapted from FiinTrade's public "Tích lũy" TA
strategy methodology) against real HOSE/HNX daily history, using the same
walk_events()/opt-holdout-split discipline as optimize_wyckoff.py and
backtest_rsi_spring.py -- including the chronological-breakdown check that
backtest_rsi_spring.py's own history shows is NOT optional: an earlier signal
this session (RSI Spring) looked like a real edge in the pooled holdout
number, but turned out to be concentrated entirely in a 6-month stretch, with
the most recent slices trending negative. Never trust a pooled number alone.

Bullish-only by design intent (spot trading has no short-selling -- see this
session's is_bullish Trust Layer filter), but reports the bearish side
(Volume Distribution) too for reference since the detector is symmetric.

Read-only, writes nothing. Run from backend/: `python scripts/backtest_accumulation.py`.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlmodel import Session  # noqa: E402

from app.db import get_engine  # noqa: E402
from app.models import Symbol, Timeframe  # noqa: E402
from app.services import scenario_backtest, settings_service  # noqa: E402
from app.wyckoff import accumulation  # noqa: E402
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

STRATEGY = "accumulation"


def main() -> None:
    engine = get_engine()
    with Session(engine) as session:
        risk_cfg = settings_service.get_risk_config(session)
        risk_amount = risk_cfg["notional_capital"] * risk_cfg["risk_pct_per_trade"] / 100

        tickers = _stock_tickers_with_enough_history(session, vn30_only=False)
        print(f"{len(tickers)} ticker(s)\n")

        buckets = {
            "bullish": {"opt": [], "holdout": []},
            "bearish": {"opt": [], "holdout": []},
        }

        for i, ticker in enumerate(tickers, 1):
            candles = _load_daily_candles(session, ticker)
            symbol = session.get(Symbol, ticker)
            print(f"[{i}/{len(tickers)}] {ticker} ({len(candles)} bars)", file=sys.stderr)

            result = accumulation.analyze(candles, None, None, "vi")

            scenarios = scenario_backtest.walk_events(
                ticker, Timeframe.DAILY, STRATEGY, candles, result.events,
                accumulation.BULLISH_EVENTS, accumulation.BEARISH_EVENTS, result.levels, accumulation,
                None, None, accumulation.RANGING_PHASES, symbol, risk_cfg,
            )

            for s in scenarios:
                if s.status not in ("hit_tp", "hit_sl", "expired") or s.exit_price is None:
                    continue
                r = _r_multiple(s, risk_cfg)
                if r is None:
                    continue
                direction = "bullish" if s.is_bullish else "bearish"
                window = "opt" if s.event_ts < OPT_HOLDOUT_CUTOFF else "holdout"
                buckets[direction][window].append((s.event_ts, r))

        for direction in ("bullish", "bearish"):
            label_name = "Accumulation" if direction == "bullish" else "Distribution"
            print(f"\n=== Volume {label_name} ({direction}) ===")
            for label, key in (("opt window    ", "opt"), ("holdout window", "holdout")):
                dated = sorted(buckets[direction][key], key=lambda pair: pair[0])
                r_multiples = [r for _, r in dated]
                print(_format_window(label, _score_window(r_multiples, risk_amount, risk_cfg["notional_capital"])))
            print("  holdout window, chronological breakdown (is the result concentrated in one stretch?):")
            _chronological_breakdown(buckets[direction]["holdout"], N_TIME_SLICES)


if __name__ == "__main__":
    main()
