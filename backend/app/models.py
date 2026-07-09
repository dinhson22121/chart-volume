"""SQLModel tables: Symbol, Candle, Analysis.

Timeframes are kept as plain strings (see ``Timeframe``) rather than a DB enum
to keep SQLite migrations trivial. Uniqueness constraints make ingest/analysis
idempotent so re-running a crawl never duplicates rows.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel


class Timeframe:
    DAILY = "daily"
    HALF_SESSION = "half_session"


class SessionPart:
    MORNING = "morning"
    AFTERNOON = "afternoon"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Symbol(SQLModel, table=True):
    ticker: str = Field(primary_key=True)
    name: str = ""
    is_vn30: bool = False
    is_watchlist: bool = False
    added_at: datetime = Field(default_factory=_utcnow)


class Candle(SQLModel, table=True):
    __table_args__ = (
        UniqueConstraint("ticker", "timeframe", "bucket_start", name="uq_candle"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    ticker: str = Field(index=True)
    timeframe: str = Field(index=True)
    session_part: Optional[str] = None  # only set for half_session
    bucket_start: datetime = Field(index=True)
    open: float
    high: float
    low: float
    close: float
    volume: float


class Analysis(SQLModel, table=True):
    __table_args__ = (
        UniqueConstraint("ticker", "timeframe", "as_of", name="uq_analysis"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    ticker: str = Field(index=True)
    timeframe: str = Field(index=True)
    as_of: datetime = Field(index=True)  # bucket_start of the latest analysed candle
    phase: str
    confidence: float
    signals_json: str  # JSON list of detected Wyckoff events
    levels_json: str  # JSON of support/resistance levels
    narrative: Optional[str] = None
    advice: Optional[str] = None
    created_at: datetime = Field(default_factory=_utcnow)
