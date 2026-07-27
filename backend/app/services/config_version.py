"""Short fingerprint of a strategy's tunable thresholds, so signal_outcomes/
trade_scenario rows can be told apart by *which* rules produced them.

Settings lets a user retune Wyckoff/SMC/SonicR thresholds at any time; the
detectors then start firing on different bars going forward. Without this tag,
a row created under yesterday's thresholds and a row created under today's
(retuned) thresholds sit in the same event_type pool with no way to tell them
apart -- pooling them silently mixes two different rule regimes into one
"win rate" that describes neither. Every consumer that creates a new row
computes this once from the same strategy_cfg it already has on hand and
persists it; existing rows are never rewritten (mirrors both services'
existing "immutable once set" pattern for their other fields).
"""

from __future__ import annotations

import dataclasses
import hashlib
import json


def compute(strategy: str, cfg) -> str:
    """``cfg`` must be a (frozen) dataclass instance -- WyckoffConfig/
    SMCConfig/SonicRConfig all qualify. Same strategy + same field values
    always yields the same tag; any field changing yields a different one."""
    payload = json.dumps(dataclasses.asdict(cfg), sort_keys=True)
    digest = hashlib.sha256(payload.encode()).hexdigest()[:8]
    return f"{strategy}:{digest}"
