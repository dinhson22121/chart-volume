"""Bar-level explanation of which SMC detectors matched/didn't, and why --
the SMC counterpart to app.wyckoff.events.trace_bar.

Unlike Wyckoff's, this can't be a pure single-row function: every SMC
detector is either stateful across the whole series (BOS/CHoCH carry a
structure trend and consume swing levels; Liquidity Sweeps run a per-pivot
forward scan) or spans several bars (an Order Block is anchored relative to a
BOS elsewhere, an FVG needs its neighbours, Equal Highs/Lows need the
previous pivot). Re-deriving those conditions as per-bar arithmetic would be
a second implementation that could silently drift from the real detectors.

So ``matched`` is never re-derived here: it comes from running the REAL
``detect_events`` over the frame and asking whether that type fired at this
bar. The per-check breakdown is explanatory detail layered on top, read from
the same feature columns and config the detectors themselves use (and, for
BOS/CHoCH, from the detector's own state machine via
``events._walk_structure``).

Only detectors worth reading at this bar are returned: everything that
matched, plus the non-matching ones whose conditions are genuinely
evaluable at this bar on their own. A type whose "why not" answer would only
ever be "because of something happening at some other bar" (Liquidity
Sweeps' forward scan, an Order Block with no breakout to anchor to) is left
out rather than padded with a vacuous all-false row.
"""

from __future__ import annotations

import pandas as pd

from app.smc.config import DEFAULT_CONFIG, SMCConfig
from app.smc.events import (
    BEARISH_FVG,
    BEARISH_OB,
    BOS_BEAR,
    BOS_BULL,
    BULLISH_FVG,
    BULLISH_OB,
    CHOCH_BEAR,
    CHOCH_BULL,
    EQUAL_HIGH,
    EQUAL_LOW,
    LIQUIDITY_SWEEP_BEAR,
    LIQUIDITY_SWEEP_BULL,
    SWING_BEARISH_OB,
    SWING_BOS_BEAR,
    SWING_BOS_BULL,
    SWING_BULLISH_OB,
    SWING_CHOCH_BEAR,
    SWING_CHOCH_BULL,
    _Bars,
    _is_high_volatility_bar,
    _StructureStep,
    _walk_structure,
    detect_events,
)

# The Check/DetectorTrace pair is the API's own trace contract (see
# app.api.analysis.get_trace) -- reused rather than re-declared so both
# strategies serialize identically. Same direction of dependency app.smc
# already has on app.wyckoff for AnalysisResult/Levels.
from app.wyckoff.events import Check, DetectorTrace


def _lbl(vi: str, en: str, language: str) -> str:
    return en if language == "en" else vi


def _fmt_level(level: tuple[int, float] | None, df: pd.DataFrame, language: str) -> str:
    if level is None:
        return _lbl("chưa có mức nào đang hiệu lực", "no level in play yet", language)
    idx, price = level
    return f"{price:.2f} ({df['time'].iloc[idx]:%Y-%m-%d})"


def _trend_text(trend: str | None, language: str) -> str:
    if trend == "bullish":
        return _lbl("tăng", "bullish", language)
    if trend == "bearish":
        return _lbl("giảm", "bearish", language)
    return _lbl("chưa xác định", "undefined", language)


