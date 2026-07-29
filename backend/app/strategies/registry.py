"""Registry of available analysis strategies.

Adding a strategy: implement it as a module (or object) exposing
``analyze(candles, config, daily_trend=None) -> AnalysisResult`` (see
``base.Strategy``), then add one entry below with a user-facing label. The
Settings API, Settings UI dropdown, and services.analysis all read from this
registry -- nothing else needs to change.
"""

from __future__ import annotations

import logging

from app import smc, sonicr, wyckoff
from app.strategies.base import Strategy

logger = logging.getLogger("chart_volume.strategies")

DEFAULT_STRATEGY = "wyckoff"

REGISTRY: dict[str, Strategy] = {
    "wyckoff": wyckoff,
    "sonicr": sonicr,
    "smc": smc,
}

LABELS: dict[str, str] = {
    "wyckoff": "Wyckoff (Accumulation/Distribution)",
    "sonicr": "Sonic R",
    "smc": "Smart Money Concept",
}


def list_strategies() -> list[dict[str, str]]:
    return [{"key": key, "label": LABELS.get(key, key)} for key in REGISTRY]


def get_strategy(key: str) -> Strategy:
    strategy = REGISTRY.get(key)
    if strategy is None:
        logger.warning("unknown strategy %r, falling back to %s", key, DEFAULT_STRATEGY)
        return REGISTRY[DEFAULT_STRATEGY]
    return strategy


def is_known(key: str) -> bool:
    return key in REGISTRY
