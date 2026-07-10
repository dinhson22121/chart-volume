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

from app.wyckoff.config import DEFAULT_CONFIG, WyckoffConfig

# --- Event type names ---
SELLING_CLIMAX = "SC"
BUYING_CLIMAX = "BC"
SPRING = "Spring"
UPTHRUST = "Upthrust"
SOS = "SOS"  # Sign of Strength
SOW = "SOW"  # Sign of Weakness
NO_DEMAND = "NoDemand"
NO_SUPPLY = "NoSupply"
LPS = "LPS"  # Last Point of Support: quiet pullback re-testing a broken resistance
LPSY = "LPSY"  # Last Point of Supply: quiet pullback re-testing a broken support

BREAK_EPS = 0.001  # require ~0.1% beyond a level to count as a break (not user-tunable)
LPS_PRICE_TOLERANCE = 0.01  # +/-1% band around the broken level counts as "testing" it


@dataclass
class WyckoffEvent:
    type: str
    index: int
    ts: datetime
    price: float
    note: str = ""


@dataclass
class Check:
    """One evaluated sub-condition of a detector, for decision tracing."""

    label: str
    passed: bool
    detail: str

    def __post_init__(self) -> None:
        # Comparisons on pandas/numpy scalars yield numpy.bool_, which
        # pydantic/FastAPI can't serialize -- normalize to a native Python bool.
        self.passed = bool(self.passed)


@dataclass
class DetectorTrace:
    """Full evaluation of one detector type against one bar, matched or not."""

    type: str
    matched: bool
    checks: list[Check]

    def __post_init__(self) -> None:
        self.matched = bool(self.matched)


_ALL_TYPES = (SELLING_CLIMAX, BUYING_CLIMAX, SPRING, UPTHRUST, SOS, SOW, NO_DEMAND, NO_SUPPLY)


def _cmp(actual: float, passed: bool, op: str, threshold: float, unit: str = "") -> str:
    return f"{actual:.2f}{unit} {op if passed else _flip(op)} {threshold:.2f}{unit}"


def _flip(op: str) -> str:
    return {"<": ">=", ">": "<=", ">=": "<", "<=": ">"}[op]