def _structure_traces(
    df: pd.DataFrame,
    step: _StructureStep,
    close: float,
    bos_bull: str,
    bos_bear: str,
    choch_bull: str,
    choch_bear: str,
    language: str,
) -> list[DetectorTrace]:
    """The 4 structure types for one tier. Always evaluable: every bar is
    tested against whatever swing level was in play at that moment."""
    lang = language
    traces: list[DetectorTrace] = []

    broke_high = step.broke_high is not None
    high_level = step.broke_high or step.active_high
    has_high = Check(
        _lbl("Có đỉnh swing đang hiệu lực", "A swing high is in play", lang),
        high_level is not None,
        _fmt_level(high_level, df, lang),
    )
    cleared_high = Check(
        _lbl("Đóng cửa vượt đỉnh đó", "Closes above that swing high", lang),
        broke_high,
        f"close {close:.2f} {'>' if broke_high else '<='} "
        + (f"{high_level[1]:.2f}" if high_level else "-"),
    )
    trend_text = _trend_text(step.trend_before, lang)
    for event_type, wants_choch in ((choch_bull, True), (bos_bull, False)):
        trend_ok = (step.trend_before != "bullish") if wants_choch else (step.trend_before == "bullish")
        trend_check = Check(
            _lbl(
                "Xu hướng trước đó chưa tăng (đảo chiều)" if wants_choch else "Xu hướng trước đó đã tăng (tiếp diễn)",
                "Prior structure not yet bullish (reversal)" if wants_choch else "Prior structure already bullish (continuation)",
                lang,
            ),
            trend_ok,
            trend_text,
        )
        matched = broke_high and step.high_is_choch == wants_choch
        traces.append(DetectorTrace(event_type, matched, [has_high, cleared_high, trend_check]))

    broke_low = step.broke_low is not None
    low_level = step.broke_low or step.active_low
    has_low = Check(
        _lbl("Có đáy swing đang hiệu lực", "A swing low is in play", lang),
        low_level is not None,
        _fmt_level(low_level, df, lang),
    )
    cleared_low = Check(
        _lbl("Đóng cửa thủng đáy đó", "Closes below that swing low", lang),
        broke_low,
        f"close {close:.2f} {'<' if broke_low else '>='} " + (f"{low_level[1]:.2f}" if low_level else "-"),
    )
    for event_type, wants_choch in ((choch_bear, True), (bos_bear, False)):
        trend_ok = (step.trend_before != "bearish") if wants_choch else (step.trend_before == "bearish")
        trend_check = Check(
            _lbl(
                "Xu hướng trước đó chưa giảm (đảo chiều)" if wants_choch else "Xu hướng trước đó đã giảm (tiếp diễn)",
                "Prior structure not yet bearish (reversal)" if wants_choch else "Prior structure already bearish (continuation)",
                lang,
            ),
            trend_ok,
            trend_text,
        )
        matched = broke_low and step.low_is_choch == wants_choch
        traces.append(DetectorTrace(event_type, matched, [has_low, cleared_low, trend_check]))

    return traces


def _order_block_traces(
    bars: _Bars, index: int, cfg: SMCConfig, fired: dict[str, object], language: str, types: tuple[str, str]
) -> list[DetectorTrace]:
    """Order Blocks for one tier. The anchor search runs backwards from a BOS
    elsewhere in the series, so "was it picked" is ground truth only -- the
    two conditions shown here are the genuinely bar-local ones the search
    itself applies to each candidate it walks past."""
    bullish_ob, bearish_ob = types
    lang = language
    is_down = bool(bars.close[index] < bars.open[index])
    volatile = _is_high_volatility_bar(bars, index, cfg)
    calm = Check(
        _lbl("Biên độ nến không bất thường", "Bar range isn't an outlier", lang),
        not volatile,
        f"spread {bars.high[index] - bars.low[index]:.2f} vs {cfg.ob_volatility_mult:.1f}x spread_ma",
    )

    traces: list[DetectorTrace] = []
    for event_type, wants_down in ((bullish_ob, True), (bearish_ob, False)):
        direction = Check(
            _lbl(
                "Là nến giảm" if wants_down else "Là nến tăng",
                "Is a down candle" if wants_down else "Is an up candle",
                lang,
            ),
            is_down == wants_down,
            f"open {bars.open[index]:.2f} -> close {bars.close[index]:.2f}",
        )
        matched = event_type in fired
        anchored = Check(
            _lbl("Được chọn làm vùng OB cho một cú phá vỡ sau đó", "Picked as the OB zone for a later break", lang),
            matched,
            _lbl("có", "yes", lang) if matched else _lbl("không", "no", lang),
        )
        traces.append(DetectorTrace(event_type, matched, [direction, calm, anchored]))
    return traces


def _fvg_traces(df: pd.DataFrame, index: int, cfg: SMCConfig, fired: dict, language: str) -> list[DetectorTrace]:
    """3-candle Fair Value Gap -- fully evaluable from this bar's neighbours."""
    lang = language
    n = len(df)
    if index < 1 or index > n - 2:
        return []
    spread_ma = df["spread_ma"].iloc[index]
    if pd.isna(spread_ma):
        return []

    threshold = cfg.fvg_min_gap_mult * spread_ma
    prev_high = float(df["high"].iloc[index - 1])
    prev_low = float(df["low"].iloc[index - 1])
    next_high = float(df["high"].iloc[index + 1])
    next_low = float(df["low"].iloc[index + 1])

    bull_gap = next_low - prev_high
    bear_gap = prev_low - next_high
    traces: list[DetectorTrace] = []
    for event_type, gap, desc in (
        (BULLISH_FVG, bull_gap, f"{prev_high:.2f} -> {next_low:.2f}"),
        (BEARISH_FVG, bear_gap, f"{next_high:.2f} -> {prev_low:.2f}"),
    ):
        exists = Check(
            _lbl("Có khoảng trống giữa nến trước và nến sau", "Gap between the neighbouring candles", lang),
            gap > 0,
            desc,
        )
        big_enough = Check(
            _lbl("Khoảng trống đủ lớn", "Gap is wide enough", lang),
            gap >= threshold,
            f"{gap:.2f} {'>=' if gap >= threshold else '<'} {threshold:.2f}",
        )
        traces.append(DetectorTrace(event_type, event_type in fired, [exists, big_enough]))
    return traces


