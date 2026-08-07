"""One-off test of a Relative Strength (RS) entry filter: classic Wyckoff
practice checks a stock's RS line (its price relative to the broader market)
before trusting an accumulation signal -- a Spring/SOS in a stock that's
actually UNDERPERFORMING the market is a much weaker setup than the same
signal in a stock leading the market higher.

Motivation: 3 independent exit-side experiments (ATR trailing stop / bigger
max_bars in scripts/optimize_wyckoff_exit.py, and two variants of
scripts/optimize_wyckoff_phase_exit.py) all made results WORSE, not better,
when given more time/room to develop -- converging evidence that the
remaining gap isn't the exit mechanism, it's that many entries simply don't
have real follow-through. This tests whether requiring the stock to already
be beating the market (RS uptrend) at entry screens out the entries that
don't.

No VNINDEX/VN30-index ticker is tracked in the DB (see the regime check this
session did directly against real candles), so the market benchmark here is
an equal-weighted composite of the same VN30 tickers' own daily closes -- an
approximation, not the free-float-weighted real VN30 index, but built from
the exact same real data every other number in this investigation used.

Backtests the SAME (2.0, 1.5, 0.003) live-default config WITH vs WITHOUT the
RS filter, isolating just this one change, through the IDENTICAL
walk_events() the live app and every other script here uses -- only the
input event list differs (bullish events failing the RS check are dropped
before the walk, everything else about detection/gating/outcome is
untouched). Read-only, opens the real app DB but never commits. Run from
backend/: `python scripts/optimize_wyckoff_rs_filter.py`.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402
from sqlmodel import Session  # noqa: E402

import app.wyckoff as wyckoff_module  # noqa: E402
from app.db import get_engine  # noqa: E402
from app.models import Symbol, Timeframe  # noqa: E402
from app.services import scenario_backtest, settings_service  # noqa: E402
from app.wyckoff import BEARISH_EVENTS, BULLISH_EVENTS, RANGING_PHASES  # noqa: E402
from app.wyckoff.config import WyckoffConfig  # noqa: E402
from scripts.optimize_wyckoff import (  # noqa: E402
    DEFAULT_CANDIDATE,
    OPT_HOLDOUT_CUTOFF,
    _format_window,
    _load_daily_candles,
    _r_multiple,
    _score_window,
    _stock_tickers_with_enough_history,
)

STRATEGY = "wyckoff"

# RS line = close / market_index. "In an RS uptrend" = RS line above its own
# trailing moving average -- the same construction as a plain price-vs-MA
# trend check, just applied to relative (not absolute) performance.
RS_MA_LEN = 50


def _build_market_index(all_candles: dict[str, list]) -> pd.Series:
    """Equal-weighted composite close, indexed by date, built from every
    ticker's own daily closes (mean across whatever tickers have a bar on a
    given date -- skipna handles tickers that started trading later)."""
    frames = [
        pd.Series({c.bucket_start.date(): float(c.close) for c in candles}, name=ticker)
        for ticker, candles in all_candles.items()
    ]
    wide = pd.concat(frames, axis=1).sort_index()
    return wide.mean(axis=1, skipna=True)


def _rs_uptrend_by_index(candles: list, market_index: pd.Series) -> dict[int, bool]:
    """event.index -> is this ticker's RS line above its own RS_MA_LEN-bar MA."""
    closes = pd.Series([float(c.close) for c in candles])
    index_vals = pd.Series([market_index.get(c.bucket_start.date()) for c in candles])
    rs = closes / index_vals
    rs_ma = rs.rolling(RS_MA_LEN, min_periods=RS_MA_LEN).mean()
    uptrend = rs > rs_ma
    return {i: bool(v) for i, v in uptrend.items()}


def main() -> None:
    engine = get_engine()
    with Session(engine) as session:
        risk_cfg = settings_service.get_risk_config(session)
        tickers = _stock_tickers_with_enough_history(session)
        all_candles = {t: _load_daily_candles(session, t) for t in tickers}
        symbols = {t: session.get(Symbol, t) for t in tickers}

    print(f"{len(tickers)} VN30 tickers, building equal-weight market index...\n")
    market_index = _build_market_index(all_candles)

    cfg = WyckoffConfig(climax_vol_mult=DEFAULT_CANDIDATE[0], sos_vol_mult=DEFAULT_CANDIDATE[1])
    risk_amount = risk_cfg["notional_capital"] * risk_cfg["risk_pct_per_trade"] / 100

    results = {True: {"opt": [], "holdout": []}, False: {"opt": [], "holdout": []}}

    for i, ticker in enumerate(tickers, start=1):
        print(f"[{i}/{len(tickers)}] {ticker}", file=sys.stderr)
        candles = all_candles[ticker]
        symbol = symbols[ticker]
        result = wyckoff_module.analyze(candles, cfg, None, "vi")
        rs_uptrend = _rs_uptrend_by_index(candles, market_index)

        for require_rs in (False, True):
            events = [
                e for e in result.events
                if e.type not in BULLISH_EVENTS or rs_uptrend.get(e.index, False) or not require_rs
            ]
            scenarios = scenario_backtest.walk_events(
                ticker, Timeframe.DAILY, STRATEGY, candles, events,
                BULLISH_EVENTS, BEARISH_EVENTS, result.levels, wyckoff_module,
                cfg, None, RANGING_PHASES, symbol, risk_cfg,
            )
            for s in scenarios:
                if not s.is_bullish or s.status not in ("hit_tp", "hit_sl", "expired") or s.exit_price is None:
                    continue
                r = _r_multiple(s, risk_cfg)
                if r is None:
                    continue
                window = "opt" if s.event_ts < OPT_HOLDOUT_CUTOFF else "holdout"
                results[require_rs][window].append(r)

    for require_rs in (False, True):
        label = "WITH RS filter" if require_rs else "WITHOUT RS filter (baseline)"
        print(f"\n=== {label} ===")
        for window in ("opt", "holdout"):
            rs_list = results[require_rs][window]
            print(_format_window(f"{window:14}", _score_window(rs_list, risk_amount, risk_cfg["notional_capital"])))


if __name__ == "__main__":
    main()
