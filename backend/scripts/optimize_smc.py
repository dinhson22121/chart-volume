"""One-off SMC parameter sweep -- swing_lookback (internal-tier fractal
sensitivity) and major_swing_lookback (swing-tier, added this session to
match LuxAlgo's two-tier design) were set to match LuxAlgo's own defaults /
this app's existing "swing lookback" convention, never independently swept
against this app's own data the way Wyckoff's climax_vol_mult/sos_vol_mult
were (see optimize_wyckoff.py). backtest_smc.py already confirmed the
DEFAULT_CONFIG has a real, durable edge (VN30 + full-universe holdout CI
entirely positive, walk_forward=1.00) -- this sweep asks whether any nearby
candidate beats that baseline, using the SAME train/holdout split +
opt-window ranking discipline as optimize_wyckoff.py.

ob_lookback_bars/fvg_min_gap_mult/ob_volatility_mult are left at their
defaults (not swept) to keep this to a 3x3=9-combo grid instead of a much
more expensive 5-dimension one; eq_threshold_mult is skipped entirely since
it only affects EQH/EQL display markers, never BULLISH_EVENTS/BEARISH_EVENTS
(see app.smc.phase._BULLISH_SUPPORT/_BEARISH_SUPPORT), so it cannot move any
R-multiple in this backtest at all.

Unlike optimize_wyckoff.py's SL_BUFFER_PCT (an orthogonal trade_scenario
constant that doesn't affect event detection, memoizable across variants),
both parameters swept here are part of SMCConfig itself and change
detect_events()'s own output -- no memoization shortcut applies; each combo
is a fully independent analyze()+walk_events() pass.

Bullish-only (spot-only, matches every other backtest this session's
is_bullish filter). VN30-only by default; pass --full for the full
HOSE/HNX universe once VN30 looks good.

Read-only, writes nothing. Run from backend/: `python scripts/optimize_smc.py [--full]`.
"""

from __future__ import annotations

import sys
from itertools import product
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlmodel import Session  # noqa: E402

from app import smc  # noqa: E402
from app.db import get_engine  # noqa: E402
from app.models import Symbol, Timeframe  # noqa: E402
from app.services import scenario_backtest, settings_service  # noqa: E402
from app.smc.config import SMCConfig  # noqa: E402
from scripts.optimize_wyckoff import (  # noqa: E402
    OPT_HOLDOUT_CUTOFF,
    _format_window,
    _load_daily_candles,
    _r_multiple,
    _score_window,
    _stock_tickers_with_enough_history,
)

STRATEGY = "smc"

SWING_LOOKBACK_GRID = (2, 3, 4)
MAJOR_SWING_LOOKBACK_GRID = (15, 20, 30)

# Matches SMCConfig()'s current live defaults -- always reported alongside
# the sweep so "did anything actually beat what's live today" has a concrete
# baseline, not just a ranking among candidates.
DEFAULT_CANDIDATE = (2, 20)


def main() -> None:
    engine = get_engine()
    with Session(engine) as session:
        risk_cfg = settings_service.get_risk_config(session)
        risk_amount = risk_cfg["notional_capital"] * risk_cfg["risk_pct_per_trade"] / 100

        vn30_only = "--full" not in sys.argv
        tickers = _stock_tickers_with_enough_history(session, vn30_only=vn30_only)
        print(f"{len(tickers)} ticker(s)\n")

        results: dict[tuple[int, int], dict[str, list[float]]] = {
            (s, m): {"opt": [], "holdout": []}
            for s, m in product(SWING_LOOKBACK_GRID, MAJOR_SWING_LOOKBACK_GRID)
        }

        for i, ticker in enumerate(tickers, 1):
            candles = _load_daily_candles(session, ticker)
            symbol = session.get(Symbol, ticker)
            print(f"[{i}/{len(tickers)}] {ticker} ({len(candles)} bars)", file=sys.stderr)

            for swing_lookback, major_swing_lookback in product(SWING_LOOKBACK_GRID, MAJOR_SWING_LOOKBACK_GRID):
                cfg = SMCConfig(swing_lookback=swing_lookback, major_swing_lookback=major_swing_lookback)
                result = smc.analyze(candles, cfg, None, "vi")
                scenarios = scenario_backtest.walk_events(
                    ticker, Timeframe.DAILY, STRATEGY, candles, result.events,
                    smc.BULLISH_EVENTS, smc.BEARISH_EVENTS, result.levels, smc,
                    cfg, None, smc.RANGING_PHASES, symbol, risk_cfg,
                )

                bucket = results[(swing_lookback, major_swing_lookback)]
                for s in scenarios:
                    if not s.is_bullish or s.status not in ("hit_tp", "hit_sl", "expired") or s.exit_price is None:
                        continue
                    r = _r_multiple(s, risk_cfg)
                    if r is None:
                        continue
                    window = "opt" if s.event_ts < OPT_HOLDOUT_CUTOFF else "holdout"
                    bucket[window].append(r)

        scored = {
            cand: _score_window(data["opt"], risk_amount, risk_cfg["notional_capital"])
            for cand, data in results.items()
        }
        ranked = sorted(
            (c for c in scored if scored[c]["n_trades"] >= 10),
            key=lambda c: (scored[c]["bootstrap_ci_lower"] is not None, scored[c]["bootstrap_ci_lower"] or -999),
            reverse=True,
        )

        print("\n=== Baseline (current live defaults) ===")
        s, m = DEFAULT_CANDIDATE
        print(f"swing_lookback={s} major_swing_lookback={m}")
        print(_format_window("opt window    ", _score_window(results[DEFAULT_CANDIDATE]["opt"], risk_amount, risk_cfg["notional_capital"])))
        print(_format_window("holdout window", _score_window(results[DEFAULT_CANDIDATE]["holdout"], risk_amount, risk_cfg["notional_capital"])))

        print("\n=== Top candidates by opt-window bootstrap CI lower bound ===")
        for cand in ranked[:5]:
            s, m = cand
            print(f"\nswing_lookback={s} major_swing_lookback={m}")
            print(_format_window("opt window    ", scored[cand]))
            print(_format_window("holdout window", _score_window(results[cand]["holdout"], risk_amount, risk_cfg["notional_capital"])))


if __name__ == "__main__":
    main()
