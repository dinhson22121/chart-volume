import pandas as pd

from app.crawler.resample import resample_half_session, resample_weekly
from app.models import SessionPart


def _hourly_row(ts, o, h, low, c, v):
    return {"time": ts, "open": o, "high": h, "low": low, "close": c, "volume": v}


def _daily_row(ts, o, h, low, c, v):
    return {"bucket_start": ts, "open": o, "high": h, "low": low, "close": c, "volume": v}


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


def test_resample_weekly_aggregates_monday_through_friday():
    # 2025-06-30 (Mon) through 2025-07-04 (Fri) is one calendar week; the next
    # Monday 2025-07-07 starts a new one.
    df = pd.DataFrame([
        _daily_row("2025-06-30", 100.0, 101.0, 99.0, 100.5, 1000),
        _daily_row("2025-07-01", 100.5, 103.0, 100.0, 102.0, 1500),
        _daily_row("2025-07-02", 102.0, 102.5, 98.0, 99.0, 2000),
        _daily_row("2025-07-03", 99.0, 100.0, 97.0, 99.5, 1200),
        _daily_row("2025-07-04", 99.5, 101.5, 99.0, 101.0, 800),
        _daily_row("2025-07-07", 101.0, 105.0, 100.5, 104.0, 900),
    ])

    out = resample_weekly(df)

    assert len(out) == 2
    week1, week2 = out.iloc[0], out.iloc[1]

    assert week1["bucket_start"] == pd.Timestamp("2025-06-30")
    assert week1["open"] == 100.0  # Monday's open
    assert week1["high"] == 103.0  # max across the 5 days
    assert week1["low"] == 97.0  # min across the 5 days
    assert week1["close"] == 101.0  # Friday's close
    assert week1["volume"] == 6500  # sum

    # Second week has only Monday so far -- still one valid, in-progress bar.
    assert week2["bucket_start"] == pd.Timestamp("2025-07-07")
    assert week2["open"] == 101.0
    assert week2["close"] == 104.0
    assert week2["volume"] == 900


def test_resample_weekly_handles_unsorted_input():
    df = pd.DataFrame([
        _daily_row("2025-07-01", 100.5, 103.0, 100.0, 102.0, 1500),
        _daily_row("2025-06-30", 100.0, 101.0, 99.0, 100.5, 1000),
    ])
    out = resample_weekly(df)
    week1 = out.iloc[0]
    assert week1["open"] == 100.0  # Monday (earliest) wins as open
    assert week1["close"] == 102.0  # Tuesday (latest) wins as close


def test_resample_weekly_empty_input():
    out = resample_weekly(pd.DataFrame(columns=["bucket_start", "open", "high", "low", "close", "volume"]))
    assert out.empty
    assert list(out.columns) == ["bucket_start", "open", "high", "low", "close", "volume"]
