"""Experimental RSI-momentum-turn entry signal.

References faytterro's "Wyckoff Accumulation Distribution" Pine Script
indicator (Selling/Buying Climax confirmed by RSI at a price pivot), but
reformulated to be CAUSAL -- the original uses ta.pivotlow/pivothigh, which
need future bars to confirm a pivot, fine for a chart indicator drawn after
the fact but unusable for a real trading signal (this app already fixed one
look-ahead bug this way -- see trade_scenario's entry-timing history).

Core idea: entering the instant RSI first touches oversold can't be
distinguished from "still falling" (a real markdown can sit under 30 for
many bars) -- that's catching a falling knife, not a Wyckoff-style
confirmed reversal. Instead: wait for RSI to have dipped oversold recently
AND already be turning back up for a few bars before entering. Mirrors
"Spring" (bullish) with a "Upthrust" (bearish) equivalent on the
overbought/falling side, for symmetry with BULLISH_EVENTS/BEARISH_EVENTS
convention -- though the spot-trading context this was built for only
cares about the bullish side (no short-selling).

Not registered in app.strategies.registry -- this is a standalone
experiment run directly through app.services.scenario_backtest.walk_events
for backtesting, not yet a selectable strategy in the UI.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from app.wyckoff import Levels
from app.wyckoff.events import WyckoffEvent

RSI_PERIOD = 14
OVERSOLD_THRESHOLD = 30.0
OVERBOUGHT_THRESHOLD = 70.0
# N: how many bars back an oversold/overbought touch still "counts" as the
# same recovery the current rise/fall is confirming, not a stale, unrelated dip.
LOOKBACK_BARS = 10
# M: consecutive rising/falling bars required to confirm the turn has
# actually started, instead of firing on the very first uptick/downtick
# (which is indistinguishable from noise). Matches the Pine script's own
# 2-consecutive-bar convention for its bull/bear/side classification.
CONFIRM_BARS = 2

RSI_SPRING = "RSI Spring"
RSI_UPTHRUST = "RSI Upthrust"

BULLISH_EVENTS = {RSI_SPRING}
BEARISH_EVENTS = {RSI_UPTHRUST}
# No real market-structure/phase concept in this simple momentum detector --
# always "Ranging" (see analyze()) so the phase-before-event gate in
# trade_scenario._build_scenario_candidate never blocks it.
RANGING_PHASES = {"Ranging"}


@dataclass
class AnalysisResult:
    phase: str
    confidence: float
    events: list[WyckoffEvent]
    levels: Levels
    as_of: datetime | None
    drivers: list[str] = field(default_factory=list)


def compute_rsi(closes: list[float], period: int = RSI_PERIOD) -> list[float | None]:
    """Wilder's RSI. Returns one value per input close; None where there
    isn't yet `period` prior closes to average over. Flat prices (zero
    average gain AND loss) report a neutral 50.0 rather than an undefined
    0/0 division."""
    n = len(closes)
    rsi: list[float | None] = [None] * n
    if n <= period:
        return rsi

    gains = [max(closes[i] - closes[i - 1], 0.0) for i in range(1, n)]
    losses = [max(closes[i - 1] - closes[i], 0.0) for i in range(1, n)]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    rsi[period] = _rsi_from_averages(avg_gain, avg_loss)

    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        rsi[i + 1] = _rsi_from_averages(avg_gain, avg_loss)

    return rsi


def _rsi_from_averages(avg_gain: float, avg_loss: float) -> float:
    if avg_gain == 0.0 and avg_loss == 0.0:
        return 50.0
    if avg_loss == 0.0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def _confirmed_turn(rsi: list[float | None], i: int, rising: bool) -> bool:
    """True if bars (i - CONFIRM_BARS) .. i form a strictly monotonic
    rising (or falling) run of exactly this length -- i.e. the turn just
    completed at i, not a continuation of a longer run already signaled on
    an earlier bar."""
    if i - CONFIRM_BARS < 0:
        return False
    window = rsi[i - CONFIRM_BARS : i + 1]
    if any(v is None for v in window):
        return False
    steps_ok = all(
        (window[k + 1] > window[k]) if rising else (window[k + 1] < window[k])
        for k in range(len(window) - 1)
    )
    if not steps_ok:
        return False
    # The step just before this run must NOT continue the same direction --
    # otherwise this is bar N+1 of an already-longer run, already flagged
    # when the run first reached CONFIRM_BARS length.
    prev_idx = i - CONFIRM_BARS - 1
    if prev_idx < 0 or rsi[prev_idx] is None:
        return True
    prior_step_same_direction = (window[0] > rsi[prev_idx]) if rising else (window[0] < rsi[prev_idx])
    return not prior_step_same_direction


def _touched_within_lookback(rsi: list[float | None], i: int, threshold: float, below: bool) -> bool:
    start = max(0, i - CONFIRM_BARS - LOOKBACK_BARS)
    end = i - CONFIRM_BARS  # bars strictly before the confirmed-turn window
    return any(
        rsi[j] is not None and ((rsi[j] <= threshold) if below else (rsi[j] >= threshold))
        for j in range(start, end + 1)
    )


def detect_events(candles: list, rsi: list[float | None]) -> list[WyckoffEvent]:
    events: list[WyckoffEvent] = []
    for i in range(len(candles)):
        if rsi[i] is None:
            continue
        if _confirmed_turn(rsi, i, rising=True) and _touched_within_lookback(
            rsi, i, OVERSOLD_THRESHOLD, below=True
        ):
            events.append(WyckoffEvent(type=RSI_SPRING, index=i, ts=candles[i].bucket_start, price=candles[i].close))
        elif _confirmed_turn(rsi, i, rising=False) and _touched_within_lookback(
            rsi, i, OVERBOUGHT_THRESHOLD, below=False
        ):
            events.append(WyckoffEvent(type=RSI_UPTHRUST, index=i, ts=candles[i].bucket_start, price=candles[i].close))
    return events


def analyze(candles: list, config, daily_trend: str | None = None, language: str = "vi") -> AnalysisResult:
    if len(candles) < RSI_PERIOD + 1:
        return AnalysisResult(
            phase="Ranging", confidence=0.0, events=[], levels=Levels(support=0.0, resistance=0.0),
            as_of=candles[-1].bucket_start if candles else None,
        )

    closes = [c.close for c in candles]
    rsi = compute_rsi(closes)
    events = detect_events(candles, rsi)
    levels = Levels(support=min(c.low for c in candles), resistance=max(c.high for c in candles))

    return AnalysisResult(
        phase="Ranging", confidence=0.5, events=events, levels=levels, as_of=candles[-1].bucket_start,
    )
