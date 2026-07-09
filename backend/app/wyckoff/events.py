"""Wyckoff event detectors.

Each bar emits at most one event; categories are checked in priority order so a
climactic bar isn't double-labelled. Thresholds are expressed as multiples of
rolling averages (volume / spread) and position of the close within the bar
(``close_loc``: 0 = close at low, 1 = close at high).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import pandas as pd

# --- Event type names ---
SELLING_CLIMAX = "SC"
BUYING_CLIMAX = "BC"
SPRING = "Spring"
UPTHRUST = "Upthrust"
SOS = "SOS"  # Sign of Strength
SOW = "SOW"  # Sign of Weakness
NO_DEMAND = "NoDemand"
NO_SUPPLY = "NoSupply"

# --- Thresholds ---
CLIMAX_VOL_MULT = 2.0
WIDE_SPREAD_MULT = 1.5
NARROW_SPREAD_MULT = 0.7
LOW_VOL_MULT = 0.7
SOS_VOL_MULT = 1.5
BREAK_EPS = 0.001  # require ~0.1% beyond a level to count as a break


@dataclass
class WyckoffEvent:
    type: str
    index: int
    ts: datetime
    price: float
    note: str = ""


def _classify_bar(row: pd.Series) -> tuple[str, str] | None:
    support = row["support"]
    resistance = row["resistance"]
    if pd.isna(support) or pd.isna(resistance) or pd.isna(row["vol_ratio"]) or pd.isna(row["spread_ratio"]):
        return None

    close = row["close"]
    high = row["high"]
    low = row["low"]
    cloc = row["close_loc"]
    vr = row["vol_ratio"]
    sr = row["spread_ratio"]
    prev_close = row["prev_close"]

    below_support = support * (1 - BREAK_EPS)
    above_resistance = resistance * (1 + BREAK_EPS)

    # 1. Spring: dips below support but closes back above it.
    if low < below_support and close > support and cloc >= 0.5:
        return SPRING, f"Thủng hỗ trợ {support:.2f} rồi đóng cửa hồi lại {close:.2f}"

    # 2. Upthrust: pokes above resistance but closes back below it.
    if high > above_resistance and close < resistance and cloc <= 0.5:
        return UPTHRUST, f"Vượt kháng cự {resistance:.2f} rồi đóng cửa tụt về {close:.2f}"

    # 3. Selling Climax: climactic volume + wide spread near/below support, recovery close.
    if vr >= CLIMAX_VOL_MULT and sr >= WIDE_SPREAD_MULT and low <= support and cloc >= 0.4:
        return SELLING_CLIMAX, f"Volume đột biến ({vr:.1f}x) tại đáy, đóng cửa hồi phục"

    # 4. Buying Climax: climactic volume + wide spread near/above resistance, weak close.
    if vr >= CLIMAX_VOL_MULT and sr >= WIDE_SPREAD_MULT and high >= resistance and cloc <= 0.6:
        return BUYING_CLIMAX, f"Volume đột biến ({vr:.1f}x) tại đỉnh, đóng cửa suy yếu"

    # 5. SOS: strong close above resistance, high volume, close near high.
    if close > above_resistance and vr >= SOS_VOL_MULT and cloc >= 0.7:
        return SOS, f"Bứt phá kháng cự {resistance:.2f} với volume {vr:.1f}x"

    # 6. SOW: weak close below support, high volume, close near low.
    if close < below_support and vr >= SOS_VOL_MULT and cloc <= 0.3:
        return SOW, f"Gãy hỗ trợ {support:.2f} với volume {vr:.1f}x"

    # 7. No Demand: up bar, narrow spread, low volume -> lack of buying interest.
    if not pd.isna(prev_close) and close > prev_close and sr <= NARROW_SPREAD_MULT and vr <= LOW_VOL_MULT:
        return NO_DEMAND, f"Nến tăng nhưng spread hẹp + volume thấp ({vr:.1f}x)"

    # 8. No Supply: down bar, narrow spread, low volume -> lack of selling pressure.
    if not pd.isna(prev_close) and close < prev_close and sr <= NARROW_SPREAD_MULT and vr <= LOW_VOL_MULT:
        return NO_SUPPLY, f"Nến giảm nhưng spread hẹp + volume thấp ({vr:.1f}x)"

    return None


def detect_events(df: pd.DataFrame) -> list[WyckoffEvent]:
    events: list[WyckoffEvent] = []
    for i in range(len(df)):
        row = df.iloc[i]
        match = _classify_bar(row)
        if match is None:
            continue
        event_type, note = match
        events.append(
            WyckoffEvent(
                type=event_type,
                index=i,
                ts=row["time"].to_pydatetime() if hasattr(row["time"], "to_pydatetime") else row["time"],
                price=float(row["close"]),
                note=note,
            )
        )
    return events
