"""Sonic R event detectors.

Two layers, mirroring the Wyckoff SOS/SOW -> LPS/LPSY pullback design:

1. Raw signals (informational, always emitted): DragonCrossUp/Down (price
   crosses the EMA(34) trend filter), SonicCrossUp/Down (T3 fast crosses T3
   slow -- a momentum turn).
2. SonicEntryLong/Short (the actual "optimized" entry signal): only emitted
   when a SonicCrossUp/Down bar also passes the Dragon-trend + CCI-zero-cross
   confirmation, AND price then pulls back to retest the Dragon line with a
   reversal bar within ``pullback_lookback_bars``, AND (when supplied) the
   higher-timeframe daily_trend does not conflict.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import pandas as pd

from app.sonicr.config import DEFAULT_CONFIG, SonicRConfig

DRAGON_CROSS_UP = "DragonCrossUp"
DRAGON_CROSS_DOWN = "DragonCrossDown"
SONIC_CROSS_UP = "SonicCrossUp"
SONIC_CROSS_DOWN = "SonicCrossDown"
SONIC_ENTRY_LONG = "SonicEntryLong"
SONIC_ENTRY_SHORT = "SonicEntryShort"

# Same daily_trend vocabulary produced by app.wyckoff.phase.phase_trend and
# passed in generically by app.services.analysis -- not imported directly to
# keep the two strategy packages decoupled.
TREND_BULLISH = "bullish"
TREND_BEARISH = "bearish"

PULLBACK_PRICE_TOLERANCE = 0.005  # +/-0.5% band around the Dragon line counts as "testing" it


@dataclass
class SonicEvent:
    type: str
    index: int
    ts: datetime
    price: float
    note: str = ""


def _make_event(event_type: str, index: int, row: pd.Series, note: str) -> SonicEvent:
    ts = row["time"]
    return SonicEvent(
        type=event_type,
        index=index,
        ts=ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts,
        price=float(row["close"]),
        note=note,
    )


def _detect_dragon_crosses(df: pd.DataFrame) -> list[SonicEvent]:
    events: list[SonicEvent] = []
    for i in range(1, len(df)):
        prev, row = df.iloc[i - 1], df.iloc[i]
        if pd.isna(prev["dragon"]) or pd.isna(row["dragon"]):
            continue
        if prev["close"] <= prev["dragon"] and row["close"] > row["dragon"]:
            events.append(_make_event(DRAGON_CROSS_UP, i, row, "Giá cắt lên Dragon EMA — đổi context sang tăng"))
        elif prev["close"] >= prev["dragon"] and row["close"] < row["dragon"]:
            events.append(_make_event(DRAGON_CROSS_DOWN, i, row, "Giá cắt xuống Dragon EMA — đổi context sang giảm"))
    return events


def _detect_sonic_crosses(df: pd.DataFrame) -> list[SonicEvent]:
    events: list[SonicEvent] = []
    for i in range(1, len(df)):
        prev, row = df.iloc[i - 1], df.iloc[i]
        if pd.isna(prev["t3_fast"]) or pd.isna(prev["t3_slow"]) or pd.isna(row["t3_fast"]) or pd.isna(row["t3_slow"]):
            continue
        if prev["t3_fast"] <= prev["t3_slow"] and row["t3_fast"] > row["t3_slow"]:
            events.append(_make_event(SONIC_CROSS_UP, i, row, "T3 fast cắt lên T3 slow — đổi động lượng tăng"))
        elif prev["t3_fast"] >= prev["t3_slow"] and row["t3_fast"] < row["t3_slow"]:
            events.append(_make_event(SONIC_CROSS_DOWN, i, row, "T3 fast cắt xuống T3 slow — đổi động lượng giảm"))
    return events


def _raw_candidate_ok(row: pd.Series, bullish: bool) -> bool:
    """Dragon-trend + CCI-zero-cross confirmation at the Sonic-cross bar itself."""
    if pd.isna(row["dragon"]) or pd.isna(row["cci_fast"]) or pd.isna(row["cci_slow"]):
        return False
    if bullish:
        return row["close"] > row["dragon"] and row["cci_fast"] > 0 and row["cci_slow"] > 0
    return row["close"] < row["dragon"] and row["cci_fast"] < 0 and row["cci_slow"] < 0


def _find_pullback(df: pd.DataFrame, from_index: int, cfg: SonicRConfig, bullish: bool) -> int | None:
    """First bar after ``from_index`` (within cfg.pullback_lookback_bars) that
    pulls back to retest the Dragon line and reverses -- the Sonic R
    equivalent of Wyckoff's LPS/LPSY quiet-pullback re-entry."""
    n = len(df)
    end = min(from_index + cfg.pullback_lookback_bars, n - 1)
    for i in range(from_index + 1, end + 1):
        row = df.iloc[i]
        dragon = row["dragon"]
        if pd.isna(dragon):
            continue
        if bullish:
            touched = row["low"] <= dragon * (1 + PULLBACK_PRICE_TOLERANCE)
            held = row["close"] > dragon
        else:
            touched = row["high"] >= dragon * (1 - PULLBACK_PRICE_TOLERANCE)
            held = row["close"] < dragon
        if touched and held:
            return i
    return None


def _mtf_conflicts(bullish: bool, daily_trend: str | None) -> bool:
    """None/"neutral" impose no restriction -- only an explicit opposing daily
    trend blocks the entry (mirrors app.wyckoff.phase.classify_phase's
    handling of daily_trend, including the no-MTF-context case for the daily
    timeframe itself)."""
    if daily_trend == TREND_BEARISH and bullish:
        return True
    if daily_trend == TREND_BULLISH and not bullish:
        return True
    return False


def _detect_entry_signals(
    df: pd.DataFrame,
    sonic_crosses: list[SonicEvent],
    cfg: SonicRConfig,
    daily_trend: str | None,
) -> list[SonicEvent]:
    events: list[SonicEvent] = []
    for cross in sonic_crosses:
        bullish = cross.type == SONIC_CROSS_UP
        row = df.iloc[cross.index]
        if not _raw_candidate_ok(row, bullish):
            continue
        if _mtf_conflicts(bullish, daily_trend):
            continue
        pullback_idx = _find_pullback(df, cross.index, cfg, bullish)
        if pullback_idx is None:
            continue
        prow = df.iloc[pullback_idx]
        label = "mua" if bullish else "bán"
        events.append(
            _make_event(
                SONIC_ENTRY_LONG if bullish else SONIC_ENTRY_SHORT,
                pullback_idx,
                prow,
                f"Pullback về test Dragon sau Sonic cross, xác nhận CCI + MTF — điểm vào {label} tối ưu",
            )
        )
    return events


def detect_events(
    df: pd.DataFrame, cfg: SonicRConfig = DEFAULT_CONFIG, daily_trend: str | None = None
) -> list[SonicEvent]:
    events: list[SonicEvent] = []
    events.extend(_detect_dragon_crosses(df))
    sonic_crosses = _detect_sonic_crosses(df)
    events.extend(sonic_crosses)
    events.extend(_detect_entry_signals(df, sonic_crosses, cfg, daily_trend))
    events.sort(key=lambda e: e.index)
    return events
