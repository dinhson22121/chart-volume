"""Volume Profile: approximates volume-at-price from OHLCV, then uses the
resulting Value Area to confirm whether a breakout/reversal event actually
cleared the range the market has been building consensus around, or merely
crossed the naive rolling high/low used elsewhere in this engine.

No tick data is available, so each bar's volume is spread uniformly across
its own [low, high] range and accumulated into equal-width price bins over a
trailing window -- the standard approximation when only OHLCV is on hand.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

import pandas as pd

from app.wyckoff.config import WyckoffConfig
from app.wyckoff.events import SOS, SOW, SPRING, UPTHRUST, WyckoffEvent

# Below this many bars, a volume profile over such a short window isn't a
# meaningful "developed" profile -- skip it entirely rather than compute one
# from too little history.
VP_MIN_BARS = 20

# Event types with a clear, well-established interpretation against a Value
# Area (a genuine range breakout or a sweep-and-reclaim). Other types
# (NoDemand/NoSupply/climaxes/LPS/LPSY) are left unconfirmed in this version --
# there isn't a similarly clean rule for them without fabricating one.
_VP_CHECKABLE = {SOS, SOW, SPRING, UPTHRUST}


@dataclass(frozen=True)
class VolumeProfile:
    poc: float
    value_area_high: float
    value_area_low: float


def compute_volume_profile(df: pd.DataFrame, cfg: WyckoffConfig) -> VolumeProfile | None:
    """Point of Control + Value Area over the last ``cfg.vp_lookback_bars``
    bars. None if there isn't enough history yet or the window has no price
    range at all."""
    if len(df) < max(cfg.vp_lookback_bars, VP_MIN_BARS):
        return None

    window = df.iloc[-cfg.vp_lookback_bars :]
    window_low = float(window["low"].min())
    window_high = float(window["high"].max())
    if window_high <= window_low:
        return None

    bins = cfg.vp_bins
    bin_width = (window_high - window_low) / bins
    bin_volumes = [0.0] * bins

    for _, bar in window.iterrows():
        bar_low, bar_high, bar_vol = float(bar["low"]), float(bar["high"]), float(bar["volume"])
        bar_range = bar_high - bar_low
        if bar_range <= 0:
            idx = min(int((bar_low - window_low) / bin_width), bins - 1)
            bin_volumes[idx] += bar_vol
            continue
        for i in range(bins):
            bin_low = window_low + i * bin_width
            bin_high = bin_low + bin_width
            overlap = min(bar_high, bin_high) - max(bar_low, bin_low)
            if overlap > 0:
                bin_volumes[i] += bar_vol * (overlap / bar_range)

    poc_idx = max(range(bins), key=lambda i: bin_volumes[i])
    poc = window_low + (poc_idx + 0.5) * bin_width

    total_volume = sum(bin_volumes)
    target = total_volume * cfg.vp_value_area_pct
    lo_idx = hi_idx = poc_idx
    covered = bin_volumes[poc_idx]
    while covered < target and (lo_idx > 0 or hi_idx < bins - 1):
        expand_low = bin_volumes[lo_idx - 1] if lo_idx > 0 else -1.0
        expand_high = bin_volumes[hi_idx + 1] if hi_idx < bins - 1 else -1.0
        if expand_high >= expand_low:
            hi_idx += 1
            covered += bin_volumes[hi_idx]
        else:
            lo_idx -= 1
            covered += bin_volumes[lo_idx]

    value_area_low = window_low + lo_idx * bin_width
    value_area_high = window_low + (hi_idx + 1) * bin_width
    return VolumeProfile(poc=poc, value_area_high=value_area_high, value_area_low=value_area_low)


def annotate_volume_confirmation(
    df: pd.DataFrame, events: list[WyckoffEvent], vp: VolumeProfile | None
) -> list[WyckoffEvent]:
    """Returns a new list -- events aren't mutated in place, only replaced
    (via ``dataclasses.replace``). ``df`` must be the same feature dataframe
    ``events`` was detected against, since confirmation looks up each event's
    own bar by ``event.index``.

    When ``vp`` is None (not enough history to compute a profile yet), every
    event is returned unchanged -- ``volume_confirmed`` stays at its default
    None ("not evaluated"), never False, so a caller can't mistake "we
    couldn't check" for "we checked and it failed"."""
    if vp is None:
        return events

    annotated: list[WyckoffEvent] = []
    for e in events:
        if e.type not in _VP_CHECKABLE:
            annotated.append(e)
            continue
        bar = df.iloc[e.index]
        if e.type == SOS:
            confirmed = bar["close"] > vp.value_area_high
        elif e.type == SOW:
            confirmed = bar["close"] < vp.value_area_low
        elif e.type == SPRING:
            confirmed = bar["low"] < vp.value_area_low and bar["close"] >= vp.value_area_low
        else:  # UPTHRUST
            confirmed = bar["high"] > vp.value_area_high and bar["close"] <= vp.value_area_high
        annotated.append(replace(e, volume_confirmed=bool(confirmed)))
    return annotated
