"""One-off diagnostic: WHY does the Wyckoff VN30 baseline lose so
consistently? optimize_wyckoff.py and optimize_wyckoff_tp.py ruled out
detection sensitivity, stop buffer size, and the TP distance cap -- none of
them move the median R (~-1.05 to -1.13) at all. This script looks at the
actual trade-level distributions behind that number: how many bars a losing
trade survives before hitting its stop, and what R:R ratio (TP distance vs
SL distance) each trade was actually offered -- to find the mechanical
reason, not just rule out more thresholds.

Read-only, VN30 only, current live defaults. Run from backend/:
`python scripts/diagnose_wyckoff.py`.
"""

from __future__ import annotations

import sys
import statistics
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlmodel import Session  # noqa: E402

import app.wyckoff as wyckoff_module  # noqa: E402
from app.db import get_engine  # noqa: E402
from app.models import Symbol, Timeframe  # noqa: E402
from app.services import scenario_backtest, settings_service  # noqa: E402
from app.wyckoff import BEARISH_EVENTS, BULLISH_EVENTS, RANGING_PHASES  # noqa: E402
from app.wyckoff.config import WyckoffConfig  # noqa: E402
from scripts.optimize_wyckoff import _load_daily_candles, _stock_tickers_with_enough_history  # noqa: E402


def _bars_between(candles, start_ts, end_ts) -> int:
    return sum(1 for c in candles if start_ts < c.bucket_start <= end_ts)


def main() -> None:
    engine = get_engine()
    with Session(engine) as session:
        risk_cfg = settings_service.get_risk_config(session)
        tickers = _stock_tickers_with_enough_history(session)
        cfg = WyckoffConfig()

        by_status: dict[str, list[int]] = {"hit_sl": [], "hit_tp": [], "expired": []}
        rr_ratios: list[float] = []
        risk_pcts: list[float] = []
        reward_pcts: list[float] = []
        event_type_by_status: dict[str, dict[str, int]] = {"hit_sl": {}, "hit_tp": {}, "expired": {}}
        max_bars_seen: list[int] = []

        for ticker in tickers:
            candles = _load_daily_candles(session, ticker)
            symbol = session.get(Symbol, ticker)
            result = wyckoff_module.analyze(candles, cfg, None, "vi")

            scenarios = scenario_backtest.walk_events(
                ticker, Timeframe.DAILY, "wyckoff", candles, result.events,
                BULLISH_EVENTS, BEARISH_EVENTS, result.levels, wyckoff_module,
                cfg, None, RANGING_PHASES, symbol, risk_cfg,
            )

            for s in scenarios:
                if s.status not in ("hit_sl", "hit_tp", "expired"):
                    continue
                bars = _bars_between(candles, s.event_ts, s.closed_bar_ts) if s.closed_bar_ts else None
                if bars is not None:
                    by_status[s.status].append(bars)
                event_type_by_status[s.status][s.event_type] = event_type_by_status[s.status].get(s.event_type, 0) + 1
                max_bars_seen.append(s.max_bars)

                risk_distance = abs(s.entry - s.stop_loss)
                reward_distance = abs(s.take_profit - s.entry)
                if risk_distance > 0:
                    rr_ratios.append(reward_distance / risk_distance)
                    risk_pcts.append(risk_distance / s.entry * 100)
                    reward_pcts.append(reward_distance / s.entry * 100)

        total = sum(len(v) for v in by_status.values())
        print(f"=== Status breakdown (n={total}) ===")
        for status, bars_list in by_status.items():
            count = len(bars_list)
            pct = count / total * 100 if total else 0
            med_bars = statistics.median(bars_list) if bars_list else None
            print(f"  {status:>8}: {count:>4} ({pct:5.1f}%)  median bars-to-resolution={med_bars}")

        print(f"\n=== Event type breakdown by outcome ===")
        for status, counts in event_type_by_status.items():
            print(f"  {status}: {counts}")

        print(f"\n=== Risk/Reward shape (n={len(rr_ratios)}) ===")
        print(f"  median R:R offered (TP_dist/SL_dist) = {statistics.median(rr_ratios):.2f}")
        print(f"  median risk  (SL distance, % of entry)   = {statistics.median(risk_pcts):.2f}%")
        print(f"  median reward (TP distance, % of entry)  = {statistics.median(reward_pcts):.2f}%")
        print(f"  median max_bars allotted = {statistics.median(max_bars_seen)}")

        # Breakeven win rate implied by the median R:R, for reference.
        median_rr = statistics.median(rr_ratios)
        breakeven_wr = 1 / (1 + median_rr)
        print(f"\n  => breakeven win rate at this median R:R: {breakeven_wr:.1%}")


if __name__ == "__main__":
    main()
