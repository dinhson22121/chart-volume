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
    # None = not evaluated (either not a Volume-Profile-checkable type, or no
    # profile could be computed yet) -- distinct from False (evaluated, not
    # confirmed). See app.wyckoff.volume_profile.
    volume_confirmed: bool | None = None


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

# Bilingual terms/labels used by trace_bar()'s Check descriptions -- kept as a
# lookup table (not duplicated code paths) since the same handful of labels
# repeat across several detectors.
_TERM = {
    "support": {"vi": "hỗ trợ", "en": "support"},
    "resistance": {"vi": "kháng cự", "en": "resistance"},
    "prev_session": {"vi": "phiên trước", "en": "previous session"},
    "no_prev_session": {"vi": "chưa có phiên trước", "en": "no previous session yet"},
}

_LABEL = {
    "insufficient_data": {"vi": "Đủ dữ liệu nền", "en": "Enough baseline data"},
    "vol_climax": {"vi": "Volume cao trào", "en": "Climactic volume"},
    "wide_spread": {"vi": "Spread rộng", "en": "Wide spread"},
    "narrow_spread": {"vi": "Spread hẹp", "en": "Narrow spread"},
    "low_vol": {"vi": "Volume thấp", "en": "Low volume"},
    "vol_confirm": {"vi": "Volume xác nhận", "en": "Volume confirmation"},
    "support_touch": {"vi": "Chạm/thủng hỗ trợ", "en": "Touches/breaks support"},
    "resistance_touch": {"vi": "Chạm/vượt kháng cự", "en": "Touches/exceeds resistance"},
    "support_break": {"vi": "Thủng hỗ trợ", "en": "Breaks support"},
    "resistance_break": {"vi": "Vượt kháng cự", "en": "Exceeds resistance"},
    "close_recover": {"vi": "Đóng cửa hồi phục", "en": "Closing recovery"},
    "close_weak": {"vi": "Đóng cửa suy yếu", "en": "Closing weakness"},
    "close_recover_above_support": {"vi": "Đóng cửa hồi lại trên hỗ trợ", "en": "Closes back above support"},
    "close_fall_below_resistance": {"vi": "Đóng cửa tụt lại dưới kháng cự", "en": "Closes back below resistance"},
    "close_upper_half": {"vi": "Đóng cửa nửa trên nến", "en": "Closes in upper half of the bar"},
    "close_lower_half": {"vi": "Đóng cửa nửa dưới nến", "en": "Closes in lower half of the bar"},
    "close_break_resistance": {"vi": "Đóng cửa bứt phá kháng cự", "en": "Closes breaking out above resistance"},
    "close_break_support": {"vi": "Đóng cửa gãy hỗ trợ", "en": "Closes breaking below support"},
    "close_near_high": {"vi": "Đóng cửa gần đỉnh nến", "en": "Closes near the bar's high"},
    "close_near_low": {"vi": "Đóng cửa gần đáy nến", "en": "Closes near the bar's low"},
    "up_bar_vs_prev": {"vi": "Nến tăng so với hôm trước", "en": "Up bar vs. previous session"},
    "down_bar_vs_prev": {"vi": "Nến giảm so với hôm trước", "en": "Down bar vs. previous session"},
}


def _t(table: dict, key: str, language: str) -> str:
    return table[key]["en" if language == "en" else "vi"]


def _cmp(actual: float, passed: bool, op: str, threshold: float, unit: str = "") -> str:
    return f"{actual:.2f}{unit} {op if passed else _flip(op)} {threshold:.2f}{unit}"


def _flip(op: str) -> str:
    return {"<": ">=", ">": "<=", ">=": "<", "<=": ">"}[op]


