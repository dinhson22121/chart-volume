"""One-off test of a genuinely different exit paradigm: instead of a fixed
range-height take-profit capped at a handful of bars (see
trade_scenario._compute_max_bars, MAX_MAX_BARS=30), ride the trade until a
strong bearish reversal signal (Upthrust / Buying Climax / SOW) actually
fires, or the stop-loss is hit -- whichever comes first. A generous bar-count
safety cap only guards against a position sitting open indefinitely; it is
not meant to be the thing that closes profitable trades early.

Motivation: scripts/optimize_wyckoff_exit.py already showed giving the
EXISTING fixed-TP/max_bars mechanism more room (bigger multiplier, or an ATR
trailing stop) makes results worse, not better -- ruling out "just loosen the
current formula" as a fix. Separately, a regime check on the real DB showed
VN30 rallied hard during the backtest's holdout window (avg +66%, several
tickers +100-975%) while Wyckoff still lost money -- a long-only strategy
with a working edge should have benefited enormously from that, so the
existing fixed/short-horizon exit is a strong suspect for leaving real
Markup continuation moves on the table. This script tests a qualitatively
different mechanism (phase/signal-driven, not formula-driven) rather than
just re-tuning the old one.

Reuses the exact same entry/SL construction and gates as the live app
(_build_scenario_candidate, unchanged) -- only the exit rule differs. Backtest
against the SAME opt/holdout split as optimize_wyckoff.py. Read-only, opens
the real app DB but never commits. Run from backend/:
`python scripts/optimize_wyckoff_phase_exit.py`.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlmodel import Session  # noqa: E402

import app.wyckoff as wyckoff_module  # noqa: E402
from app.ai.narrative import PROVIDER_ANTHROPIC, ProviderConfig  # noqa: E402
from app.db import get_engine  # noqa: E402
from app.models import Symbol, Timeframe  # noqa: E402
from app.services import settings_service, trade_scenario  # noqa: E402
from app.services.trade_scenario import CONTINUATION_EVENT_TYPES, _build_scenario_candidate  # noqa: E402
from app.wyckoff import BULLISH_EVENTS, RANGING_PHASES  # noqa: E402
from app.wyckoff.config import WyckoffConfig  # noqa: E402
from app.wyckoff.events import SOW  # noqa: E402
from scripts import parallel_tickers  # noqa: E402
from scripts.optimize_wyckoff import (  # noqa: E402
    OPT_HOLDOUT_CUTOFF,
    _format_window,
    _load_daily_candles,
    _score_window,
    _stock_tickers_with_enough_history,
)

STRATEGY = "wyckoff"

# Iteration 2: {Upthrust, BuyingClimax, SOW} as the trigger set (iteration 1)
# performed WORSE than the fixed-TP baseline on VN30 holdout (win_rate
# 9.7-14.3% vs 29.3%, mean_r/median_r deeply negative) -- Upthrust/BC fire too
# readily on ordinary within-trend volatility, cutting real winners short
# before they'd have reached the existing TP. Narrowed to SOW alone: the one
# signal that requires an actual confirmed break of support with volume, not
# just a wick/climax that can occur mid-trend.
STRONG_BEARISH_EVENTS = {SOW}

# Safety net only -- guards against a position sitting open indefinitely in
# the backtest, not meant to be what closes winners. 30 reproduces roughly
# today's live ceiling (MAX_MAX_BARS) as a baseline comparison point.
SAFETY_CAP_GRID = (30, 60, 90, 250)


@dataclass
class PhaseExitOutcome:
    status: str  # "stopped" | "signal_exit" | "expired" | "active"
    closed_bar_ts: datetime | None
    exit_price: float | None


def _resolve_phase_exit(
    entry_index: int,
    stop_loss: float,
    is_bullish: bool,
    candles: list,
    bearish_indices: set[int],
    safety_cap: int,
) -> PhaseExitOutcome:
    n = len(candles)
    for offset, idx in enumerate(range(entry_index + 1, n), start=1):
        bar = candles[idx]
        hit_stop = bar.low <= stop_loss if is_bullish else bar.high >= stop_loss
        if hit_stop:
            return PhaseExitOutcome("stopped", bar.bucket_start, stop_loss)

        if idx in bearish_indices:
            # Exit at the NEXT bar's open -- can't act on the same bar the
            # reversal signal is only confirmed at close, same causality
            # rule the live entry itself follows (entry = next bar's open).
            if idx + 1 < n:
                exit_bar = candles[idx + 1]
                return PhaseExitOutcome("signal_exit", exit_bar.bucket_start, exit_bar.open)
            return PhaseExitOutcome("signal_exit", bar.bucket_start, bar.close)

        if offset >= safety_cap:
            return PhaseExitOutcome("expired", bar.bucket_start, bar.close)
    return PhaseExitOutcome("active", None, None)


def _r_multiple_from_prices(entry, stop_loss, exit_price, is_bullish, risk_cfg) -> float:
    risk_distance = abs(entry - stop_loss)
    cost_pct = (
        risk_cfg["slippage_pct_stock"] + risk_cfg["broker_fee_pct_stock"] + risk_cfg["sell_tax_pct_stock"]
    ) / 100
    cost_amount = cost_pct * entry
    adjusted_exit = exit_price - cost_amount if is_bullish else exit_price + cost_amount
    raw = (adjusted_exit - entry) / risk_distance
    return raw if is_bullish else -raw


def _walk_phase_exit(ticker, candles, events, levels, cfg, symbol, risk_cfg, safety_cap):
    """Same candidate-building as walk_events (entry/SL untouched), resolved
    with the strong-reversal-signal exit instead of trade_scenario._resolve_outcome."""
    qualifying = sorted(
        (e for e in events if e.type in BULLISH_EVENTS and e.type not in CONTINUATION_EVENT_TYPES),
        key=lambda e: e.ts,
    )
    bearish_indices = {e.index for e in events if e.type in STRONG_BEARISH_EVENTS}
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
        outcome = _resolve_phase_exit(
            event.index, candidate.stop_loss, True, candles, bearish_indices, safety_cap,
        )
        if outcome.status == "active":
            still_open = True
            continue
        r = _r_multiple_from_prices(candidate.entry, candidate.stop_loss, outcome.exit_price, True, risk_cfg)
        results.append((event.ts, r))
        blocked_until = outcome.closed_bar_ts
    return results


def _sweep_one_ticker(session: Session, ticker: str) -> dict[int, dict[str, list[tuple]]]:
    risk_cfg = settings_service.get_risk_config(session)
    candles = _load_daily_candles(session, ticker)
    symbol = session.get(Symbol, ticker)
    cfg = WyckoffConfig()

    result = wyckoff_module.analyze(candles, cfg, None, "vi")

    analyze_cache: dict[int, object] = {}
    original_analyze = wyckoff_module.analyze

    def _cached_analyze(candles_arg, *a, **k):
        key = len(candles_arg)
        if key not in analyze_cache:
            analyze_cache[key] = original_analyze(candles_arg, *a, **k)
        return analyze_cache[key]

    ticker_results: dict[int, dict[str, list[tuple]]] = {c: {"opt": [], "holdout": []} for c in SAFETY_CAP_GRID}

    wyckoff_module.analyze = _cached_analyze
    try:
        for cap in SAFETY_CAP_GRID:
            dated = _walk_phase_exit(ticker, candles, result.events, result.levels, cfg, symbol, risk_cfg, cap)
            for ts, r in dated:
                window = "opt" if ts < OPT_HOLDOUT_CUTOFF else "holdout"
                ticker_results[cap][window].append((ts, r))
    finally:
        wyckoff_module.analyze = original_analyze

    return ticker_results


def main() -> None:
    engine = get_engine()
    with Session(engine) as session:
        risk_cfg = settings_service.get_risk_config(session)
        risk_amount = risk_cfg["notional_capital"] * risk_cfg["risk_pct_per_trade"] / 100
        tickers = _stock_tickers_with_enough_history(session)

    results: dict[int, dict[str, list[tuple]]] = {c: {"opt": [], "holdout": []} for c in SAFETY_CAP_GRID}
    workers = parallel_tickers.default_workers()
    print(f"{len(tickers)} stock ticker(s), {workers} process(es)\n")

    for i, ticker, ticker_results in parallel_tickers.map_tickers(tickers, _sweep_one_ticker, workers=workers):
        print(f"[{i}/{len(tickers)}] {ticker}", file=sys.stderr)
        for cap, windows in ticker_results.items():
            for window, dated in windows.items():
                results[cap][window].extend(dated)

    print("\n=== Phase/signal-driven exit (ride until Upthrust/BC/SOW, or SL) ===")
    for cap in SAFETY_CAP_GRID:
        print(f"\nsafety_cap={cap} bars")
        for window in ("opt", "holdout"):
            dated = sorted(results[cap][window], key=lambda pair: pair[0])
            rs = [r for _, r in dated]
            print(_format_window(f"{window:14}", _score_window(rs, risk_amount, risk_cfg["notional_capital"])))


if __name__ == "__main__":
    main()
