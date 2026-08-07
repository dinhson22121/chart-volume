"""Bar-level explanation of which detectors matched/didn't, and why.

Computed on demand from already-stored candles — no persistence needed, this
is just re-running the pure detector logic for one requested bar. Dispatches
on the active strategy: Wyckoff and SMC each own their own tracer (see
app.wyckoff.events.trace_bar / app.smc.trace.trace_bar), since their
detectors read different feature columns and config.
"""

from __future__ import annotations

from datetime import datetime

import pandas as pd
from sqlmodel import Session, select

from app.models import Candle
from app.services import settings_service
from app.smc import trace as smc_trace
from app.smc.indicators import compute_features as smc_compute_features
from app.wyckoff import candles_to_dataframe
from app.wyckoff.events import DetectorTrace, trace_bar
from app.wyckoff.indicators import compute_features

# Strategies with a decision tracer of their own. app.api.analysis rejects
# anything not listed here before it ever reaches this module.
SUPPORTED_STRATEGIES = frozenset({"wyckoff", "smc"})


def get_bar_trace(
    session: Session, ticker: str, timeframe: str, bar_ts: datetime, strategy: str = "wyckoff"
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

    language = settings_service.get_language(session)
    index = int(matches[0])

    if strategy == "smc":
        cfg = settings_service.get_smc_config(session)
        return smc_trace.trace_bar(smc_compute_features(df, cfg), index, cfg, language)

    feat = compute_features(df)
    return trace_bar(feat.iloc[index], settings_service.get_wyckoff_config(session), language)
