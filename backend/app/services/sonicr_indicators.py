"""Per-bar Sonic R indicator series (Dragon EMA, T3 fast/slow), for chart overlay.

Computed on demand from already-stored candles -- no persistence needed, this
is just re-running the pure feature computation over the full candle series.
"""

from __future__ import annotations

import pandas as pd
from sqlmodel import Session, select

from app.models import Candle
from app.services import settings_service
from app.sonicr.indicators import compute_features
from app.wyckoff import candles_to_dataframe

_SERIES_COLUMNS = ("dragon", "t3_fast", "t3_slow")


def _series(df: pd.DataFrame, column: str) -> list[dict]:
    return [
        {"ts": row["time"].isoformat(), "value": float(row[column])}
        for _, row in df.iterrows()
        if not pd.isna(row[column])
    ]


def get_indicator_series(session: Session, ticker: str, timeframe: str) -> dict[str, list[dict]]:
    ticker = ticker.upper()
    candles = session.exec(
        select(Candle).where(Candle.ticker == ticker, Candle.timeframe == timeframe).order_by(Candle.bucket_start)
    ).all()
    if not candles:
        return {col: [] for col in _SERIES_COLUMNS}

    df = candles_to_dataframe(candles)
    cfg = settings_service.get_sonicr_config(session)
    feat = compute_features(df, cfg)
    return {col: _series(feat, col) for col in _SERIES_COLUMNS}