def trace_bar(row: pd.Series, cfg: WyckoffConfig = DEFAULT_CONFIG) -> list[DetectorTrace]:
    """Evaluate all 8 detectors against one bar, explaining why each matched or not.

    Unlike ``_classify_bar`` (which stops at the first match, priority order),
    this runs every detector independently so a UI can show "why wasn't this a
    Spring" as well as "why was this a Spring".
    """
    support = row["support"]
    resistance = row["resistance"]
    vr = row["vol_ratio"]
    sr = row["spread_ratio"]

    if pd.isna(support) or pd.isna(resistance) or pd.isna(vr) or pd.isna(sr):
        insufficient = [
            Check(
                label="Đủ dữ liệu nền",
                passed=False,
                detail="Chưa đủ nến trước đó để tính hỗ trợ/kháng cự/volume trung bình",
            )
        ]
        return [DetectorTrace(t, False, insufficient) for t in _ALL_TYPES]

    close = row["close"]
    high = row["high"]
    low = row["low"]
    cloc = row["close_loc"]
    prev_close = row["prev_close"]
    below_support = support * (1 - BREAK_EPS)
    above_resistance = resistance * (1 + BREAK_EPS)
    has_prev = not pd.isna(prev_close)

    traces: list[DetectorTrace] = []

    # Selling Climax
    c1 = Check("Volume cao trào", vr >= cfg.climax_vol_mult, _cmp(vr, vr >= cfg.climax_vol_mult, ">=", cfg.climax_vol_mult, "x"))
    c2 = Check("Spread rộng", sr >= cfg.wide_spread_mult, _cmp(sr, sr >= cfg.wide_spread_mult, ">=", cfg.wide_spread_mult, "x"))
    c3 = Check("Chạm/thủng hỗ trợ", low <= support, f"low {low:.2f} {'<=' if low <= support else '>'} hỗ trợ {support:.2f}")
    c4 = Check("Đóng cửa hồi phục", cloc >= 0.4, _cmp(cloc, cloc >= 0.4, ">=", 0.4))
    traces.append(DetectorTrace(SELLING_CLIMAX, all(c.passed for c in (c1, c2, c3, c4)), [c1, c2, c3, c4]))

    # Buying Climax
    c1 = Check("Volume cao trào", vr >= cfg.climax_vol_mult, _cmp(vr, vr >= cfg.climax_vol_mult, ">=", cfg.climax_vol_mult, "x"))
    c2 = Check("Spread rộng", sr >= cfg.wide_spread_mult, _cmp(sr, sr >= cfg.wide_spread_mult, ">=", cfg.wide_spread_mult, "x"))
    c3 = Check("Chạm/vượt kháng cự", high >= resistance, f"high {high:.2f} {'>=' if high >= resistance else '<'} kháng cự {resistance:.2f}")
    c4 = Check("Đóng cửa suy yếu", cloc <= 0.6, _cmp(cloc, cloc <= 0.6, "<=", 0.6))
    traces.append(DetectorTrace(BUYING_CLIMAX, all(c.passed for c in (c1, c2, c3, c4)), [c1, c2, c3, c4]))

    # Spring
    c1 = Check("Thủng hỗ trợ", low < below_support, f"low {low:.2f} {'<' if low < below_support else '>='} hỗ trợ {support:.2f}")
    c2 = Check("Đóng cửa hồi lại trên hỗ trợ", close > support, f"close {close:.2f} {'>' if close > support else '<='} hỗ trợ {support:.2f}")
    c3 = Check("Đóng cửa nửa trên nến", cloc >= 0.5, _cmp(cloc, cloc >= 0.5, ">=", 0.5))
    traces.append(DetectorTrace(SPRING, all(c.passed for c in (c1, c2, c3)), [c1, c2, c3]))

    # Upthrust
    c1 = Check("Vượt kháng cự", high > above_resistance, f"high {high:.2f} {'>' if high > above_resistance else '<='} kháng cự {resistance:.2f}")
    c2 = Check("Đóng cửa tụt lại dưới kháng cự", close < resistance, f"close {close:.2f} {'<' if close < resistance else '>='} kháng cự {resistance:.2f}")
    c3 = Check("Đóng cửa nửa dưới nến", cloc <= 0.5, _cmp(cloc, cloc <= 0.5, "<=", 0.5))
    traces.append(DetectorTrace(UPTHRUST, all(c.passed for c in (c1, c2, c3)), [c1, c2, c3]))

    # SOS
    c1 = Check("Đóng cửa bứt phá kháng cự", close > above_resistance, f"close {close:.2f} {'>' if close > above_resistance else '<='} kháng cự {resistance:.2f}")
    c2 = Check("Volume xác nhận", vr >= cfg.sos_vol_mult, _cmp(vr, vr >= cfg.sos_vol_mult, ">=", cfg.sos_vol_mult, "x"))
    c3 = Check("Đóng cửa gần đỉnh nến", cloc >= 0.7, _cmp(cloc, cloc >= 0.7, ">=", 0.7))
    traces.append(DetectorTrace(SOS, all(c.passed for c in (c1, c2, c3)), [c1, c2, c3]))

    # SOW
    c1 = Check("Đóng cửa gãy hỗ trợ", close < below_support, f"close {close:.2f} {'<' if close < below_support else '>='} hỗ trợ {support:.2f}")
    c2 = Check("Volume xác nhận", vr >= cfg.sos_vol_mult, _cmp(vr, vr >= cfg.sos_vol_mult, ">=", cfg.sos_vol_mult, "x"))
    c3 = Check("Đóng cửa gần đáy nến", cloc <= 0.3, _cmp(cloc, cloc <= 0.3, "<=", 0.3))
    traces.append(DetectorTrace(SOW, all(c.passed for c in (c1, c2, c3)), [c1, c2, c3]))

    # No Demand
    c1 = Check("Nến tăng so với hôm trước", has_prev and close > prev_close, "close > phiên trước" if has_prev else "chưa có phiên trước")
    c2 = Check("Spread hẹp", sr <= cfg.narrow_spread_mult, _cmp(sr, sr <= cfg.narrow_spread_mult, "<=", cfg.narrow_spread_mult, "x"))
    c3 = Check("Volume thấp", vr <= cfg.low_vol_mult, _cmp(vr, vr <= cfg.low_vol_mult, "<=", cfg.low_vol_mult, "x"))
    traces.append(DetectorTrace(NO_DEMAND, all(c.passed for c in (c1, c2, c3)), [c1, c2, c3]))

    # No Supply
    c1 = Check("Nến giảm so với hôm trước", has_prev and close < prev_close, "close < phiên trước" if has_prev else "chưa có phiên trước")
    c2 = Check("Spread hẹp", sr <= cfg.narrow_spread_mult, _cmp(sr, sr <= cfg.narrow_spread_mult, "<=", cfg.narrow_spread_mult, "x"))
    c3 = Check("Volume thấp", vr <= cfg.low_vol_mult, _cmp(vr, vr <= cfg.low_vol_mult, "<=", cfg.low_vol_mult, "x"))
    traces.append(DetectorTrace(NO_SUPPLY, all(c.passed for c in (c1, c2, c3)), [c1, c2, c3]))

    return traces


