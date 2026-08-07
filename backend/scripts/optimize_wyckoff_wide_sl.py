"""One-off test: does a WIDER initial stop-loss (ATR-scaled, replacing the
tight bar-low-based SL_BUFFER_PCT stop) reduce premature stop-outs from
ordinary volatility, while keeping the existing fixed take-profit and
breakeven+trail mechanism (trade_scenario._resolve_outcome) untouched?

Distinct from every exit-side experiment already tried this session
(scripts/optimize_wyckoff_exit.py's ATR trail / bigger max_bars,
scripts/optimize_wyckoff_phase_exit.py's ride-until-reversal-signal): those
all REMOVED or delayed the profit target, giving trades more time/room to
develop -- and all made results worse, because most trades simply drifted
down to the still-tight original SL before any of that extra room paid off.
This keeps the SAME quick take-profit and max_bars (the part that's
currently salvaging a ~29% win rate), and widens ONLY the initial stop, so
ordinary noise doesn't stop a trade out before the existing fast TP has a
chance to fire.

Mechanically: build each candidate exactly as the live app does
(_build_scenario_candidate, entry/take_profit/max_bars/gates all untouched),
then replace ONLY candidate.stop_loss with an ATR-multiple-based one before
calling _resolve_outcome -- the same breakeven-at-1R + trailing-stop logic
in _resolve_outcome still applies automatically from that wider starting
point. Backtests against the SAME opt/holdout split as optimize_wyckoff.py.
Read-only, opens the real app DB but never commits. Run from backend/:
`python scripts/optimize_wyckoff_wide_sl.py`.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlmodel import Session  # noqa: E402

import app.wyckoff as wyckoff_module  # noqa: E402
from app.ai.narrative import PROVIDER_ANTHROPIC, ProviderConfig  # noqa: E402
from app.db import get_engine  # noqa: E402
from app.models import Symbol, Timeframe  # noqa: E402
from app.services import scenario_backtest, settings_service, trade_scenario  # noqa: E402
from app.services.trade_scenario import (  # noqa: E402
    CONTINUATION_EVENT_TYPES,
    _build_scenario_candidate,
    _settlement_bars_for,
)
from app.wyckoff import BULLISH_EVENTS, RANGING_PHASES  # noqa: E402
from app.wyckoff.config import WyckoffConfig  # noqa: E402
from scripts import parallel_tickers  # noqa: E402
from scripts.optimize_wyckoff import (  # noqa: E402
    DEFAULT_CANDIDATE,
    OPT_HOLDOUT_CUTOFF,
    _format_window,
    _load_daily_candles,
    _score_window,
    _stock_tickers_with_enough_history,
)

STRATEGY = "wyckoff"

# Multiple of ATR (computed the same way _build_scenario_candidate/
# _resolve_outcome already do, from the pre-event window) the wider stop
# sits below entry. None = baseline (today's live tight SL, unmodified).
ATR_MULT_GRID = (None, 1.5, 2.0, 3.0, 4.0)


def _r_multiple_from_prices(entry, stop_loss, exit_price, is_bullish, risk_cfg) -> float:
    cost_pct = (
        risk_cfg["slippage_pct_stock"] + risk_cfg["broker_fee_pct_stock"] + risk_cfg["sell_tax_pct_stock"]
    ) / 100
    risk_distance = abs(entry - stop_loss)
    cost_amount = cost_pct * entry
    adjusted_exit = exit_price - cost_amount if is_bullish else exit_price + cost_amount
    raw = (adjusted_exit - entry) / risk_distance
    return raw if is_bullish else -raw


def _walk_with_wide_sl(ticker, candles, events, levels, cfg, symbol, risk_cfg, atr_mult):
    provider_cfg = ProviderConfig(provider=PROVIDER_ANTHROPIC, model="", api_key="", language="vi")
    qualifying = sorted(
        (e for e in events if e.type in BULLISH_EVENTS and e.type not in CONTINUATION_EVENT_TYPES),
        key=lambda e: e.ts,
    )
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

        stop_loss = candidate.stop_loss
        if atr_mult is not None:
            atr = trade_scenario._atr(candles[: event.index])
            if atr and atr > 0:
                stop_loss = candidate.entry - atr_mult * atr

        outcome = scenario_backtest._resolve_outcome(
            candidate.event_ts, candidate.entry, stop_loss, candidate.max_bars, True, candles,
            _settlement_bars_for(symbol, Timeframe.DAILY), take_profit=candidate.take_profit,
        )
        if outcome.status not in ("hit_tp", "hit_sl", "expired") or outcome.exit_price is None:
            blocked_until = outcome.closed_bar_ts
            if outcome.status == "active":
                still_open = True
            continue
        r = _r_multiple_from_prices(candidate.entry, stop_loss, outcome.exit_price, True, risk_cfg)
        results.append((event.ts, r))
        blocked_until = outcome.closed_bar_ts
    return results


def _sweep_one_ticker(session: Session, ticker: str) -> dict:
    risk_cfg = settings_service.get_risk_config(session)
    candles = _load_daily_candles(session, ticker)
    symbol = session.get(Symbol, ticker)
    climax, sos, sl_buffer = DEFAULT_CANDIDATE
    cfg = WyckoffConfig(climax_vol_mult=climax, sos_vol_mult=sos)

    result = wyckoff_module.analyze(candles, cfg, None, "vi")

    ticker_results: dict = {m: {"opt": [], "holdout": []} for m in ATR_MULT_GRID}
    original_sl_buffer = trade_scenario.SL_BUFFER_PCT
    trade_scenario.SL_BUFFER_PCT = sl_buffer
    try:
        for atr_mult in ATR_MULT_GRID:
            dated = _walk_with_wide_sl(ticker, candles, result.events, result.levels, cfg, symbol, risk_cfg, atr_mult)
            for ts, r in dated:
                window = "opt" if ts < OPT_HOLDOUT_CUTOFF else "holdout"
                ticker_results[atr_mult][window].append((ts, r))
    finally:
        trade_scenario.SL_BUFFER_PCT = original_sl_buffer

    return ticker_results


def main() -> None:
    engine = get_engine()
    with Session(engine) as session:
        risk_cfg = settings_service.get_risk_config(session)
        risk_amount = risk_cfg["notional_capital"] * risk_cfg["risk_pct_per_trade"] / 100
        tickers = _stock_tickers_with_enough_history(session)

    results: dict = {m: {"opt": [], "holdout": []} for m in ATR_MULT_GRID}
    workers = parallel_tickers.default_workers()
    print(f"{len(tickers)} stock ticker(s), {workers} process(es)\n")

    for i, ticker, ticker_results in parallel_tickers.map_tickers(tickers, _sweep_one_ticker, workers=workers):
        print(f"[{i}/{len(tickers)}] {ticker}", file=sys.stderr)
        for m, windows in ticker_results.items():
            for window, dated in windows.items():
                results[m][window].extend(dated)

    print("\n=== Wider initial SL (ATR-based), same TP/max_bars/trail as live ===")
    for atr_mult in ATR_MULT_GRID:
        label = "baseline (today's live tight SL)" if atr_mult is None else f"atr_mult={atr_mult}"
        print(f"\n{label}")
        for window in ("opt", "holdout"):
            dated = sorted(results[atr_mult][window], key=lambda pair: pair[0])
            rs = [r for _, r in dated]
            print(_format_window(f"{window:14}", _score_window(rs, risk_amount, risk_cfg["notional_capital"])))


if __name__ == "__main__":
    main()
