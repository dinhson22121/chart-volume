"""SMC (Smart Money Concept) event detectors: market structure (BOS/CHoCH),
Order Blocks, and Fair Value Gaps.

Scope deliberately excludes liquidity sweeps / premium-discount zones --
Wyckoff's Spring/Upthrust already cover the "sweep then reverse" idea, so
adding an SMC-flavored duplicate would just be the same signal under a
different name.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import pandas as pd

from app.smc.config import DEFAULT_CONFIG, SMCConfig

BOS_BULL = "BOS_Bull"
BOS_BEAR = "BOS_Bear"
CHOCH_BULL = "CHoCH_Bull"
CHOCH_BEAR = "CHoCH_Bear"
BULLISH_OB = "BullishOB"
BEARISH_OB = "BearishOB"
BULLISH_FVG = "BullishFVG"
BEARISH_FVG = "BearishFVG"


@dataclass
class SMCEvent:
    type: str
    index: int
    ts: datetime
    price: float
    note: str = ""


def _ts_at(df: pd.DataFrame, i: int):
    ts = df["time"].iloc[i]
    return ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts


def _detect_structure(df: pd.DataFrame, language: str = "vi") -> list[SMCEvent]:
    """Walks confirmed swing highs/lows in chronological order, emitting BOS
    (break of structure -- continuation) or CHoCH (change of character --
    reversal) whenever price closes beyond the most recently "active" swing
    level. A broken level is consumed immediately so the same break can't
    keep re-triggering before a new swing point forms."""
    en = language == "en"
    events: list[SMCEvent] = []
    swing_highs = [(i, df["high"].iloc[i]) for i in range(len(df)) if df["swing_high"].iloc[i]]
    swing_lows = [(i, df["low"].iloc[i]) for i in range(len(df)) if df["swing_low"].iloc[i]]

    structure_trend: str | None = None  # None | "bullish" | "bearish"
    next_high_idx = 0
    next_low_idx = 0
    active_high: float | None = None
    active_low: float | None = None

    for i in range(len(df)):
        close = df["close"].iloc[i]

        # Advance to the most recent CONFIRMED swing high/low strictly before i.
        while next_high_idx < len(swing_highs) and swing_highs[next_high_idx][0] < i:
            active_high = swing_highs[next_high_idx][1]
            next_high_idx += 1
        while next_low_idx < len(swing_lows) and swing_lows[next_low_idx][0] < i:
            active_low = swing_lows[next_low_idx][1]
            next_low_idx += 1

        if active_high is not None and close > active_high:
            if structure_trend != "bullish":
                note = (
                    "Break above the last swing high while structure was bearish/undefined -- trend reversal"
                    if en
                    else "Phá vỡ đỉnh swing gần nhất trong khi xu hướng đang giảm/chưa rõ -- đổi chiều xu hướng"
                )
                events.append(SMCEvent(CHOCH_BULL, i, _ts_at(df, i), float(close), note))
            else:
                note = (
                    f"Closes above the last swing high {active_high:.2f} -- uptrend continues"
                    if en
                    else f"Đóng cửa vượt đỉnh swing gần nhất {active_high:.2f} -- xu hướng tăng tiếp diễn"
                )
                events.append(SMCEvent(BOS_BULL, i, _ts_at(df, i), float(close), note))
            structure_trend = "bullish"
            active_high = None  # consumed -- wait for the next confirmed swing high

        if active_low is not None and close < active_low:
            if structure_trend != "bearish":
                note = (
                    "Break below the last swing low while structure was bullish/undefined -- trend reversal"
                    if en
                    else "Phá vỡ đáy swing gần nhất trong khi xu hướng đang tăng/chưa rõ -- đổi chiều xu hướng"
                )
                events.append(SMCEvent(CHOCH_BEAR, i, _ts_at(df, i), float(close), note))
            else:
                note = (
                    f"Closes below the last swing low {active_low:.2f} -- downtrend continues"
                    if en
                    else f"Đóng cửa phá đáy swing gần nhất {active_low:.2f} -- xu hướng giảm tiếp diễn"
                )
                events.append(SMCEvent(BOS_BEAR, i, _ts_at(df, i), float(close), note))
            structure_trend = "bearish"
            active_low = None

    return events


def _find_order_block_index(df: pd.DataFrame, bos_index: int, cfg: SMCConfig, bullish: bool) -> int | None:
    """Last opposite-direction candle before ``bos_index``, within
    ``cfg.ob_lookback_bars`` -- the classic order-block definition."""
    start = max(0, bos_index - cfg.ob_lookback_bars)
    for i in range(bos_index - 1, start - 1, -1):
        is_down = df["close"].iloc[i] < df["open"].iloc[i]
        if bullish and is_down:
            return i
        if not bullish and not is_down:
            return i
    return None


def _detect_order_blocks(
    df: pd.DataFrame, structure_events: list[SMCEvent], cfg: SMCConfig, language: str = "vi"
) -> list[SMCEvent]:
    """Order blocks are anchored to BOS (continuation), not CHoCH -- CHoCH is
    the reversal point itself; the order block is the supply/demand zone left
    behind by the impulsive move that then confirms the new trend (BOS)."""
    en = language == "en"
    events: list[SMCEvent] = []
    for e in structure_events:
        if e.type == BOS_BULL:
            ob_idx = _find_order_block_index(df, e.index, cfg, bullish=True)
            if ob_idx is None:
                continue
            low, high = float(df["low"].iloc[ob_idx]), float(df["high"].iloc[ob_idx])
            note = (
                f"Last down candle before the breakout -- zone {low:.2f}-{high:.2f} often gets retested"
                if en
                else f"Nến giảm cuối cùng trước cú bứt phá -- vùng {low:.2f}-{high:.2f} hay được test lại"
            )
            events.append(SMCEvent(BULLISH_OB, ob_idx, _ts_at(df, ob_idx), float(df["close"].iloc[ob_idx]), note))
        elif e.type == BOS_BEAR:
            ob_idx = _find_order_block_index(df, e.index, cfg, bullish=False)
            if ob_idx is None:
                continue
            low, high = float(df["low"].iloc[ob_idx]), float(df["high"].iloc[ob_idx])
            note = (
                f"Last up candle before the breakdown -- zone {low:.2f}-{high:.2f} often gets retested"
                if en
                else f"Nến tăng cuối cùng trước cú gãy -- vùng {low:.2f}-{high:.2f} hay được test lại"
            )
            events.append(SMCEvent(BEARISH_OB, ob_idx, _ts_at(df, ob_idx), float(df["close"].iloc[ob_idx]), note))
    return events


def _detect_fvg(df: pd.DataFrame, cfg: SMCConfig, language: str = "vi") -> list[SMCEvent]:
    """3-candle fair value gap: the middle candle's impulsive move leaves a
    gap between candle[i-1] and candle[i+1] that price tends to revisit."""
    en = language == "en"
    events: list[SMCEvent] = []
    n = len(df)
    for i in range(1, n - 1):
        spread_ma = df["spread_ma"].iloc[i]
        if pd.isna(spread_ma):
            continue
        gap_threshold = cfg.fvg_min_gap_mult * spread_ma

        prev_high = df["high"].iloc[i - 1]
        next_low = df["low"].iloc[i + 1]
        if next_low > prev_high and (next_low - prev_high) >= gap_threshold:
            note = (
                f"Gap between {prev_high:.2f} and {next_low:.2f} left by the impulsive move -- price tends to fill it"
                if en
                else f"Khoảng trống giữa {prev_high:.2f} và {next_low:.2f} do nến bứt phá để lại -- giá hay quay lại lấp đầy"
            )
            events.append(SMCEvent(BULLISH_FVG, i, _ts_at(df, i), float(df["close"].iloc[i]), note))
            continue  # one bar can't be both a bullish and a bearish FVG's middle candle

        prev_low = df["low"].iloc[i - 1]
        next_high = df["high"].iloc[i + 1]
        if prev_low > next_high and (prev_low - next_high) >= gap_threshold:
            note = (
                f"Gap between {next_high:.2f} and {prev_low:.2f} left by the impulsive move -- price tends to fill it"
                if en
                else f"Khoảng trống giữa {next_high:.2f} và {prev_low:.2f} do nến bứt phá để lại -- giá hay quay lại lấp đầy"
            )
            events.append(SMCEvent(BEARISH_FVG, i, _ts_at(df, i), float(df["close"].iloc[i]), note))

    return events


def detect_events(df: pd.DataFrame, cfg: SMCConfig = DEFAULT_CONFIG, language: str = "vi") -> list[SMCEvent]:
    structure_events = _detect_structure(df, language)
    ob_events = _detect_order_blocks(df, structure_events, cfg, language)
    fvg_events = _detect_fvg(df, cfg, language)
    events = structure_events + ob_events + fvg_events
    events.sort(key=lambda e: e.index)
    return events
