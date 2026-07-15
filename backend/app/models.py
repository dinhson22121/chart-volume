"""SQLModel tables: Symbol, Candle, Analysis.

Timeframes are kept as plain strings (see ``Timeframe``) rather than a DB enum
to keep SQLite migrations trivial. Uniqueness constraints make ingest/analysis
idempotent so re-running a crawl never duplicates rows.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import Index, UniqueConstraint
from sqlmodel import Field, SQLModel


class Timeframe:
    DAILY = "daily"
    HALF_SESSION = "half_session"  # stocks only (VN market session halves)
    HOUR_1 = "1h"  # crypto only
    HOUR_4 = "4h"  # crypto only


class SessionPart:
    MORNING = "morning"
    AFTERNOON = "afternoon"


class AssetClass:
    STOCK = "stock"
    CRYPTO = "crypto"


class CryptoExchange:
    BINANCE = "binance"
    KUCOIN = "kucoin"
    MEXC = "mexc"
    GECKOTERMINAL = "geckoterminal"
    ALL = (BINANCE, KUCOIN, MEXC, GECKOTERMINAL)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Symbol(SQLModel, table=True):
    # For stocks/manually-added crypto, ticker IS the trading symbol. For
    # crypto promoted from the screener, ticker is the CoinGecko coin_id
    # instead (e.g. "pepesol") -- guaranteed unique, unlike the human ticker
    # symbol which many unrelated coins share (see display_symbol below).
    ticker: str = Field(primary_key=True)
    name: str = ""
    # The actual trading symbol (e.g. "PEPE") -- used for display and for
    # looking the coin up on an exchange. Equal to `ticker` for stocks/manual
    # adds; distinct from it when `ticker` is a coin_id.
    display_symbol: str = ""
    asset_class: str = Field(default=AssetClass.STOCK, index=True)
    is_vn30: bool = False
    is_watchlist: bool = False
    # Top-100-by-market-cap membership (crypto analog of is_vn30) -- seeded
    # from CoinGecko, refreshed manually or by the top100_refresh cron job.
    # rank is 1-based display order; NULL when the coin is not in the top 100.
    is_top100: bool = False
    top100_rank: Optional[int] = None
    added_at: datetime = Field(default_factory=_utcnow)
    # Crypto only, all optional -- populated at promote time or lazily on
    # first ingest. coingecko_id links back to the screener candidate for the
    # CoinGecko-platforms lookup; dex_network/dex_pool_address cache a
    # resolved GeckoTerminal pool so we never re-resolve on every refresh.
    coingecko_id: Optional[str] = None
    dex_network: Optional[str] = None
    dex_pool_address: Optional[str] = None


class Candle(SQLModel, table=True):
    # The unique constraint below is backed by a composite index on exactly
    # (ticker, timeframe, bucket_start) -- the same shape as every real query
    # here (WHERE ticker=? AND timeframe=? ORDER BY bucket_start). Separate
    # single-column indexes on those fields would be redundant (no read
    # benefit, only extra write cost on every ingest), so there aren't any.
    __table_args__ = (
        UniqueConstraint("ticker", "timeframe", "bucket_start", name="uq_candle"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    ticker: str
    timeframe: str
    session_part: Optional[str] = None  # only set for half_session
    bucket_start: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


class Setting(SQLModel, table=True):
    """Key-value user settings. Secret values (e.g. API key) are stored encrypted."""

    key: str = Field(primary_key=True)
    value: str = ""


class Analysis(SQLModel, table=True):
    # `strategy` is part of the identity key: switching the active strategy
    # (see app.strategies) must never silently overwrite or reuse another
    # strategy's cached result for the same ticker/timeframe/as_of. That same
    # unique constraint is backed by a composite index matching every real
    # query (WHERE ticker=? AND timeframe=? AND strategy=? ORDER BY as_of) --
    # no separate single-column indexes needed on top of it.
    __table_args__ = (
        UniqueConstraint("ticker", "timeframe", "strategy", "as_of", name="uq_analysis"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    ticker: str
    timeframe: str
    strategy: str = Field(default="wyckoff")
    as_of: datetime  # bucket_start of the latest analysed candle
    phase: str
    confidence: float
    signals_json: str  # JSON list of detected events
    levels_json: str  # JSON of support/resistance (or strategy-equivalent) levels
    narrative: Optional[str] = None
    advice: Optional[str] = None
    daily_trend: Optional[str] = None  # multi-timeframe context used, half_session only
    mtf_alignment: Optional[str] = None  # "aligned" | "conflicting" | None
    sub_agents_json: Optional[str] = None  # JSON of spawned subagents details
    created_at: datetime = Field(default_factory=_utcnow)


class SignalOutcome(SQLModel, table=True):
    """Forward-return outcome of one detected event, for signal-quality stats.

    Populated/updated incrementally: horizons with no future bar yet stay null
    and get filled in on a later analysis run once more candles exist.
    """

    # Every query here (see app.services.signal_outcomes) filters on some
    # prefix of ticker/timeframe/strategy, or the full 5-tuple -- all covered
    # by this constraint's composite index, so no separate single-column
    # indexes are needed on top of it.
    __table_args__ = (
        UniqueConstraint(
            "ticker", "timeframe", "strategy", "event_type", "event_ts", name="uq_signal_outcome"
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    ticker: str
    timeframe: str
    strategy: str = Field(default="wyckoff")
    event_type: str
    event_ts: datetime
    event_price: float
    # Set once at write time from the owning strategy's own BULLISH_EVENTS
    # set (see app.services.signal_outcomes.record_outcomes) -- persisted
    # rather than re-derived later, so get_stats() never has to guess which
    # strategy's event-type vocabulary a row belongs to.
    is_bullish: bool = False
    return_5: Optional[float] = None
    return_10: Optional[float] = None
    return_20: Optional[float] = None
    is_win_5: Optional[bool] = None
    is_win_10: Optional[bool] = None
    is_win_20: Optional[bool] = None
    updated_at: datetime = Field(default_factory=_utcnow)


class CryptoVolumeSnapshot(SQLModel, table=True):
    """One scan's (market_cap, 24h volume) reading for a coin.

    CoinGecko never reports a volume *trend* directly -- only the current 24h
    total. We keep our own history so the screener can compare the latest
    snapshot against a prior one to compute "volume rising" ourselves.
    """

    # _previous_snapshot() runs once per coin on every scan pass (the
    # hottest read in the whole app during a scan) as exactly
    # WHERE coin_id=? AND scanned_at<? ORDER BY scanned_at DESC -- a
    # composite index matching that shape, not two separate single-column
    # indexes (which SQLite can't combine as efficiently for this query).
    __table_args__ = (Index("ix_snapshot_coin_scanned", "coin_id", "scanned_at"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    coin_id: str  # CoinGecko coin id, e.g. "bitcoin"
    symbol: str = ""
    market_cap: float
    volume_24h: float
    scanned_at: datetime = Field(default_factory=_utcnow)


class ScreenerCandidate(SQLModel, table=True):
    """Latest screener result for one coin (upserted every scan).

    Deliberately separate from Symbol/Candle: a candidate is just a market-wide
    hit, not something the user has chosen to track yet. Promoting a candidate
    creates a Symbol (asset_class=crypto) and starts candle ingest for it.
    """

    coin_id: str = Field(primary_key=True)
    symbol: str = ""
    name: str = ""
    market_cap: float = Field(index=True)
    volume_24h: float
    # Indexed: list_candidates() sorts by one of these two per the user's
    # chosen sort order on every page load/scroll.
    volume_change_pct: Optional[float] = Field(default=None, index=True)
    last_seen_at: datetime = Field(default_factory=_utcnow)
    # "coingecko" (default, existing behaviour) or "geckoterminal" (found via
    # a DEX pool scan). DEX-only hits with no real CoinGecko id use a
    # synthesized coin_id ("gt:{network}:{pool_address}") and carry network +
    # pool_address here so ingest can fetch OHLCV without re-resolving.
    source: str = "coingecko"
    network: Optional[str] = None
    pool_address: Optional[str] = None
    # The actual centralized exchange (see CryptoExchange) this coin's symbol
    # resolves to, in the same priority order ingest_crypto uses (Binance >
    # KuCoin > MEXC) -- None for geckoterminal-sourced candidates, or if the
    # symbol isn't tradeable on any enabled exchange yet at scan time.
    exchange: Optional[str] = None


class ConfigChangeLog(SQLModel, table=True):
    """One row per Settings field that actually changed value (see
    app.services.settings_service.update()). Never holds the real
    anthropic_api_key value -- callers pass a presence placeholder instead."""

    id: Optional[int] = Field(default=None, primary_key=True)
    changed_at: datetime = Field(default_factory=_utcnow, index=True)
    key: str
    old_value: str
    new_value: str


class SystemActionLog(SQLModel, table=True):
    """Start/finish record for a scheduled job or a user-triggered background
    action (crypto screener scan, VN30 seed) -- lets the user see when
    something ran and whether it succeeded."""

    id: Optional[int] = Field(default=None, primary_key=True)
    action: str
    trigger: str  # "manual" | "scheduled"
    started_at: datetime = Field(default_factory=_utcnow, index=True)
    finished_at: Optional[datetime] = None
    status: str = "running"  # "running" | "success" | "error" | "cancelled"
    detail: Optional[str] = None
