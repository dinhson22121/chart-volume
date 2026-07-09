"""Wyckoff rule-based analysis engine.

Deterministic price-volume analysis producing a structured result (phase +
confidence + detected events + support/resistance). This struct is the *input*
to the LLM narrative step; the LLM never invents the phase itself.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd

from app.wyckoff.events import WyckoffEvent, detect_events
from app.wyckoff.indicators import compute_features, latest_levels
from app.wyckoff.phase import classify_phase

MIN_BARS = 15


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

    def events_as_dicts(self) -> list[dict]:
        return [
            {
                "type": e.type,
                "ts": e.ts.isoformat() if e.ts else None,
                "price": e.price,
                "note": e.note,
            }
            for e in self.events
        ]


def _to_dataframe(candles) -> pd.DataFrame:
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


def analyze(candles) -> AnalysisResult:
    df = _to_dataframe(candles)
    if len(df) < MIN_BARS:
        return AnalysisResult(
            phase="Insufficient data",
            confidence=0.0,
            events=[],
            levels=Levels(support=0.0, resistance=0.0),
            as_of=(df["time"].iloc[-1].to_pydatetime() if not df.empty else None),
        )

    feat = compute_features(df)
    events = detect_events(feat)
    support, resistance = latest_levels(feat)
    phase, confidence, drivers = classify_phase(feat, events)

    return AnalysisResult(
        phase=phase,
        confidence=confidence,
        events=events,
        levels=Levels(support=support, resistance=resistance),
        as_of=df["time"].iloc[-1].to_pydatetime(),
        drivers=drivers,
    )
