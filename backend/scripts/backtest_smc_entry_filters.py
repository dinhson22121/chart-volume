"""One-off backtest: do the SMC execution spec's entry-quality rules beat
this app's validated SMC baseline? (The answer, measured: yes -- see
app.smc.phase's POI_ZONE_THRESHOLD_PCT / MIN_RR_RATIO, now enabled by
default at the settings this script picked.)

The spec prescribed three rules the app didn't have:
  1. POI within Discount/Premium -- only buy in the lower part of the active
     range, only sell in the upper (POI_ZONE_THRESHOLD_PCT).
  2. Minimum 1:3 reward:risk (MIN_RR_RATIO).
  3. Stop at the refined Order Block's own zone edge. NOT swept here: the
     app already did exactly this, since an Order Block event's index IS its
     anchor candle, so candles[event.index].low/high are that zone's edges.
     A flag for it was implemented and measured byte-identical to baseline,
     then removed as redundant -- see app.smc.phase's own note.

Both knobs live on the SMC module and are read by trade_scenario through
getattr, so this sweep patches them on `smc` -- Wyckoff and Sonic R declare
neither and are unaffected by anything measured here.

Both surviving rules shipped disabled and were swept against a genuine
no-filter baseline before their defaults flipped -- the liquidity-sweep
gate shipped-then-reverted earlier this session is exactly why nothing here
goes live on the spec's word alone.

Bullish-only (spot-only, matches every other backtest this session).
VN30-only by default; pass --full for the full HOSE/HNX universe.
Read-only, writes nothing.
Run from backend/: `python scripts/backtest_smc_entry_filters.py [--full]`.
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

_OB_FVG = frozenset(
    {"BullishOB", "BearishOB", "SwingBullishOB", "SwingBearishOB", "BullishFVG", "BearishFVG"}
)
_ALL_TYPES: frozenset[str] = frozenset()  # empty == "apply to every qualifying type"

# name -> (poi_threshold_pct, poi_filter_types, min_rr_ratio)
#
# Round 1 (each filter alone + the spec's literal combo) established that
# every POI and RR variant beats the no-filter baseline on holdout mean_r
# AND on the bootstrap CI's lower bound, with walk_forward 1.00 throughout.
# Round 2 (this grid) searched the POI-scope x RR combinations the spec's
# own combo didn't cover, since POI applied to ALL entry types had beaten
# its OB/FVG-only scope when each ran alone.
#
# Measured on VN30 holdout (30 tickers), best-to-worst by CI lower bound:
#   POI-third-ALL+RR3  n= 58  mean_r=+4.25  median=+1.60  ci=[0.348, 0.716]
#   POI-half-ALL +RR3  n= 65  mean_r=+3.82  median=+1.21  ci=[0.328, 0.651]  <- shipped
#   POI-third-ALL+RR2  n= 79  mean_r=+3.12  median=+0.71  ci=[0.286, 0.567]
#   POI-half-ALL +RR2  n= 87  mean_r=+2.88  median=+0.67  ci=[0.266, 0.534]
#   spec combined      n= 86  mean_r=+2.72  median=+0.43  ci=[0.258, 0.520]
#   baseline           n=201  mean_r=+1.20  median=-0.05  ci=[0.158, 0.321]
# The shipped pick is deliberately not the top row -- see app.smc.phase's
# note on why 0.5/3.0 (both doctrine, neither fitted) was preferred over a
# 0.33 threshold whose edge over 0.5 was inside the noise at n<70.
VARIANTS: dict[str, tuple] = {
    "baseline (no filters)":            (0.0,  _OB_FVG,    0.0),
    "spec combined (POI-OBFVG + RR3)":  (0.5,  _OB_FVG,    3.0),
    "POI-half-ALL + RR3 (shipped)":     (0.5,  _ALL_TYPES, 3.0),
    "POI-third-ALL + RR3":              (0.33, _ALL_TYPES, 3.0),
    "POI-half-ALL + RR2":               (0.5,  _ALL_TYPES, 2.0),
    "POI-third-ALL + RR2":              (0.33, _ALL_TYPES, 2.0),
}


def main() -> None:
    engine = get_engine()
    with Session(engine) as session:
        risk_cfg = settings_service.get_risk_config(session)
        risk_amount = risk_cfg["notional_capital"] * risk_cfg["risk_pct_per_trade"] / 100

        vn30_only = "--full" not in sys.argv
        tickers = _stock_tickers_with_enough_history(session, vn30_only=vn30_only)
        print(f"{len(tickers)} ticker(s), {len(VARIANTS)} variant(s)\n")

        buckets: dict[str, dict[str, list]] = {
            name: {"opt": [], "holdout": []} for name in VARIANTS
        }

        originals = (
            smc.POI_ZONE_THRESHOLD_PCT,
            smc.POI_ZONE_FILTER_TYPES,
            smc.MIN_RR_RATIO,
        )

        for i, ticker in enumerate(tickers, 1):
            candles = _load_daily_candles(session, ticker)
            symbol = session.get(Symbol, ticker)
            print(f"[{i}/{len(tickers)}] {ticker} ({len(candles)} bars)", file=sys.stderr)

            # _build_scenario_candidate's phase-before-event gate re-runs
            # analyze() on the truncated pre-event window for EVERY qualifying
            # event -- by far the most expensive step, and its result is
            # identical across all variants here (none of them touch SMCConfig
            # or detection). Memoize it keyed by truncated length, exactly as
            # optimize_wyckoff.py does for its own sweep.
            analyze_cache: dict[int, object] = {}
            original_analyze = smc.analyze

            def _cached_analyze(candles_arg, *a, **k):
                key = len(candles_arg)
                if key not in analyze_cache:
                    analyze_cache[key] = original_analyze(candles_arg, *a, **k)
                return analyze_cache[key]

            smc.analyze = _cached_analyze
            try:
                result = smc.analyze(candles, smc.DEFAULT_CONFIG, None, "vi")

                for name, (poi_pct, poi_types, min_rr) in VARIANTS.items():
                    smc.POI_ZONE_THRESHOLD_PCT = poi_pct
                    smc.POI_ZONE_FILTER_TYPES = poi_types
                    smc.MIN_RR_RATIO = min_rr
                    try:
                        scenarios = scenario_backtest.walk_events(
                            ticker, Timeframe.DAILY, STRATEGY, candles, result.events,
                            smc.BULLISH_EVENTS, smc.BEARISH_EVENTS, result.levels, smc,
                            smc.DEFAULT_CONFIG, None, smc.RANGING_PHASES, symbol, risk_cfg,
                        )
                    finally:
                        (
                            smc.POI_ZONE_THRESHOLD_PCT,
                            smc.POI_ZONE_FILTER_TYPES,
                            smc.MIN_RR_RATIO,
                        ) = originals

                    bucket = buckets[name]
                    for s in scenarios:
                        if not s.is_bullish or s.status not in ("hit_tp", "hit_sl", "expired") or s.exit_price is None:
                            continue
                        r = _r_multiple(s, risk_cfg)
                        if r is None:
                            continue
                        window = "opt" if s.event_ts < OPT_HOLDOUT_CUTOFF else "holdout"
                        bucket[window].append((s.event_ts, r))
            finally:
                smc.analyze = original_analyze

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
