"""Common interface every analysis strategy must satisfy.

A "strategy" takes a candle series + its own config object and returns an
``app.wyckoff.AnalysisResult``-shaped result (phase/confidence/events/levels).
Wyckoff is currently the only implementation, registered as a plain module in
``registry.py`` -- adding a second strategy later means writing a new module
with the same ``analyze()`` shape and adding one line to the registry; no
other module needs to change.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from app.wyckoff import AnalysisResult


@runtime_checkable
class Strategy(Protocol):
    def analyze(self, candles: list, config: Any, daily_trend: str | None = None) -> AnalysisResult: ...
