"""Bar-level explanation of which Wyckoff detectors matched/didn't, and why.

Computed on demand from already-stored candles — no persistence needed, this
is just re-running the pure detector logic for one requested bar.
"""

from __future__ import annotations

from datetime import datetime

import pandas as pd
from sqlmodel import Session, select

from app.models import Candle
from app.services import settings_service
from app.wyckoff import candles_to_dataframe
from app.wyckoff.events import DetectorTrace, trace_bar
from app.wyckoff.indicators import compute_features


def get_bar_trace(
    session: Session, ticker: str, timeframe: str, bar_ts: datetime
) -> list[DetectorTrace] | None:
    ticker = ticker.upper()
    candles = session.exec(
        select(Candle)
        .where(Candle.ticker == ticker, Candle.timeframe == timeframe)
        .order_by(Candle.bucket_start)
    ).all()
    if not candles:
        return None

    df = candles_to_dataframe(candles)
    target = pd.Timestamp(bar_ts)
    matches = df.index[df["time"] == target]
    if len(matches) == 0:
        return None

    feat = compute_features(df)
    cfg = settings_service.get_wyckoff_config(session)
    row = feat.iloc[matches[0]]
    return trace_bar(row, cfg)
