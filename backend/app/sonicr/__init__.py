"""Sonic R rule-based analysis engine.

Dragon (EMA 34) trend filter + T3 fast/slow momentum cross + CCI confirmation,
with the actual entry gated on a pullback retest of the Dragon line and
(optional) higher-timeframe alignment -- see app.sonicr.events for the full
entry rule. AnalysisResult/Levels are reused as-is from app.wyckoff since the
shape (phase, confidence, events, levels, drivers, mtf context) is
strategy-agnostic.
"""

from __future__ import annotations

from app.sonicr.config import DEFAULT_CONFIG, SonicRConfig
from app.sonicr.events import SonicEvent, detect_events
from app.sonicr.indicators import compute_features
from app.sonicr.phase import BEARISH_EVENTS, BULLISH_EVENTS, RANGING_PHASES, classify_regime, phase_trend
from app.wyckoff import MIN_BARS, AnalysisResult, Levels, candles_to_dataframe

__all__ = [
    "SonicRConfig",
    "SonicEvent",
    "BULLISH_EVENTS",
    "BEARISH_EVENTS",
    "RANGING_PHASES",
    "phase_trend",
    "analyze",
]

_SWING_LOOKBACK = 20


def _latest_swing_levels(df, lookback: int = _SWING_LOOKBACK) -> tuple[float, float]:
    """Most recent swing low/high -- reuses the Levels.support/resistance
    fields for chart drawing, even though Sonic R has no Wyckoff-style
    accumulation range."""
    window = df.iloc[-lookback:]
    return float(window["low"].min()), float(window["high"].max())


def analyze(
    candles, config: SonicRConfig = DEFAULT_CONFIG, daily_trend: str | None = None, language: str = "vi"
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
    events = detect_events(feat, config, daily_trend, language)
    support, resistance = _latest_swing_levels(feat)
    phase, confidence, drivers, mtf_alignment = classify_regime(feat, events, daily_trend)

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
