"""SMC (Smart Money Concept) event detectors: market structure (BOS/CHoCH,
two tiers), Order Blocks, Fair Value Gaps, Equal Highs/Lows, and Liquidity
Sweeps.

Two independent structure tiers, matching LuxAlgo's reference indicator:
"internal" (the original detector, swing_lookback -- despite the name, this
app's long-tested default of 2 bars is closer to LuxAlgo's fast "internal"
tier than to a real major-structure one) and "swing"/major (new,
major_swing_lookback, matching this app's own established 20-bar
convention). Both run the exact same BOS/CHoCH/Order-Block logic, just
parameterized on which swing_high/swing_low columns and event-type
constants to use -- see _detect_structure_tier/_detect_order_blocks_tier.

Premium/Discount/Equilibrium zones (see app.smc.zones) were previously
excluded here as "duplicative of Wyckoff Spring/Upthrust" -- re-added as a
display-only reference (support/resistance-style context, not a gate on
entries), not a duplicate signal.

Liquidity Sweeps (_detect_liquidity_sweeps) ports LuxAlgo's SEPARATE
"Liquidity Sweeps" indicator ("Wicks + Outbreaks & Retest" mode) -- its own
pivot pass (sweep_lookback), unrelated to the two structure tiers above.
Tried as a confirmation GATE on BOS/CHoCH/Order-Block trade creation, shipped
without a prior backtest per explicit user direction -- a backtest
afterward (scripts/backtest_liquidity_sweep_gate.py) showed every tested
lookback window underperformed no gate at all, so the gate was removed
(see app.smc.phase's history). LiquiditySweep_Bull/Bear are still detected
and recorded via signal_outcomes, display-only like Equal Highs/Lows.
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
EQUAL_HIGH = "EqualHigh"
EQUAL_LOW = "EqualLow"

# Liquidity Sweeps (LuxAlgo's separate "Liquidity Sweeps" indicator -- see
# _detect_liquidity_sweeps): a bullish sweep is either a wick below a swing
# low that closes back above it (a stop-hunt reversal, the SMC equivalent of
# Wyckoff's Spring), OR a retest that holds above a previously-broken swing
# high (continuation confirmation). Bearish is the mirror. Both mechanisms
# collapse to these 2 event types, matching LuxAlgo's own "Wicks + Outbreaks
# & Retest" mode (both colored the same regardless of which mechanism fired).
LIQUIDITY_SWEEP_BULL = "LiquiditySweep_Bull"
LIQUIDITY_SWEEP_BEAR = "LiquiditySweep_Bear"

# Major/"swing" structure tier -- see module docstring. Distinct event-type
# strings (not a reuse of the tier above) so BOTH tiers can coexist in the
# same event stream and signal_outcomes/trade_scenario history without one
# silently overwriting what the other means.
SWING_BOS_BULL = "SwingBOS_Bull"
SWING_BOS_BEAR = "SwingBOS_Bear"
SWING_CHOCH_BULL = "SwingCHoCH_Bull"
SWING_CHOCH_BEAR = "SwingCHoCH_Bear"
SWING_BULLISH_OB = "SwingBullishOB"
SWING_BEARISH_OB = "SwingBearishOB"


@dataclass
class SMCEvent:
    type: str
    index: int
    ts: datetime
    price: float
    note: str = ""
    # Order Blocks only (see _detect_order_blocks): True once price has
    # closed back through the zone after formation -- the zone's premise
    # (unfilled supply/demand) no longer holds. Always False for every other
    # event type, which has no equivalent "still valid?" concept.
    mitigated: bool = False
    # Order Blocks only: the anchor candle's own low/high -- the actual price
    # RANGE the zone spans (chart rendering draws this as a band, not just
    # the single `price` point). None for every other event type, which has
    # no zone-shaped concept of its own.
    zone_low: float | None = None
    zone_high: float | None = None


def _ts_at(df: pd.DataFrame, i: int):
    ts = df["time"].iloc[i]
    return ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts


def _detect_structure_tier(
    df: pd.DataFrame,
    swing_high_col: str,
    swing_low_col: str,
    bos_bull: str,
    bos_bear: str,
    choch_bull: str,
    choch_bear: str,
    language: str = "vi",
) -> list[SMCEvent]:
    """Walks confirmed swing highs/lows in chronological order, emitting BOS
    (break of structure -- continuation) or CHoCH (change of character --
    reversal) whenever price closes beyond the most recently "active" swing
    level. A broken level is consumed immediately so the same break can't
    keep re-triggering before a new swing point forms.

    Parameterized on which swing_high/swing_low columns to read and which
    event-type strings to emit -- see module docstring on the two structure
    tiers this drives (``_detect_structure``/``_detect_major_structure``)."""
    en = language == "en"
    events: list[SMCEvent] = []
    swing_highs = [(i, df["high"].iloc[i]) for i in range(len(df)) if df[swing_high_col].iloc[i]]
    swing_lows = [(i, df["low"].iloc[i]) for i in range(len(df)) if df[swing_low_col].iloc[i]]

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
                events.append(SMCEvent(choch_bull, i, _ts_at(df, i), float(close), note))
            else:
                note = (
                    f"Closes above the last swing high {active_high:.2f} -- uptrend continues"
                    if en
                    else f"Đóng cửa vượt đỉnh swing gần nhất {active_high:.2f} -- xu hướng tăng tiếp diễn"
                )
                events.append(SMCEvent(bos_bull, i, _ts_at(df, i), float(close), note))
            structure_trend = "bullish"
            active_high = None  # consumed -- wait for the next confirmed swing high

        if active_low is not None and close < active_low:
            if structure_trend != "bearish":
                note = (
                    "Break below the last swing low while structure was bullish/undefined -- trend reversal"
                    if en
                    else "Phá vỡ đáy swing gần nhất trong khi xu hướng đang tăng/chưa rõ -- đổi chiều xu hướng"
                )
                events.append(SMCEvent(choch_bear, i, _ts_at(df, i), float(close), note))
            else:
                note = (
                    f"Closes below the last swing low {active_low:.2f} -- downtrend continues"
                    if en
                    else f"Đóng cửa phá đáy swing gần nhất {active_low:.2f} -- xu hướng giảm tiếp diễn"
                )
                events.append(SMCEvent(bos_bear, i, _ts_at(df, i), float(close), note))
            structure_trend = "bearish"
            active_low = None

    return events


def _detect_structure(df: pd.DataFrame, language: str = "vi") -> list[SMCEvent]:
    return _detect_structure_tier(df, "swing_high", "swing_low", BOS_BULL, BOS_BEAR, CHOCH_BULL, CHOCH_BEAR, language)


def _detect_major_structure(df: pd.DataFrame, language: str = "vi") -> list[SMCEvent]:
    return _detect_structure_tier(
        df, "major_swing_high", "major_swing_low",
        SWING_BOS_BULL, SWING_BOS_BEAR, SWING_CHOCH_BULL, SWING_CHOCH_BEAR, language,
    )


def _is_high_volatility_bar(df: pd.DataFrame, i: int, cfg: SMCConfig) -> bool:
    """A wick/spike outlier bar makes a poor OB anchor -- its own range dwarfs
    the zone a real order block should represent. NaN spread_ma (too early in
    the series) never disqualifies a candle -- there's nothing to compare
    against yet, so it degrades to the pre-filter behavior for that bar."""
    spread_ma = df["spread_ma"].iloc[i]
    if pd.isna(spread_ma):
        return False
    spread = df["high"].iloc[i] - df["low"].iloc[i]
    return bool(spread >= cfg.ob_volatility_mult * spread_ma)


def _find_order_block_index(df: pd.DataFrame, bos_index: int, cfg: SMCConfig, bullish: bool) -> int | None:
    """Last opposite-direction candle before ``bos_index``, within
    ``cfg.ob_lookback_bars`` -- the classic order-block definition. Skips a
    candidate whose own range is abnormally large (see
    _is_high_volatility_bar) and keeps searching further back instead."""
    start = max(0, bos_index - cfg.ob_lookback_bars)
    for i in range(bos_index - 1, start - 1, -1):
        if _is_high_volatility_bar(df, i, cfg):
            continue
        is_down = df["close"].iloc[i] < df["open"].iloc[i]
        if bullish and is_down:
            return i
        if not bullish and not is_down:
            return i
    return None


def _is_mitigated(df: pd.DataFrame, ob_idx: int, low: float, high: float, bullish: bool) -> bool:
    """True once a later bar closes back through the OB zone -- for a
    bullish OB (demand zone), a close below its low; for a bearish OB
    (supply zone), a close above its high. Scans the WHOLE known future here
    (detect_events sees the full history at once) -- live tracking re-derives
    this fresh on every call against whatever candles have arrived so far, so
    it can never see a mitigation before it actually happened."""
    subsequent_closes = df["close"].iloc[ob_idx + 1 :]
    if bullish:
        return bool((subsequent_closes < low).any())
    return bool((subsequent_closes > high).any())


def _detect_order_blocks_tier(
    df: pd.DataFrame,
    structure_events: list[SMCEvent],
    cfg: SMCConfig,
    source_bos_bull: str,
    source_bos_bear: str,
    bullish_ob: str,
    bearish_ob: str,
    language: str = "vi",
) -> list[SMCEvent]:
    """Order blocks are anchored to BOS (continuation), not CHoCH -- CHoCH is
    the reversal point itself; the order block is the supply/demand zone left
    behind by the impulsive move that then confirms the new trend (BOS).

    Parameterized so both structure tiers (see module docstring) can each get
    their own order blocks from their own BOS events, tagged with their own
    distinct event-type strings."""
    en = language == "en"
    events: list[SMCEvent] = []
    for e in structure_events:
        if e.type == source_bos_bull:
            ob_idx = _find_order_block_index(df, e.index, cfg, bullish=True)
            if ob_idx is None:
                continue
            low, high = float(df["low"].iloc[ob_idx]), float(df["high"].iloc[ob_idx])
            mitigated = _is_mitigated(df, ob_idx, low, high, bullish=True)
            note = (
                f"Last down candle before the breakout -- zone {low:.2f}-{high:.2f} often gets retested"
                if en
                else f"Nến giảm cuối cùng trước cú bứt phá -- vùng {low:.2f}-{high:.2f} hay được test lại"
            )
            events.append(
                SMCEvent(
                    bullish_ob, ob_idx, _ts_at(df, ob_idx), float(df["close"].iloc[ob_idx]), note, mitigated,
                    zone_low=low, zone_high=high,
                )
            )
        elif e.type == source_bos_bear:
            ob_idx = _find_order_block_index(df, e.index, cfg, bullish=False)
            if ob_idx is None:
                continue
            low, high = float(df["low"].iloc[ob_idx]), float(df["high"].iloc[ob_idx])
            mitigated = _is_mitigated(df, ob_idx, low, high, bullish=False)
            note = (
                f"Last up candle before the breakdown -- zone {low:.2f}-{high:.2f} often gets retested"
                if en
                else f"Nến tăng cuối cùng trước cú gãy -- vùng {low:.2f}-{high:.2f} hay được test lại"
            )
            events.append(
                SMCEvent(
                    bearish_ob, ob_idx, _ts_at(df, ob_idx), float(df["close"].iloc[ob_idx]), note, mitigated,
                    zone_low=low, zone_high=high,
                )
            )
    return events


def _detect_order_blocks(
    df: pd.DataFrame, structure_events: list[SMCEvent], cfg: SMCConfig, language: str = "vi"
) -> list[SMCEvent]:
    return _detect_order_blocks_tier(df, structure_events, cfg, BOS_BULL, BOS_BEAR, BULLISH_OB, BEARISH_OB, language)


def _detect_major_order_blocks(
    df: pd.DataFrame, structure_events: list[SMCEvent], cfg: SMCConfig, language: str = "vi"
) -> list[SMCEvent]:
    return _detect_order_blocks_tier(
        df, structure_events, cfg, SWING_BOS_BULL, SWING_BOS_BEAR, SWING_BULLISH_OB, SWING_BEARISH_OB, language,
    )


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


def _detect_equal_highs_lows(df: pd.DataFrame, cfg: SMCConfig, language: str = "vi") -> list[SMCEvent]:
    """A liquidity pool: two consecutive swing highs (or lows) sitting within
    ``cfg.eq_threshold_mult * spread_ma`` of each other -- a common SMC target
    for a stop-run/sweep before the real move. Tagged on the SECOND
    (confirming) pivot, the earliest point the pair is actually known."""
    en = language == "en"
    events: list[SMCEvent] = []
    swing_highs = [(i, float(df["high"].iloc[i])) for i in range(len(df)) if df["swing_high"].iloc[i]]
    swing_lows = [(i, float(df["low"].iloc[i])) for i in range(len(df)) if df["swing_low"].iloc[i]]

    for (_, level1), (i2, level2) in zip(swing_highs, swing_highs[1:]):
        spread_ma = df["spread_ma"].iloc[i2]
        if pd.isna(spread_ma) or abs(level2 - level1) >= cfg.eq_threshold_mult * spread_ma:
            continue
        note = (
            f"Two swing highs near {level2:.2f} -- resting liquidity often swept before reversing"
            if en
            else f"Hai đỉnh swing gần nhau quanh {level2:.2f} -- thanh khoản chờ sẵn thường bị quét trước khi đảo chiều"
        )
        events.append(SMCEvent(EQUAL_HIGH, i2, _ts_at(df, i2), level2, note))

    for (_, level1), (i2, level2) in zip(swing_lows, swing_lows[1:]):
        spread_ma = df["spread_ma"].iloc[i2]
        if pd.isna(spread_ma) or abs(level2 - level1) >= cfg.eq_threshold_mult * spread_ma:
            continue
        note = (
            f"Two swing lows near {level2:.2f} -- resting liquidity often swept before reversing"
            if en
            else f"Hai đáy swing gần nhau quanh {level2:.2f} -- thanh khoản chờ sẵn thường bị quét trước khi đảo chiều"
        )
        events.append(SMCEvent(EQUAL_LOW, i2, _ts_at(df, i2), level2, note))

    return events


class _SweepPivot:
    """Tracks one swing pivot's state as later bars are scanned -- mirrors
    LuxAlgo's per-pivot `piv` UDT (this port only needs the two booleans that
    matter for "Wicks + Outbreaks & Retest" mode: whether a real breakout has
    already been confirmed, and whether the wick-sweep has already fired once
    for this pivot, since it should only ever fire once per pivot)."""

    __slots__ = ("index", "price", "broken", "wick_fired")

    def __init__(self, index: int, price: float):
        self.index = index
        self.price = price
        self.broken = False
        self.wick_fired = False


def _detect_liquidity_sweeps(df: pd.DataFrame, language: str = "vi") -> list[SMCEvent]:
    """Port of LuxAlgo's "Liquidity Sweeps" indicator, "Wicks + Outbreaks &
    Retest" mode (both sweep mechanisms active): for each swing high/low
    pivot, scans forward bar by bar tracking whether it has been broken (a
    later close crosses through it) and, once broken, whether that breakout
    later fails (mitigated -- pivot dropped) or holds on a retest (fires the
    continuation-confirmation sweep). Before a breakout, a wick beyond the
    pivot that closes back on the other side fires the reversal (stop-hunt)
    sweep once per pivot.

    Uses its own ``sweep_swing_high``/``sweep_swing_low`` pivot columns (see
    app.smc.indicators), independent of the Structure detector's swing
    columns -- LuxAlgo's Liquidity Sweeps and Structure are separate
    indicators with their own pivot lookbacks."""
    en = language == "en"
    events: list[SMCEvent] = []
    n = len(df)

    high_pivots: list[_SweepPivot] = []
    low_pivots: list[_SweepPivot] = []

    for i in range(n):
        close = df["close"].iloc[i]
        high = df["high"].iloc[i]
        low = df["low"].iloc[i]

        remaining_high_pivots: list[_SweepPivot] = []
        for piv in high_pivots:
            if not piv.broken:
                if close > piv.price:
                    piv.broken = True
                elif not piv.wick_fired and high > piv.price and close < piv.price:
                    note = (
                        f"Wick above {piv.price:.2f} closes back below -- liquidity swept, reversal"
                        if en
                        else f"Bấc chọc qua {piv.price:.2f} rồi đóng cửa trở lại dưới -- quét thanh khoản, đảo chiều"
                    )
                    events.append(SMCEvent(LIQUIDITY_SWEEP_BEAR, i, _ts_at(df, i), float(close), note))
                    piv.wick_fired = True
                remaining_high_pivots.append(piv)
            else:
                if close < piv.price:
                    continue  # mitigated -- breakout failed, drop the pivot
                if low < piv.price and close > piv.price:
                    note = (
                        f"Retest of broken {piv.price:.2f} holds -- continuation confirmed"
                        if en
                        else f"Test lại mức {piv.price:.2f} vừa vỡ vẫn giữ được -- xác nhận tiếp diễn"
                    )
                    events.append(SMCEvent(LIQUIDITY_SWEEP_BULL, i, _ts_at(df, i), float(close), note))
                    continue  # taken -- drop the pivot, it already confirmed
                remaining_high_pivots.append(piv)
        high_pivots = remaining_high_pivots

        remaining_low_pivots: list[_SweepPivot] = []
        for piv in low_pivots:
            if not piv.broken:
                if close < piv.price:
                    piv.broken = True
                elif not piv.wick_fired and low < piv.price and close > piv.price:
                    note = (
                        f"Wick below {piv.price:.2f} closes back above -- liquidity swept, reversal"
                        if en
                        else f"Bấc chọc xuống {piv.price:.2f} rồi đóng cửa trở lại trên -- quét thanh khoản, đảo chiều"
                    )
                    events.append(SMCEvent(LIQUIDITY_SWEEP_BULL, i, _ts_at(df, i), float(close), note))
                    piv.wick_fired = True
                remaining_low_pivots.append(piv)
            else:
                if close > piv.price:
                    continue  # mitigated -- breakdown failed, drop the pivot
                if high > piv.price and close < piv.price:
                    note = (
                        f"Retest of broken {piv.price:.2f} holds -- continuation confirmed"
                        if en
                        else f"Test lại mức {piv.price:.2f} vừa gãy vẫn giữ được -- xác nhận tiếp diễn"
                    )
                    events.append(SMCEvent(LIQUIDITY_SWEEP_BEAR, i, _ts_at(df, i), float(close), note))
                    continue  # taken -- drop the pivot, it already confirmed
                remaining_low_pivots.append(piv)
        low_pivots = remaining_low_pivots

        # A pivot only confirms cfg.sweep_lookback bars after its own bar (a
        # fractal needs bars on both sides) -- this is exactly when
        # sweep_swing_high/low go True for it, so start tracking it from here.
        if df["sweep_swing_high"].iloc[i]:
            high_pivots.append(_SweepPivot(i, float(high)))
        if df["sweep_swing_low"].iloc[i]:
            low_pivots.append(_SweepPivot(i, float(low)))

    events.sort(key=lambda e: e.index)
    return events


def detect_events(df: pd.DataFrame, cfg: SMCConfig = DEFAULT_CONFIG, language: str = "vi") -> list[SMCEvent]:
    structure_events = _detect_structure(df, language)
    ob_events = _detect_order_blocks(df, structure_events, cfg, language)
    major_structure_events = _detect_major_structure(df, language)
    major_ob_events = _detect_major_order_blocks(df, major_structure_events, cfg, language)
    fvg_events = _detect_fvg(df, cfg, language)
    eq_events = _detect_equal_highs_lows(df, cfg, language)
    sweep_events = _detect_liquidity_sweeps(df, language)
    events = (
        structure_events + ob_events + major_structure_events + major_ob_events
        + fvg_events + eq_events + sweep_events
    )
    events.sort(key=lambda e: e.index)
    return events
