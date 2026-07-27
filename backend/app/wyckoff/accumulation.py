"""Experimental volume-breakout entry signal, adapted from FiinTrade's public
"Tích lũy" (Accumulation) TA-strategy methodology (fiintrade_technical-
analysis-methodology_v1-1.md, section 5.1): a session's volume spikes to
more than 2x its trailing 10-session average AND price moves more than 1%
that same session.

FiinTrade's own formula estimates a FULL day's volume from a partial
intraday read (``T.K.L ước lượng = volume khớp * total_session_time /
elapsed_time``) because their system flags this intraday, mid-session. This
app only backfills complete EOD daily candles, so that estimation step is
unnecessary here -- the session's already-final volume is used directly
against the trailing average.

A pure volume/price-momentum idea, structurally unrelated to Wyckoff phase
detection or RSI (app.wyckoff.rsi_spring). Backtested against the full
HOSE/HNX universe with the SAME thresholds FiinTrade publishes (no fitting
to this app's own data): holdout bootstrap CI entirely positive
([+0.031, +0.075] mean_r, n=2361) and walk_forward_consistency=1.00, the
strongest and most time-consistent result found this session -- see
scripts/backtest_accumulation.py. Registered in app.strategies.registry as
"accumulation", selectable in the UI like Wyckoff/SMC/Sonic R.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.wyckoff import AnalysisResult, Levels
from app.wyckoff.events import WyckoffEvent

# K.L TB 10 phiên: trailing window (session count), excluding the day itself.
VOLUME_LOOKBACK = 10
# "tỷ lệ khối lượng > 2" in the source methodology.
VOLUME_RATIO_THRESHOLD = 2.0
# "%thay đổi giá > 1%" in the source methodology -- applied symmetrically
# (< -1%) for the bearish/distribution mirror, for the same
# BULLISH_EVENTS/BEARISH_EVENTS symmetry app.wyckoff.rsi_spring already uses.
PRICE_CHANGE_PCT_THRESHOLD = 1.0

VOLUME_ACCUMULATION = "Volume Accumulation"
VOLUME_DISTRIBUTION = "Volume Distribution"

BULLISH_EVENTS = {VOLUME_ACCUMULATION}
BEARISH_EVENTS = {VOLUME_DISTRIBUTION}
# No market-structure/phase concept in this simple volume/price screen --
# always "Ranging" (see analyze()) so the phase-before-event gate in
# trade_scenario._build_scenario_candidate never blocks it.
RANGING_PHASES = {"Ranging"}


@dataclass(frozen=True)
class AccumulationConfig:
    """User-tunable thresholds, mirroring app.wyckoff.config.WyckoffConfig's
    shape -- required to be a (frozen) dataclass by
    app.services.config_version.compute, which every registered strategy's
    config goes through. Not yet exposed in the Settings API/UI; the module
    constants above double as its defaults."""

    volume_lookback: int = VOLUME_LOOKBACK
    volume_ratio_threshold: float = VOLUME_RATIO_THRESHOLD
    price_change_pct_threshold: float = PRICE_CHANGE_PCT_THRESHOLD


DEFAULT_CONFIG = AccumulationConfig()


def detect_events(candles: list, config: AccumulationConfig | None = None) -> list[WyckoffEvent]:
    """Causal: day i's signal depends only on the CLOSED trailing window
    candles[i-volume_lookback:i] and candles[i-1].close -- never on candles
    at or after i, and never revisited once a later day's own window has
    moved past it."""
    cfg = config or DEFAULT_CONFIG
    events: list[WyckoffEvent] = []
    for i in range(cfg.volume_lookback, len(candles)):
        window = candles[i - cfg.volume_lookback : i]
        avg_volume = sum(c.volume for c in window) / cfg.volume_lookback
        if avg_volume <= 0:
            continue
        volume_ratio = candles[i].volume / avg_volume
        if volume_ratio <= cfg.volume_ratio_threshold:
            continue

        prev_close = candles[i - 1].close
        if prev_close <= 0:
            continue
        pct_change = (candles[i].close - prev_close) / prev_close * 100

        if pct_change > cfg.price_change_pct_threshold:
            events.append(
                WyckoffEvent(type=VOLUME_ACCUMULATION, index=i, ts=candles[i].bucket_start, price=candles[i].close)
            )
        elif pct_change < -cfg.price_change_pct_threshold:
            events.append(
                WyckoffEvent(type=VOLUME_DISTRIBUTION, index=i, ts=candles[i].bucket_start, price=candles[i].close)
            )
    return events


def phase_trend(phase: str) -> str:
    """Always "neutral" -- this detector has no market-structure/phase
    vocabulary of its own (analyze() always reports "Ranging"), so there's
    no directional trend to report for multi-timeframe alignment."""
    return "neutral"


def analyze(
    candles: list, config: AccumulationConfig | None = None, daily_trend: str | None = None, language: str = "vi"
) -> AnalysisResult:
    cfg = config or DEFAULT_CONFIG
    if len(candles) <= cfg.volume_lookback:
        return AnalysisResult(
            phase="Ranging", confidence=0.0, events=[], levels=Levels(support=0.0, resistance=0.0),
            as_of=candles[-1].bucket_start if candles else None,
        )

    events = detect_events(candles, cfg)
    levels = Levels(support=min(c.low for c in candles), resistance=max(c.high for c in candles))

    return AnalysisResult(
        phase="Ranging", confidence=0.5, events=events, levels=levels, as_of=candles[-1].bucket_start,
    )