def trace_bar(row: pd.Series, cfg: WyckoffConfig = DEFAULT_CONFIG, language: str = "vi") -> list[DetectorTrace]:
    """Evaluate all 8 detectors against one bar, explaining why each matched or not.

    Unlike ``_classify_bar`` (which stops at the first match, priority order),
    this runs every detector independently so a UI can show "why wasn't this a
    Spring" as well as "why was this a Spring".
    """
    support = row["support"]
    resistance = row["resistance"]
    vr = row["vol_ratio"]
    sr = row["spread_ratio"]

    def lbl(key: str) -> str:
        return _t(_LABEL, key, language)

    def term(key: str) -> str:
        return _t(_TERM, key, language)

    if pd.isna(support) or pd.isna(resistance) or pd.isna(vr) or pd.isna(sr):
        insufficient_detail = (
            "Chưa đủ nến trước đó để tính hỗ trợ/kháng cự/volume trung bình"
            if language != "en"
            else "Not enough prior bars to compute support/resistance/average volume yet"
        )
        insufficient = [Check(label=lbl("insufficient_data"), passed=False, detail=insufficient_detail)]
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
    c1 = Check(lbl("vol_climax"), vr >= cfg.climax_vol_mult, _cmp(vr, vr >= cfg.climax_vol_mult, ">=", cfg.climax_vol_mult, "x"))
    c2 = Check(lbl("wide_spread"), sr >= cfg.wide_spread_mult, _cmp(sr, sr >= cfg.wide_spread_mult, ">=", cfg.wide_spread_mult, "x"))
    c3 = Check(lbl("support_touch"), low <= support, f"low {low:.2f} {'<=' if low <= support else '>'} {term('support')} {support:.2f}")
    c4 = Check(lbl("close_recover"), cloc >= 0.4, _cmp(cloc, cloc >= 0.4, ">=", 0.4))
    traces.append(DetectorTrace(SELLING_CLIMAX, all(c.passed for c in (c1, c2, c3, c4)), [c1, c2, c3, c4]))

    # Buying Climax
    c1 = Check(lbl("vol_climax"), vr >= cfg.climax_vol_mult, _cmp(vr, vr >= cfg.climax_vol_mult, ">=", cfg.climax_vol_mult, "x"))
    c2 = Check(lbl("wide_spread"), sr >= cfg.wide_spread_mult, _cmp(sr, sr >= cfg.wide_spread_mult, ">=", cfg.wide_spread_mult, "x"))
    c3 = Check(lbl("resistance_touch"), high >= resistance, f"high {high:.2f} {'>=' if high >= resistance else '<'} {term('resistance')} {resistance:.2f}")
    c4 = Check(lbl("close_weak"), cloc <= 0.6, _cmp(cloc, cloc <= 0.6, "<=", 0.6))
    traces.append(DetectorTrace(BUYING_CLIMAX, all(c.passed for c in (c1, c2, c3, c4)), [c1, c2, c3, c4]))

    # Spring
    c1 = Check(lbl("support_break"), low < below_support, f"low {low:.2f} {'<' if low < below_support else '>='} {term('support')} {support:.2f}")
    c2 = Check(lbl("close_recover_above_support"), close > support, f"close {close:.2f} {'>' if close > support else '<='} {term('support')} {support:.2f}")
    c3 = Check(lbl("close_upper_half"), cloc >= 0.5, _cmp(cloc, cloc >= 0.5, ">=", 0.5))
    traces.append(DetectorTrace(SPRING, all(c.passed for c in (c1, c2, c3)), [c1, c2, c3]))

    # Upthrust
    c1 = Check(lbl("resistance_break"), high > above_resistance, f"high {high:.2f} {'>' if high > above_resistance else '<='} {term('resistance')} {resistance:.2f}")
    c2 = Check(lbl("close_fall_below_resistance"), close < resistance, f"close {close:.2f} {'<' if close < resistance else '>='} {term('resistance')} {resistance:.2f}")
    c3 = Check(lbl("close_lower_half"), cloc <= 0.5, _cmp(cloc, cloc <= 0.5, "<=", 0.5))
    traces.append(DetectorTrace(UPTHRUST, all(c.passed for c in (c1, c2, c3)), [c1, c2, c3]))

    # SOS
    c1 = Check(lbl("close_break_resistance"), close > above_resistance, f"close {close:.2f} {'>' if close > above_resistance else '<='} {term('resistance')} {resistance:.2f}")
    c2 = Check(lbl("vol_confirm"), vr >= cfg.sos_vol_mult, _cmp(vr, vr >= cfg.sos_vol_mult, ">=", cfg.sos_vol_mult, "x"))
    c3 = Check(lbl("close_near_high"), cloc >= 0.7, _cmp(cloc, cloc >= 0.7, ">=", 0.7))
    traces.append(DetectorTrace(SOS, all(c.passed for c in (c1, c2, c3)), [c1, c2, c3]))

    # SOW
    c1 = Check(lbl("close_break_support"), close < below_support, f"close {close:.2f} {'<' if close < below_support else '>='} {term('support')} {support:.2f}")
    c2 = Check(lbl("vol_confirm"), vr >= cfg.sos_vol_mult, _cmp(vr, vr >= cfg.sos_vol_mult, ">=", cfg.sos_vol_mult, "x"))
    c3 = Check(lbl("close_near_low"), cloc <= 0.3, _cmp(cloc, cloc <= 0.3, "<=", 0.3))
    traces.append(DetectorTrace(SOW, all(c.passed for c in (c1, c2, c3)), [c1, c2, c3]))

    # No Demand
    c1 = Check(lbl("up_bar_vs_prev"), has_prev and close > prev_close, f"close > {term('prev_session')}" if has_prev else term("no_prev_session"))
    c2 = Check(lbl("narrow_spread"), sr <= cfg.narrow_spread_mult, _cmp(sr, sr <= cfg.narrow_spread_mult, "<=", cfg.narrow_spread_mult, "x"))
    c3 = Check(lbl("low_vol"), vr <= cfg.low_vol_mult, _cmp(vr, vr <= cfg.low_vol_mult, "<=", cfg.low_vol_mult, "x"))
    traces.append(DetectorTrace(NO_DEMAND, all(c.passed for c in (c1, c2, c3)), [c1, c2, c3]))

    # No Supply
    c1 = Check(lbl("down_bar_vs_prev"), has_prev and close < prev_close, f"close < {term('prev_session')}" if has_prev else term("no_prev_session"))
    c2 = Check(lbl("narrow_spread"), sr <= cfg.narrow_spread_mult, _cmp(sr, sr <= cfg.narrow_spread_mult, "<=", cfg.narrow_spread_mult, "x"))
    c3 = Check(lbl("low_vol"), vr <= cfg.low_vol_mult, _cmp(vr, vr <= cfg.low_vol_mult, "<=", cfg.low_vol_mult, "x"))
    traces.append(DetectorTrace(NO_SUPPLY, all(c.passed for c in (c1, c2, c3)), [c1, c2, c3]))

    return traces


