"""Smart Money Concept (SMC) rule-based analysis engine.

Market structure (BOS/CHoCH) + Order Blocks + Fair Value Gaps -- see
app.smc.events for the detection rules. Deliberately excludes liquidity
sweeps/premium-discount zones, which would just duplicate Wyckoff's
Spring/Upthrust under different names. AnalysisResult/Levels are reused as-is
from app.wyckoff since the shape (phase, confidence, events, levels, drivers,
mtf context) is strategy-agnostic.
"""

from __future__ import annotations

from app.smc.config import DEFAULT_CONFIG, SMCConfig
from app.smc.events import SMCEvent, detect_events
from app.smc.indicators import compute_features
from app.smc.phase import BEARISH_EVENTS, BULLISH_EVENTS, RANGING_PHASES, classify_structure, phase_trend
from app.wyckoff import MIN_BARS, AnalysisResult, Levels, candles_to_dataframe

__all__ = [
    "SMCConfig",
    "SMCEvent",
    "BULLISH_EVENTS",
    "BEARISH_EVENTS",
    "RANGING_PHASES",
    "phase_trend",
    "analyze",
]

_SWING_LOOKBACK_LEVELS = 20


def _latest_swing_levels(df, lookback: int = _SWING_LOOKBACK_LEVELS) -> tuple[float, float]:
    """Most recent swing low/high -- reuses the Levels.support/resistance
    fields for chart drawing (same simple rolling-window approach as
    app.sonicr, rather than tracking "the last structural swing" separately)."""
    window = df.iloc[-lookback:]
    return float(window["low"].min()), float(window["high"].max())


def analyze(
    candles, config: SMCConfig = DEFAULT_CONFIG, daily_trend: str | None = None, language: str = "vi"
) -> AnalysisResult:
    df = candles_to_dataframe(candles)
    if len(df) < MIN_BARS:
        return AnalysisResult(
            phase="Insufficient data",
            confidence=0.0,
            events=[],
            levels=Levels(support=0.0, resistance=0.0),
            as_of=(df["time"].iloc[-1].to_pydatetime() if not df.empty else None),
        )

    feat = compute_features(df, config)
    events = detect_events(feat, config, language)
    support, resistance = _latest_swing_levels(feat)
    phase, confidence, drivers, mtf_alignment = classify_structure(feat, events, daily_trend)

    return AnalysisResult(
        phase=phase,
        confidence=confidence,
        events=events,
        levels=Levels(support=support, resistance=resistance),
        as_of=df["time"].iloc[-1].to_pydatetime(),
        drivers=drivers,
        daily_trend=daily_trend,
        mtf_alignment=mtf_alignment,
    )