def _equal_level_traces(df: pd.DataFrame, index: int, cfg: SMCConfig, fired: dict, language: str) -> list[DetectorTrace]:
    """Equal Highs/Lows -- only meaningful on a confirmed swing pivot, since
    the detector tags the SECOND pivot of a near-equal pair."""
    lang = language
    spread_ma = df["spread_ma"].iloc[index]
    if pd.isna(spread_ma):
        return []
    threshold = cfg.eq_threshold_mult * spread_ma

    traces: list[DetectorTrace] = []
    for event_type, pivot_col, price_col in (
        (EQUAL_HIGH, "swing_high", "high"),
        (EQUAL_LOW, "swing_low", "low"),
    ):
        if not bool(df[pivot_col].iloc[index]):
            continue  # not a pivot -- this detector has nothing to say here
        prior = [i for i in range(index) if bool(df[pivot_col].iloc[i])]
        is_pivot = Check(
            _lbl("Là đỉnh/đáy swing đã xác nhận", "Is a confirmed swing pivot", lang),
            True,
            f"{float(df[price_col].iloc[index]):.2f}",
        )
        if not prior:
            traces.append(DetectorTrace(event_type, event_type in fired, [is_pivot, Check(
                _lbl("Có đỉnh/đáy swing trước đó để so", "Has an earlier pivot to compare with", lang),
                False,
                _lbl("chưa có", "none yet", lang),
            )]))
            continue
        level_now = float(df[price_col].iloc[index])
        level_prev = float(df[price_col].iloc[prior[-1]])
        gap = abs(level_now - level_prev)
        near = Check(
            _lbl("Ngang bằng đỉnh/đáy swing trước", "Level with the previous pivot", lang),
            gap < threshold,
            f"|{level_now:.2f} - {level_prev:.2f}| = {gap:.2f} {'<' if gap < threshold else '>='} {threshold:.2f}",
        )
        traces.append(DetectorTrace(event_type, event_type in fired, [is_pivot, near]))
    return traces


def trace_bar(
    df: pd.DataFrame, index: int, cfg: SMCConfig = DEFAULT_CONFIG, language: str = "vi"
) -> list[DetectorTrace]:
    """``df`` must already carry app.smc.indicators.compute_features' columns."""
    events = detect_events(df, cfg, language)
    fired = {e.type: e for e in events if e.index == index}
    close = float(df["close"].iloc[index])
    bars = _Bars.of(df)

    traces: list[DetectorTrace] = []
    for swing_high_col, swing_low_col, structure_types, ob_types in (
        ("swing_high", "swing_low", (BOS_BULL, BOS_BEAR, CHOCH_BULL, CHOCH_BEAR), (BULLISH_OB, BEARISH_OB)),
        (
            "major_swing_high", "major_swing_low",
            (SWING_BOS_BULL, SWING_BOS_BEAR, SWING_CHOCH_BULL, SWING_CHOCH_BEAR),
            (SWING_BULLISH_OB, SWING_BEARISH_OB),
        ),
    ):
        step = next((s for s in _walk_structure(df, swing_high_col, swing_low_col) if s.index == index), None)
        if step is not None:
            traces.extend(_structure_traces(df, step, close, *structure_types, language))
        traces.extend(_order_block_traces(bars, index, cfg, fired, language, ob_types))

    traces.extend(_fvg_traces(df, index, cfg, fired, language))
    traces.extend(_equal_level_traces(df, index, cfg, fired, language))

    # Liquidity Sweeps run a per-pivot forward scan whose "why not" is never a
    # property of this bar alone -- reported only when one actually fired.
    for sweep_type in (LIQUIDITY_SWEEP_BULL, LIQUIDITY_SWEEP_BEAR):
        event = fired.get(sweep_type)
        if event is not None:
            traces.append(DetectorTrace(sweep_type, True, [Check(
                _lbl("Quét thanh khoản", "Liquidity sweep", language), True, event.note,
            )]))

    # Everything that fired stays; a non-matching row is only worth showing
    # when its checks actually explain something (see module docstring).
    return [t for t in traces if t.matched or any(c.passed for c in t.checks)]