def _classify_bar(row: pd.Series, cfg: WyckoffConfig, language: str = "vi") -> tuple[str, str] | None:
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
    en = language == "en"

    # 1. Spring: dips below support but closes back above it.
    if low < below_support and close > support and cloc >= 0.5:
        note = (
            f"Breaks below support {support:.2f} then closes back at {close:.2f}"
            if en else f"Thủng hỗ trợ {support:.2f} rồi đóng cửa hồi lại {close:.2f}"
        )
        return SPRING, note

    # 2. Upthrust: pokes above resistance but closes back below it.
    if high > above_resistance and close < resistance and cloc <= 0.5:
        note = (
            f"Exceeds resistance {resistance:.2f} then closes back down at {close:.2f}"
            if en else f"Vượt kháng cự {resistance:.2f} rồi đóng cửa tụt về {close:.2f}"
        )
        return UPTHRUST, note

    # 3. Selling Climax: climactic volume + wide spread near/below support, recovery close.
    if vr >= cfg.climax_vol_mult and sr >= cfg.wide_spread_mult and low <= support and cloc >= 0.4:
        note = (
            f"Volume spike ({vr:.1f}x) at the bottom, closing recovery"
            if en else f"Volume đột biến ({vr:.1f}x) tại đáy, đóng cửa hồi phục"
        )
        return SELLING_CLIMAX, note

    # 4. Buying Climax: climactic volume + wide spread near/above resistance, weak close.
    if vr >= cfg.climax_vol_mult and sr >= cfg.wide_spread_mult and high >= resistance and cloc <= 0.6:
        note = (
            f"Volume spike ({vr:.1f}x) at the top, closing weakness"
            if en else f"Volume đột biến ({vr:.1f}x) tại đỉnh, đóng cửa suy yếu"
        )
        return BUYING_CLIMAX, note

    # 5. SOS: strong close above resistance, high volume, close near high.
    if close > above_resistance and vr >= cfg.sos_vol_mult and cloc >= 0.7:
        note = (
            f"Breaks out above resistance {resistance:.2f} with {vr:.1f}x volume"
            if en else f"Bứt phá kháng cự {resistance:.2f} với volume {vr:.1f}x"
        )
        return SOS, note

    # 6. SOW: weak close below support, high volume, close near low.
    if close < below_support and vr >= cfg.sos_vol_mult and cloc <= 0.3:
        note = (
            f"Breaks below support {support:.2f} with {vr:.1f}x volume"
            if en else f"Gãy hỗ trợ {support:.2f} với volume {vr:.1f}x"
        )
        return SOW, note

    # 7. No Demand: up bar, narrow spread, low volume -> lack of buying interest.
    if not pd.isna(prev_close) and close > prev_close and sr <= cfg.narrow_spread_mult and vr <= cfg.low_vol_mult:
        note = (
            f"Up bar but narrow spread + low volume ({vr:.1f}x)"
            if en else f"Nến tăng nhưng spread hẹp + volume thấp ({vr:.1f}x)"
        )
        return NO_DEMAND, note

    # 8. No Supply: down bar, narrow spread, low volume -> lack of selling pressure.
    if not pd.isna(prev_close) and close < prev_close and sr <= cfg.narrow_spread_mult and vr <= cfg.low_vol_mult:
        note = (
            f"Down bar but narrow spread + low volume ({vr:.1f}x)"
            if en else f"Nến giảm nhưng spread hẹp + volume thấp ({vr:.1f}x)"
        )
        return NO_SUPPLY, note

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
    df: pd.DataFrame, base_events: list[WyckoffEvent], cfg: WyckoffConfig, language: str = "vi"
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
        # NOTE: preserves the pre-existing label/level mismatch from before this
        # change (level is resistance for SOS/support for SOW, but the label
        # text says the opposite) -- not fixing it here, out of scope for an
        # i18n pass; flagged separately.
        label = _t(_TERM, "support" if event.type == SOS else "resistance", language)
        note = (
            f"Pullback retesting {label} {level:.2f} on low volume — potential entry point"
            if language == "en"
            else f"Pullback về test lại {label} {level:.2f} với volume thấp — điểm vào tiềm năng"
        )
        lps_events.append(
            WyckoffEvent(
                type=LPS if event.type == SOS else LPSY,
                index=pullback_idx,
                ts=row["time"].to_pydatetime() if hasattr(row["time"], "to_pydatetime") else row["time"],
                price=float(row["close"]),
                note=note,
            )
        )
    return lps_events


def detect_events(df: pd.DataFrame, cfg: WyckoffConfig = DEFAULT_CONFIG, language: str = "vi") -> list[WyckoffEvent]:
    events: list[WyckoffEvent] = []
    for i in range(len(df)):
        row = df.iloc[i]
        match = _classify_bar(row, cfg, language)
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
    events.extend(detect_lps_signals(df, events, cfg, language))
    events.sort(key=lambda e: e.index)
    return events
