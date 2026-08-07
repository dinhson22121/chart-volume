"""Wyckoff rule-based analysis engine.

Deterministic price-volume analysis producing a structured result (phase +
confidence + detected events + support/resistance). This struct is the *input*
to the LLM narrative step; the LLM never invents the phase itself.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd

from app.wyckoff.config import DEFAULT_CONFIG, WyckoffConfig
from app.wyckoff.events import WyckoffEvent, detect_events
from app.wyckoff.indicators import compute_features, latest_levels
from app.wyckoff.phase import BEARISH_EVENTS, BULLISH_EVENTS, RANGING_PHASES, classify_phase, phase_trend
from app.wyckoff.volume_profile import annotate_volume_confirmation, compute_volume_profile

__all__ = [
    "AnalysisResult",
    "Levels",
    "MIN_BARS",
    "BULLISH_EVENTS",
    "BEARISH_EVENTS",
    "RANGING_PHASES",
    "phase_trend",
    "analyze",
    "candles_to_dataframe",
]

MIN_BARS = 15

# NOTE: a MIN_RR_RATIO=3.0 was added and removed again the same day (see git
# history) -- it was backtest-"validated" using scenario_backtest.walk_events()
# output that wasn't filtered to is_bullish, so it silently included
# untradeable bearish-scenario R-multiples (the live app's real
# trade_scenario._create_scenarios path is bullish-only, spot/long-only).
# Re-measured bullish-only, it crushed the VN30 holdout sample from n=62 to
# n=6 -- far too small to trust in either direction. Don't re-add a
# MIN_RR_RATIO for Wyckoff without re-sweeping through a script that filters
# `scenario.is_bullish` before scoring.


@dataclass
class Levels:
    support: float
    resistance: float


@dataclass
class AnalysisResult:
    phase: str
    confidence: float
    events: list[WyckoffEvent]
    levels: Levels
    as_of: datetime | None
    drivers: list[str] = field(default_factory=list)
    daily_trend: str | None = None
    mtf_alignment: str | None = None
    # Volume Profile fields -- Wyckoff-only (see app.wyckoff.volume_profile).
    # SMC/SonicR never set these, so they stay None for those strategies.
    poc: float | None = None
    value_area_high: float | None = None
    value_area_low: float | None = None
    vp_alignment: str | None = None
    # SMC-only (see app.smc.zones) -- Premium/Discount/Equilibrium zone
    # boundaries + Strong/Weak High/Low labels, derived from the current
    # active swing range. Display-only reference context, never a gate on
    # entries. None for every other strategy.
    smc_zones: dict | None = None

    def events_as_dicts(self) -> list[dict]:
        return [
            {
                "type": e.type,
                "ts": e.ts.isoformat() if e.ts else None,
                "price": e.price,
                "note": e.note,
                # getattr, not e.volume_confirmed -- SMC/SonicR events don't
                # have this attribute (their own dataclasses), and this method
                # is shared across all 3 strategies.
                "volume_confirmed": getattr(e, "volume_confirmed", None),
                # Order Blocks only (see app.smc.events.SMCEvent) -- None/False
                # for every other event type across every strategy.
                "zone_low": getattr(e, "zone_low", None),
                "zone_high": getattr(e, "zone_high", None),
                "mitigated": getattr(e, "mitigated", False),
                # BOS/CHoCH only (see app.smc.events.SMCEvent) -- the broken
                # swing point's own timestamp/price, None for every other
                # event type across every strategy.
                "structure_level_ts": (
                    sl_ts.isoformat() if (sl_ts := getattr(e, "structure_level_ts", None)) else None
                ),
                "structure_level_price": getattr(e, "structure_level_price", None),
            }
            for e in self.events
        ]


def candles_to_dataframe(candles) -> pd.DataFrame:
    rows = [
        {
            "time": c.bucket_start,
            "open": float(c.open),
            "high": float(c.high),
            "low": float(c.low),
            "close": float(c.close),
            "volume": float(c.volume),
        }
        for c in candles
    ]
    df = pd.DataFrame(rows)
    if not df.empty:
        df["time"] = pd.to_datetime(df["time"])
        df = df.sort_values("time").reset_index(drop=True)
    return df


def analyze(
    candles, config: WyckoffConfig = DEFAULT_CONFIG, daily_trend: str | None = None, language: str = "vi"
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

    feat = compute_features(df)
    events = detect_events(feat, config, language)
    support, resistance = latest_levels(feat)
    vp = compute_volume_profile(feat, config)
    events = annotate_volume_confirmation(feat, events, vp)
    phase, confidence, drivers, mtf_alignment, vp_alignment = classify_phase(feat, events, daily_trend)

    return AnalysisResult(
        phase=phase,
        confidence=confidence,
        events=events,
        levels=Levels(support=support, resistance=resistance),
        as_of=df["time"].iloc[-1].to_pydatetime(),
        drivers=drivers,
        daily_trend=daily_trend,
        mtf_alignment=mtf_alignment,
        poc=vp.poc if vp else None,
        value_area_high=vp.value_area_high if vp else None,
        value_area_low=vp.value_area_low if vp else None,
        vp_alignment=vp_alignment,
    )
