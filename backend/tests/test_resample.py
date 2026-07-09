import pandas as pd

from app.crawler.resample import resample_half_session
from app.models import SessionPart


def _hourly_row(ts, o, h, low, c, v):
    return {"time": ts, "open": o, "high": h, "low": low, "close": c, "volume": v}


def test_resample_splits_morning_and_afternoon():
    df = pd.DataFrame([
        _hourly_row("2025-07-03 09:00:00", 100.0, 101.0, 99.5, 100.5, 1000),
        _hourly_row("2025-07-03 10:00:00", 100.5, 102.0, 100.0, 101.5, 2000),
        _hourly_row("2025-07-03 11:00:00", 101.5, 101.8, 101.0, 101.2, 500),
        _hourly_row("2025-07-03 13:00:00", 101.2, 101.4, 100.0, 100.3, 1500),
        _hourly_row("2025-07-03 14:00:00", 100.3, 100.9, 99.0, 99.5, 1200),
    ])

    out = resample_half_session(df)

    assert len(out) == 2
    morning = out[out["session_part"] == SessionPart.MORNING].iloc[0]
    afternoon = out[out["session_part"] == SessionPart.AFTERNOON].iloc[0]

    # Morning aggregation across 09/10/11 bars.
    assert morning["open"] == 100.0  # first
    assert morning["high"] == 102.0  # max
    assert morning["low"] == 99.5  # min
    assert morning["close"] == 101.2  # last
    assert morning["volume"] == 3500  # sum
    assert morning["bucket_start"] == pd.Timestamp("2025-07-03 09:00:00")

    # Afternoon aggregation across 13/14 bars.
    assert afternoon["open"] == 101.2
    assert afternoon["high"] == 101.4
    assert afternoon["low"] == 99.0
    assert afternoon["close"] == 99.5
    assert afternoon["volume"] == 2700
    assert afternoon["bucket_start"] == pd.Timestamp("2025-07-03 13:00:00")


def test_resample_handles_unsorted_input():
    df = pd.DataFrame([
        _hourly_row("2025-07-03 10:00:00", 100.5, 102.0, 100.0, 101.5, 2000),
        _hourly_row("2025-07-03 09:00:00", 100.0, 101.0, 99.5, 100.5, 1000),
    ])
    out = resample_half_session(df)
    morning = out.iloc[0]
    assert morning["open"] == 100.0  # earliest bar wins as open
    assert morning["close"] == 101.5  # latest bar wins as close


def test_resample_empty_input():
    out = resample_half_session(pd.DataFrame(columns=["time", "open", "high", "low", "close", "volume"]))
    assert out.empty
    assert list(out.columns) == ["bucket_start", "session_part", "open", "high", "low", "close", "volume"]