def _classify_bar(row: pd.Series, cfg: WyckoffConfig) -> tuple[str, str] | None:
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
    if vr >= cfg.climax_vol_mult and sr >= cfg.wide_spread_mult and low <= support and cloc >= 0.4:
        return SELLING_CLIMAX, f"Volume đột biến ({vr:.1f}x) tại đáy, đóng cửa hồi phục"

    # 4. Buying Climax: climactic volume + wide spread near/above resistance, weak close.
    if vr >= cfg.climax_vol_mult and sr >= cfg.wide_spread_mult and high >= resistance and cloc <= 0.6:
        return BUYING_CLIMAX, f"Volume đột biến ({vr:.1f}x) tại đỉnh, đóng cửa suy yếu"

    # 5. SOS: strong close above resistance, high volume, close near high.
    if close > above_resistance and vr >= cfg.sos_vol_mult and cloc >= 0.7:
        return SOS, f"Bứt phá kháng cự {resistance:.2f} với volume {vr:.1f}x"

    # 6. SOW: weak close below support, high volume, close near low.
    if close < below_support and vr >= cfg.sos_vol_mult and cloc <= 0.3:
        return SOW, f"Gãy hỗ trợ {support:.2f} với volume {vr:.1f}x"

    # 7. No Demand: up bar, narrow spread, low volume -> lack of buying interest.
    if not pd.isna(prev_close) and close > prev_close and sr <= cfg.narrow_spread_mult and vr <= cfg.low_vol_mult:
        return NO_DEMAND, f"Nến tăng nhưng spread hẹp + volume thấp ({vr:.1f}x)"

    # 8. No Supply: down bar, narrow spread, low volume -> lack of selling pressure.
    if not pd.isna(prev_close) and close < prev_close and sr <= cfg.narrow_spread_mult and vr <= cfg.low_vol_mult:
        return NO_SUPPLY, f"Nến giảm nhưng spread hẹp + volume thấp ({vr:.1f}x)"

    return None


def _find_pullback(df: pd.DataFrame, from_index: int, level: float, cfg: WyckoffConfig, bullish: bool) -> int | None:
    """First bar after ``from_index`` (within cfg.lps_lookback_bars) that quietly
    re-tests ``level`` -- the resistance/support just broken by a SOS/SOW -- on
    low volume without violating it. Returns that bar's index, or None."""
    n = len(df)
    end = min(from_index + cfg.lps_lookback_bars, n - 1)
    for i in range(from_index + 1, end + 1):
        row = df.iloc[i]
        vr = row["vol_ratio"]
        if pd.isna(vr) or vr > cfg.low_vol_mult:
            continue  # a genuine LPS/LPSY pullback is quiet (supply/demand drying up)
        if bullish:
            touched = row["low"] <= level * (1 + LPS_PRICE_TOLERANCE)
            held = row["close"] >= level * (1 - LPS_PRICE_TOLERANCE)
        else:
            touched = row["high"] >= level * (1 - LPS_PRICE_TOLERANCE)
            held = row["close"] <= level * (1 + LPS_PRICE_TOLERANCE)
        if touched and held:
            return i
    return None


def detect_lps_signals(
    df: pd.DataFrame, base_events: list[WyckoffEvent], cfg: WyckoffConfig
) -> list[WyckoffEvent]:
    """LPS (after SOS) / LPSY (after SOW): the classic Wyckoff re-entry point --
    a quiet pullback that re-tests the just-broken level without violating it,
    safer to act on than the SOS/SOW breakout bar itself."""
    lps_events: list[WyckoffEvent] = []
    for event in base_events:
        if event.type not in (SOS, SOW):
            continue
        level = df.iloc[event.index]["resistance"] if event.type == SOS else df.iloc[event.index]["support"]
        if pd.isna(level):
            continue
        pullback_idx = _find_pullback(df, event.index, level, cfg, bullish=event.type == SOS)
        if pullback_idx is None:
            continue
        row = df.iloc[pullback_idx]
        label = "hỗ trợ" if event.type == SOS else "kháng cự"
        lps_events.append(
            WyckoffEvent(
                type=LPS if event.type == SOS else LPSY,
                index=pullback_idx,
                ts=row["time"].to_pydatetime() if hasattr(row["time"], "to_pydatetime") else row["time"],
                price=float(row["close"]),
                note=f"Pullback về test lại {label} {level:.2f} với volume thấp — điểm vào tiềm năng",
            )
        )
    return lps_events


def detect_events(df: pd.DataFrame, cfg: WyckoffConfig = DEFAULT_CONFIG) -> list[WyckoffEvent]:
    events: list[WyckoffEvent] = []
    for i in range(len(df)):
        row = df.iloc[i]
        match = _classify_bar(row, cfg)
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
    events.extend(detect_lps_signals(df, events, cfg))
    events.sort(key=lambda e: e.index)
    return events
