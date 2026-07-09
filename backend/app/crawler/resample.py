"""Aggregate hourly bars into half-session candles.

VN trading day = morning (09:00-11:30) + afternoon (13:00-15:00). Hourly bars
arrive stamped at 09:00/10:00/11:00 (morning) and 13:00/14:00 (afternoon, plus
an occasional 15:00 ATC bar). We split on hour < 12 to assign each bar to a
half-session, then OHLCV-aggregate: open=first, high=max, low=min, close=last,
volume=sum. Bucket start is normalised to 09:00 (morning) / 13:00 (afternoon).
"""

from __future__ import annotations

import pandas as pd

from app.models import SessionPart

_MORNING_HOUR = 9
_AFTERNOON_HOUR = 13


def resample_half_session(df: pd.DataFrame) -> pd.DataFrame:
    """Return columns: bucket_start, session_part, open, high, low, close, volume.

    Empty input yields an empty frame with the expected columns.
    """
    cols = ["bucket_start", "session_part", "open", "high", "low", "close", "volume"]
    if df is None or df.empty:
        return pd.DataFrame(columns=cols)

    work = df.copy()
    work["time"] = pd.to_datetime(work["time"])
    work = work.sort_values("time")
    work["date"] = work["time"].dt.normalize()
    work["session_part"] = work["time"].dt.hour.map(
        lambda h: SessionPart.MORNING if h < 12 else SessionPart.AFTERNOON
    )

    grouped = work.groupby(["date", "session_part"], sort=True).agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        volume=("volume", "sum"),
    ).reset_index()

    hour_offset = grouped["session_part"].map(
        {SessionPart.MORNING: _MORNING_HOUR, SessionPart.AFTERNOON: _AFTERNOON_HOUR}
    )
    grouped["bucket_start"] = grouped["date"] + pd.to_timedelta(hour_offset, unit="h")

    return grouped[cols].sort_values("bucket_start").reset_index(drop=True)
