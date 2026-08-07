"""Technical volume/price-based money-flow analysis.

Reintroduces the Volume Accumulation/Distribution detection logic removed
from app.wyckoff.accumulation (see commit removing "Volume Accumulation" as
a registered strategy) -- but as a standalone, read-only analysis/display
layer, not a trade-scenario-generating strategy. The underlying signal
(session volume > 2x its trailing 10-session average AND price moves > 1%
that session, adapted from FiinTrade's published "Tích lũy" methodology)
backtested with a genuinely strong, time-consistent standalone edge on the
full HOSE/HNX universe (holdout bootstrap CI entirely positive,
walk_forward_consistency=1.00, every chronological slice positive) before
removal -- it was removed ONLY because it was used as a confluence filter
gating other strategies' entries, which diluted SMC's edge. That failure
mode doesn't apply here: this module never gates or creates trades, it only
reports a recent inflow/outflow read for display and for grounding the AI
potential screener in real evidence instead of raw-candle guessing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

MONEY_FLOW_IN = "MoneyFlowIn"
MONEY_FLOW_OUT = "MoneyFlowOut"

NET_INFLOW = "inflow"
NET_OUTFLOW = "outflow"
NET_NEUTRAL = "neutral"

# Trailing session count the volume average is computed over, excluding the
# day itself -- "K.L TB 10 phiên" in the source methodology.
VOLUME_LOOKBACK = 10
# "tỷ lệ khối lượng > 2" in the source methodology.
VOLUME_RATIO_THRESHOLD = 2.0
# "%thay đổi giá > 1%" in the source methodology, applied symmetrically for
# the distribution/outflow mirror.
PRICE_CHANGE_PCT_THRESHOLD = 1.0
# How many of the most recent sessions analyze_money_flow() summarizes into
# a single net inflow/outflow/neutral read.
RECENT_WINDOW = 20


@dataclass
class MoneyFlowEvent:
    type: str  # MONEY_FLOW_IN | MONEY_FLOW_OUT
    index: int
    ts: datetime
    price: float
    volume_ratio: float
    price_change_pct: float


@dataclass
class MoneyFlowResult:
    events: list[MoneyFlowEvent] = field(default_factory=list)
    recent_in_count: int = 0
    recent_out_count: int = 0
    recent_window: int = RECENT_WINDOW
    net_signal: str = NET_NEUTRAL
    as_of: datetime | None = None


def detect_money_flow_events(candles: list) -> list[MoneyFlowEvent]:
    """Causal: day i's signal depends only on the closed trailing window
    candles[i-VOLUME_LOOKBACK:i] and candles[i-1].close -- never on candles
    at or after i, so a later day's move never retroactively relabels an
    earlier one."""
    events: list[MoneyFlowEvent] = []
    for i in range(VOLUME_LOOKBACK, len(candles)):
        window = candles[i - VOLUME_LOOKBACK : i]
        avg_volume = sum(c.volume for c in window) / VOLUME_LOOKBACK
        if avg_volume <= 0:
            continue
        volume_ratio = candles[i].volume / avg_volume
        if volume_ratio <= VOLUME_RATIO_THRESHOLD:
            continue

        prev_close = candles[i - 1].close
        if prev_close <= 0:
            continue
        pct_change = (candles[i].close - prev_close) / prev_close * 100

        if pct_change > PRICE_CHANGE_PCT_THRESHOLD:
            event_type = MONEY_FLOW_IN
        elif pct_change < -PRICE_CHANGE_PCT_THRESHOLD:
            event_type = MONEY_FLOW_OUT
        else:
            continue

        events.append(
            MoneyFlowEvent(
                type=event_type, index=i, ts=candles[i].bucket_start, price=candles[i].close,
                volume_ratio=volume_ratio, price_change_pct=pct_change,
            )
        )
    return events


def analyze_money_flow(candles: list, recent_window: int = RECENT_WINDOW) -> MoneyFlowResult:
    if not candles:
        return MoneyFlowResult()

    events = detect_money_flow_events(candles)
    cutoff = len(candles) - recent_window
    recent = [e for e in events if e.index >= cutoff]
    in_count = sum(1 for e in recent if e.type == MONEY_FLOW_IN)
    out_count = sum(1 for e in recent if e.type == MONEY_FLOW_OUT)

    if in_count > out_count:
        net_signal = NET_INFLOW
    elif out_count > in_count:
        net_signal = NET_OUTFLOW
    else:
        net_signal = NET_NEUTRAL

    return MoneyFlowResult(
        events=events, recent_in_count=in_count, recent_out_count=out_count,
        recent_window=recent_window, net_signal=net_signal, as_of=candles[-1].bucket_start,
    )


def result_as_dict(result: MoneyFlowResult) -> dict:
    return {
        "net_signal": result.net_signal,
        "recent_in_count": result.recent_in_count,
        "recent_out_count": result.recent_out_count,
        "recent_window": result.recent_window,
        "as_of": result.as_of.isoformat() if result.as_of else None,
        "events": [
            {
                "type": e.type,
                "ts": e.ts.isoformat(),
                "price": e.price,
                "volume_ratio": e.volume_ratio,
                "price_change_pct": e.price_change_pct,
            }
            for e in result.events
        ],
    }
