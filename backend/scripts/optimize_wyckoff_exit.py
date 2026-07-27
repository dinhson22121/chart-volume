"""One-off comparison: does the Wyckoff strategy's exit MECHANISM explain its
poor performance (see diagnose_wyckoff.py: 81.2% hit_sl, median 5 bars to
resolution, vs a 2.48 R:R that needs a much longer runway to pay off)?

Two candidate fixes, backtested against the SAME opt/holdout split as
optimize_wyckoff.py:

  A) Keep the fixed TP/SL model, just give max_bars more room to actually
     reach the existing TP -- sweep multipliers on the current ATR-based
     formula (trade_scenario._compute_max_bars).
  B) Drop the fixed TP entirely: breakeven-at-1R + ATR trailing stop, so a
     winning trade isn't capped by a hard measured-move target and a loser
     is cut at the same original SL as today.

Both reuse the exact same entry/SL formulas the live app uses
(_build_scenario_candidate) -- only the EXIT rule changes. Read-only, opens
the real app DB but never commits. Run from backend/:
`python scripts/optimize_wyckoff_exit.py`.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlmodel import Session  # noqa: E402

import app.wyckoff as wyckoff_module  # noqa: E402
from app.db import get_engine  # noqa: E402
from app.models import Timeframe  # noqa: E402
from app.services import settings_service, trade_scenario  # noqa: E402
from app.wyckoff import BEARISH_EVENTS, BULLISH_EVENTS, RANGING_PHASES  # noqa: E402
from app.wyckoff.config import WyckoffConfig  # noqa: E402
from scripts.optimize_wyckoff import (  # noqa: E402
    OPT_HOLDOUT_CUTOFF,
    _format_window,
    _load_daily_candles,
    _r_multiple,
    _score_window,
    _stock_tickers_with_enough_history,
)

STRATEGY = "wyckoff"

# Approach A: multiplier on the existing tp_distance/atr formula before the
# MIN/MAX clamp -- 1.0 reproduces today's live behavior exactly (baseline).
MAX_BARS_MULT_GRID = (1.0, 1.5, 2.0, 3.0)

# Approach B: how many ATRs beyond the highest (bullish) / lowest (bearish)
# favorable excursion so far the trailing stop sits, once price has moved 1R
# in favor. Smaller = tighter trail (locks in profit sooner, more likely to
# get stopped out on a pullback); larger = more room to breathe.
TRAIL_ATR_MULT_GRID = (1.5, 2.0, 3.0)


def _make_max_bars_with_mult(mult: float):
    original = trade_scenario._compute_max_bars

    def _wrapped(candles_before_event, tp_distance):
        atr = trade_scenario._atr(candles_before_event)
        if not atr or atr <= 0:
            return trade_scenario.DEFAULT_MAX_BARS
        bars = round((tp_distance / atr) * mult)
        return max(trade_scenario.MIN_MAX_BARS, min(trade_scenario.MAX_MAX_BARS, bars))

    return _wrapped, original


@dataclass
class TrailOutcome:
    status: str  # "stopped" | "expired" | "active"
    closed_bar_ts: datetime | None
    exit_price: float | None


def _resolve_trailing_outcome(
    event_ts: datetime,
    entry: float,
    stop_loss: float,
    is_bullish: bool,
    max_bars: int,
    atr: float,
    trail_mult: float,
    candles: list,
) -> TrailOutcome:
    """Breakeven-at-1R + ATR trailing stop beyond that -- no fixed take-profit
    ceiling. Causal: the stop for bar N is fixed before bar N's own high/low
    is used to (maybe) move it for bar N+1."""
    subsequent = sorted((c for c in candles if c.bucket_start > event_ts), key=lambda c: c.bucket_start)
    risk_distance = abs(entry - stop_loss)
    current_stop = stop_loss
    best = entry
    for idx, bar in enumerate(subsequent, start=1):
        hit_stop = bar.low <= current_stop if is_bullish else bar.high >= current_stop
        if hit_stop:
            return TrailOutcome("stopped", bar.bucket_start, current_stop)

        best = max(best, bar.high) if is_bullish else min(best, bar.low)
        unrealized = (best - entry) if is_bullish else (entry - best)
        if unrealized >= risk_distance and atr and atr > 0:
            trail_level = (best - trail_mult * atr) if is_bullish else (best + trail_mult * atr)
            current_stop = max(current_stop, entry, trail_level) if is_bullish else min(current_stop, entry, trail_level)

        if idx >= max_bars:
            return TrailOutcome("expired", bar.bucket_start, bar.close)
    return TrailOutcome("active", None, None)


def _r_multiple_from_prices(entry, stop_loss, exit_price, is_bullish, risk_cfg, ticker_is_crypto) -> float:
    risk_distance = abs(entry - stop_loss)
    cost_pct = (
        (risk_cfg["slippage_pct_crypto"] + risk_cfg["trading_fee_pct_crypto"]) / 100
        if ticker_is_crypto
        else (risk_cfg["slippage_pct_stock"] + risk_cfg["broker_fee_pct_stock"] + risk_cfg["sell_tax_pct_stock"]) / 100
    )
    cost_amount = cost_pct * entry
    adjusted_exit = exit_price - cost_amount if is_bullish else exit_price + cost_amount
    raw = (adjusted_exit - entry) / risk_distance
    return raw if is_bullish else -raw


def _walk_approach_b(ticker, candles, events, levels, cfg, symbol, risk_cfg, trail_mult):
    """Same candidate-building as walk_events, but resolved with the
    breakeven+trail rule instead of trade_scenario._resolve_outcome."""
    from app.ai.narrative import PROVIDER_ANTHROPIC, ProviderConfig
    from app.services.trade_scenario import _CONTINUATION_EVENT_TYPES, _build_scenario_candidate

    qualifying = sorted(
        (e for e in events if e.type in BULLISH_EVENTS and e.type not in _CONTINUATION_EVENT_TYPES),
        key=lambda e: e.ts,
    )
    provider_cfg = ProviderConfig(provider=PROVIDER_ANTHROPIC, model="", api_key="", language="vi")
    results = []
    blocked_until = None
    still_open = False
    for event in qualifying:
        if still_open:
            break
        if blocked_until is not None and event.ts <= blocked_until:
            continue
        candidate = _build_scenario_candidate(
            ticker, Timeframe.DAILY, STRATEGY, candles, event, True, levels, provider_cfg,
            wyckoff_module, cfg, None, RANGING_PHASES, symbol, risk_cfg, use_ai=False, config_version="",
        )
        if candidate is None:
            continue
        atr = trade_scenario._atr(candles[: event.index])
        outcome = _resolve_trailing_outcome(
            candidate.event_ts, candidate.entry, candidate.stop_loss, True,
            candidate.max_bars, atr or 0.0, trail_mult, candles,
        )
        if outcome.status == "active":
            still_open = True
            continue
        r = _r_multiple_from_prices(
            candidate.entry, candidate.stop_loss, outcome.exit_price, True, risk_cfg, ticker_is_crypto=False,
        )
        results.append((event.ts, r))
        blocked_until = outcome.closed_bar_ts
    return results


def main() -> None:
    engine = get_engine()
    with Session(engine) as session:
        risk_cfg = settings_service.get_risk_config(session)
        risk_amount = risk_cfg["notional_capital"] * risk_cfg["risk_pct_per_trade"] / 100

        tickers = _stock_tickers_with_enough_history(session, vn30_only=False)
        print(f"{len(tickers)} HOSE/HNX ticker(s)\n")

        cfg = WyckoffConfig()

        # --- Approach A: sweep max_bars multipliers via the real walk_events/_resolve_outcome path ---
        a_results: dict[float, dict[str, list[tuple]]] = {m: {"opt": [], "holdout": []} for m in MAX_BARS_MULT_GRID}
        # --- Approach B: sweep trailing-stop ATR multiples via the custom resolver above ---
        b_results: dict[float, dict[str, list[tuple]]] = {m: {"opt": [], "holdout": []} for m in TRAIL_ATR_MULT_GRID}

        from app.models import Symbol
        from app.services import scenario_backtest

        for i, ticker in enumerate(tickers, 1):
            candles = _load_daily_candles(session, ticker)
            symbol = session.get(Symbol, ticker)
            print(f"[{i}/{len(tickers)}] {ticker} ({len(candles)} bars)", file=sys.stderr)

            result = wyckoff_module.analyze(candles, cfg, None, "vi")

            # _build_scenario_candidate's phase-before-event gate re-runs
            # analyze() on the truncated pre-event window for every
            # qualifying event -- the single most expensive step in this
            # walk (see optimize_wyckoff.py's own docstring on this). Both
            # approaches below share the exact same qualifying-event set for
            # this ticker, so memoize it once per ticker (keyed by truncated
            # length == event.index, unique per event) instead of paying for
            # it 7 times (4 max_bars multipliers + 3 trail multiples).
            analyze_cache: dict[int, object] = {}
            original_analyze = wyckoff_module.analyze

            def _cached_analyze(candles_arg, *a, **k):
                key = len(candles_arg)
                if key not in analyze_cache:
                    analyze_cache[key] = original_analyze(candles_arg, *a, **k)
                return analyze_cache[key]

            wyckoff_module.analyze = _cached_analyze
            try:
                for mult in MAX_BARS_MULT_GRID:
                    wrapped, original_max_bars = _make_max_bars_with_mult(mult)
                    trade_scenario._compute_max_bars = wrapped
                    try:
                        scenarios = scenario_backtest.walk_events(
                            ticker, Timeframe.DAILY, STRATEGY, candles, result.events,
                            BULLISH_EVENTS, BEARISH_EVENTS, result.levels, wyckoff_module,
                            cfg, None, RANGING_PHASES, symbol, risk_cfg,
                        )
                    finally:
                        trade_scenario._compute_max_bars = original_max_bars

                    for s in scenarios:
                        if not s.is_bullish or s.status not in ("hit_tp", "hit_sl", "expired") or s.exit_price is None:
                            continue
                        r = _r_multiple(s, risk_cfg)
                        if r is None:
                            continue
                        window = "opt" if s.event_ts < OPT_HOLDOUT_CUTOFF else "holdout"
                        a_results[mult][window].append((s.event_ts, r))

                for trail_mult in TRAIL_ATR_MULT_GRID:
                    dated = _walk_approach_b(
                        ticker, candles, result.events, result.levels, cfg, symbol, risk_cfg, trail_mult
                    )
                    for ts, r in dated:
                        window = "opt" if ts < OPT_HOLDOUT_CUTOFF else "holdout"
                        b_results[trail_mult][window].append((ts, r))
            finally:
                wyckoff_module.analyze = original_analyze

        print("\n=== Approach A: max_bars multiplier on current ATR-based formula (1.0 = live baseline) ===")
        for mult in MAX_BARS_MULT_GRID:
            print(f"\nmax_bars_mult={mult}")
            for label, key in (("opt window    ", "opt"), ("holdout window", "holdout")):
                dated = sorted(a_results[mult][key], key=lambda p: p[0])
                r_multiples = [r for _, r in dated]
                print(_format_window(label, _score_window(r_multiples, risk_amount, risk_cfg["notional_capital"])))

        print("\n=== Approach B: breakeven-at-1R + ATR trailing stop (no fixed TP) ===")
        for trail_mult in TRAIL_ATR_MULT_GRID:
            print(f"\ntrail_atr_mult={trail_mult}")
            for label, key in (("opt window    ", "opt"), ("holdout window", "holdout")):
                dated = sorted(b_results[trail_mult][key], key=lambda p: p[0])
                r_multiples = [r for _, r in dated]
                print(_format_window(label, _score_window(r_multiples, risk_amount, risk_cfg["notional_capital"])))


if __name__ == "__main__":
    main()
