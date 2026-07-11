"""Detector-layer tests for SMC (Smart Money Concept) events: market
structure (BOS/CHoCH), Order Blocks, and Fair Value Gaps."""

import pandas as pd

from app.smc.config import SMCConfig
from app.smc.events import (
    BEARISH_FVG,
    BEARISH_OB,
    BOS_BEAR,
    BOS_BULL,
    BULLISH_FVG,
    BULLISH_OB,
    CHOCH_BEAR,
    CHOCH_BULL,
    detect_events,
)
from app.smc.indicators import compute_features


def _df(opens, highs, lows, closes):
    n = len(closes)
    return pd.DataFrame(
        {
            "time": pd.date_range("2025-01-01", periods=n, freq="D"),
            "open": opens,
            "high": highs,
            "low": lows,
            "close": closes,
            "volume": [1000.0] * n,
        }
    )


def _zigzag(values):
    """Builds OHLC bars from a list of close waypoints -- open slightly below
    close (an up candle) so every bar defaults to bullish unless overridden;
    high/low give each bar a small, consistent spread."""
    opens = [v - 0.3 for v in values]
    highs = [v + 0.5 for v in values]
    lows = [v - 0.5 for v in values]
    return opens, highs, lows, list(values)


def _by_type(events, event_type):
    return [e for e in events if e.type == event_type]


CFG = SMCConfig(swing_lookback=2, ob_lookback_bars=10, fvg_min_gap_mult=0.3)


def test_first_break_of_a_swing_high_is_choch_bull_not_bos():
    # down to a swing low, up to a swing high, down to a higher low, then
    # break above the swing high -- the FIRST break ever, so structure was
    # undefined -> CHoCH_Bull, not BOS_Bull.
    values = [110, 108, 106, 104, 102, 100, 102, 104, 106, 108, 110, 112, 110, 108, 106, 104, 103, 105, 108, 111, 113]
    feat = compute_features(_df(*_zigzag(values)), CFG)
    events = detect_events(feat, CFG)

    choch = _by_type(events, CHOCH_BULL)
    assert len(choch) == 1
    assert not _by_type(events, BOS_BULL)


def test_second_break_after_choch_is_bos_bull():
    # Same as above, then a pullback to a new (higher) swing low, then a
    # break above a NEW swing high -- structure is already bullish, so this
    # second break is BOS (continuation), not another CHoCH.
    values = [
        110, 108, 106, 104, 102, 100,  # swing low ~100
        102, 104, 106, 108, 110, 112,  # swing high ~112
        110, 108, 106, 104, 103,  # higher swing low ~103
        105, 108, 111, 113, 115, 117, 119,  # CHoCH_Bull breaking 112, new swing high ~119
        117, 115, 113, 112, 111,  # pullback to a new swing low ~111
        113, 116, 119, 122, 125,  # break above 119 -- BOS_Bull
    ]
    feat = compute_features(_df(*_zigzag(values)), CFG)
    events = detect_events(feat, CFG)

    assert len(_by_type(events, CHOCH_BULL)) == 1
    bos = _by_type(events, BOS_BULL)
    assert len(bos) == 1
    # Fires on the first close that clears the new swing high (~119.5), not
    # necessarily the last bar of the crafted breakout run.
    assert bos[0].price == 122.0


def test_first_break_of_a_swing_low_is_choch_bear():
    values = [
        90, 92, 94, 96, 98, 100,  # up to swing high ~100
        98, 96, 94, 92, 90, 88,  # down to swing low ~88
        90, 92, 94, 96, 97,  # up to a lower high ~97
        95, 92, 89, 87, 85, 83, 81,  # down, breaking below 88 -> CHoCH_Bear
    ]
    feat = compute_features(_df(*_zigzag(values)), CFG)
    events = detect_events(feat, CFG)

    choch = _by_type(events, CHOCH_BEAR)
    assert len(choch) == 1
    assert not _by_type(events, BOS_BEAR)


def test_bullish_order_block_anchors_to_last_down_candle_before_bos():
    values = [
        110, 108, 106, 104, 102, 100,
        102, 104, 106, 108, 110, 112,
        110, 108, 106, 104, 103,
        105, 108, 111, 113, 115, 117, 119,  # CHoCH_Bull, new swing high ~119
        117, 115, 113, 112, 111,  # new swing low ~111
    ]
    opens, highs, lows, closes = _zigzag(values)
    # Explicit down candle right before the impulse that breaks 119.
    opens.append(113.0); closes.append(112.0); highs.append(113.5); lows.append(111.5)  # noqa: E702
    for v in (116, 119, 122, 125):
        opens.append(v - 0.3); closes.append(v); highs.append(v + 0.5); lows.append(v - 0.5)  # noqa: E702

    feat = compute_features(_df(opens, highs, lows, closes), CFG)
    events = detect_events(feat, CFG)

    bos = _by_type(events, BOS_BULL)
    assert len(bos) == 1
    ob = _by_type(events, BULLISH_OB)
    assert len(ob) == 1
    # The OB candle is the down candle we inserted, strictly before the BOS bar.
    down_candle_index = len(values)
    assert ob[0].index == down_candle_index
    assert ob[0].index < bos[0].index


def test_bearish_order_block_anchors_to_last_up_candle_before_bos():
    values = [
        90, 92, 94, 96, 98, 100,
        98, 96, 94, 92, 90, 88,
        90, 92, 94, 96, 97,
        95, 92, 89, 87, 85, 83, 81,  # CHoCH_Bear, new swing low ~81
        83, 85, 87, 88, 89,  # new swing high ~89
    ]
    opens, highs, lows, closes = _zigzag(values)
    # Explicit UP candle right before the impulse that breaks below 81.
    opens.append(87.0); closes.append(88.0); highs.append(88.5); lows.append(86.5)  # noqa: E702
    for v in (84, 81, 78, 75):
        opens.append(v + 0.3); closes.append(v); highs.append(v + 0.5); lows.append(v - 0.5)  # noqa: E702

    feat = compute_features(_df(opens, highs, lows, closes), CFG)
    events = detect_events(feat, CFG)

    bos = _by_type(events, BOS_BEAR)
    assert len(bos) == 1
    ob = _by_type(events, BEARISH_OB)
    assert len(ob) == 1
    up_candle_index = len(values)
    assert ob[0].index == up_candle_index
    assert ob[0].index < bos[0].index


def test_detects_bullish_fvg_when_gap_exceeds_threshold():
    n_base = 10
    opens = [100.0] * n_base
    closes = [100.0] * n_base
    highs = [100.5] * n_base
    lows = [99.5] * n_base
    # candle i-1: normal; candle i: big impulsive up bar; candle i+1: gaps
    # clear above candle i-1's high.
    opens += [100.0, 100.2, 104.0]
    closes += [100.2, 104.0, 105.0]
    highs += [100.5, 104.5, 105.5]
    lows += [99.5, 100.0, 104.8]
    opens += [105.0] * 3; closes += [105.0] * 3; highs += [105.5] * 3; lows += [104.5] * 3  # noqa: E702

    feat = compute_features(_df(opens, highs, lows, closes), CFG)
    events = detect_events(feat, CFG)

    fvg = _by_type(events, BULLISH_FVG)
    assert len(fvg) == 1
    assert fvg[0].index == 11  # the impulsive middle candle


def test_fvg_filtered_out_when_below_min_gap_threshold():
    n_base = 10
    opens = [100.0] * n_base
    closes = [100.0] * n_base
    highs = [100.5] * n_base
    lows = [99.5] * n_base
    opens += [100.0, 100.2, 104.0]
    closes += [100.2, 104.0, 105.0]
    highs += [100.5, 104.5, 105.5]
    lows += [99.5, 100.0, 104.8]
    opens += [105.0] * 3; closes += [105.0] * 3; highs += [105.5] * 3; lows += [104.5] * 3  # noqa: E702

    feat = compute_features(_df(opens, highs, lows, closes), SMCConfig(fvg_min_gap_mult=50.0))
    events = detect_events(feat, SMCConfig(fvg_min_gap_mult=50.0))

    assert not _by_type(events, BULLISH_FVG)
    assert not _by_type(events, BEARISH_FVG)


def test_detects_bearish_fvg_when_gap_exceeds_threshold():
    n_base = 10
    opens = [100.0] * n_base
    closes = [100.0] * n_base
    highs = [100.5] * n_base
    lows = [99.5] * n_base
    # candle i-1: normal; candle i: big impulsive down bar; candle i+1: gaps
    # clear below candle i-1's low.
    opens += [100.0, 99.8, 96.0]
    closes += [99.8, 96.0, 95.0]
    highs += [100.5, 100.0, 95.2]
    lows += [99.5, 95.5, 94.5]
    opens += [95.0] * 3; closes += [95.0] * 3; highs += [95.5] * 3; lows += [94.5] * 3  # noqa: E702

    feat = compute_features(_df(opens, highs, lows, closes), CFG)
    events = detect_events(feat, CFG)

    fvg = _by_type(events, BEARISH_FVG)
    assert len(fvg) == 1
    assert fvg[0].index == 11


def test_language_switches_note_text():
    values = [110, 108, 106, 104, 102, 100, 102, 104, 106, 108, 110, 112, 110, 108, 106, 104, 103, 105, 108, 111, 113]
    feat = compute_features(_df(*_zigzag(values)), CFG)

    events_vi = detect_events(feat, CFG, language="vi")
    events_en = detect_events(feat, CFG, language="en")

    choch_vi = _by_type(events_vi, CHOCH_BULL)[0]
    choch_en = _by_type(events_en, CHOCH_BULL)[0]
    assert "đổi chiều" in choch_vi.note
    assert "reversal" in choch_en.note
