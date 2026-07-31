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
    WEEK = "1w"  # both stock and crypto -- resampled from daily, never crawled directly


class SessionPart:
    MORNING = "morning"
    AFTERNOON = "afternoon"


class AssetClass:
    STOCK = "stock"
    CRYPTO = "crypto"


class Exchange:
    """Listed VN stock exchange -- orthogonal to is_vn30 (a VN30 constituent
    is also HOSE). UPCOM (unlisted) is deliberately not tracked here (see
    app.services.hose_hnx): thinner liquidity, lower disclosure standards."""

    HOSE = "HOSE"
    HNX = "HNX"


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
    # Which listed exchange (see Exchange) a stock trades on -- None for
    # crypto and for stocks added before this field existed. Drives the
    # HOSE-vs-HNX daily price-limit band (app.services.trade_scenario).
    exchange: Optional[str] = None
    # HOSE/HNX-universe membership above the liquidity bar (crypto analog:
    # is_top100) -- seeded/refreshed by app.services.hose_hnx. A stock keeps
    # its `exchange` even after dropping below the bar; only this flag clears.
    is_hose_hnx: bool = False
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
    vp_alignment: Optional[str] = None  # "confirmed" | "unconfirmed" | None (Wyckoff Volume Profile only)
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
    # True when the event's own polarity matched the phase/regime trend the
    # engine classified at detection time (e.g. a bullish Spring inside a
    # bullish Accumulation phase). A counter-trend signal the engine already
    # discounted shouldn't drag down the "does this signal work" stats, so
    # get_stats can filter to aligned-only. Null on rows written before this
    # column existed.
    aligned: Optional[bool] = None
    # Fingerprint of the detector thresholds active when this row was first
    # created (see app.services.config_version) -- lets stats tell apart
    # samples produced under the currently-active thresholds from samples
    # produced under thresholds the user has since retuned. Empty string on
    # rows written before this column existed (unknown regime, not "current").
    config_version: str = ""
    updated_at: datetime = Field(default_factory=_utcnow)


class TradeScenario(SQLModel, table=True):
    """Entry/SL/TP plan spawned from one bullish/bearish event, tracked until
    it hits TP, hits SL, or expires after ``max_bars`` candles with neither.

    Mirrors SignalOutcome's shape (same identity tuple, populated once at
    detection then updated as later analysis runs bring in new candles) --
    see app.services.trade_scenario for the create/update logic.
    """

    # `source` is part of the identity tuple so a "live" row and a
    # "backtest" row for the exact same event can coexist (running a
    # backtest over a date range live tracking has already partly covered
    # shouldn't collide with what's already there). NOTE: existing SQLite
    # databases created before this field existed keep the OLD 5-column
    # constraint -- ALTER TABLE can't redefine a constraint in SQLite, and a
    # full table-recreation migration isn't worth it for what's currently a
    # narrow, low-risk edge case on a local single-user dev database. New
    # databases (create_all on a table that doesn't exist yet) get the
    # correct 6-column constraint below.
    __table_args__ = (
        UniqueConstraint(
            "ticker", "timeframe", "strategy", "event_type", "event_ts", "source", name="uq_trade_scenario"
        ),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    ticker: str
    timeframe: str
    strategy: str
    event_type: str
    event_ts: datetime
    is_bullish: bool
    entry: float
    stop_loss: float
    # Informational only since the breakeven+trailing-stop exit mechanism
    # (see app.services.trade_scenario.TRAIL_ATR_MULT/_resolve_outcome): the
    # original measured-move target computed at creation, displayed as a
    # reference point, but no longer an exit trigger -- a scenario now closes
    # via the (possibly-trailed) stop, never by touching this level.
    take_profit: float
    max_bars: int
    # hit_tp: closed at/above entry (breakeven or the trailing stop locked in
    # a profit) -- a win or scratch. hit_sl: closed below entry -- a real
    # loss. Neither implies price ever reached take_profit; see its own note.
    status: str = Field(default="active")  # active | hit_tp | hit_sl | expired
    # Plain-language rationale, set once at creation -- AI-written when a
    # narrative provider is configured, else a deterministic template built
    # from the scenario's own numbers (see app.services.trade_scenario).
    explanation: Optional[str] = None
    closed_at: Optional[datetime] = None
    closed_bar_ts: Optional[datetime] = None
    close_reason: Optional[str] = None
    # Set once, at close time, for all 3 close paths (hit_tp -> take_profit,
    # hit_sl -> stop_loss, expired -> last known close) -- see
    # app.services.trade_scenario._update_active_scenarios. Lets
    # get_scenario_stats compute R-multiple/P&L uniformly across every closed
    # status instead of excluding "expired" for lack of an exit price.
    exit_price: Optional[float] = None
    # Same fingerprint/semantics as SignalOutcome.config_version above.
    config_version: str = ""
    # True when the SL/TP distance from entry exceeds the stock exchange's
    # daily price-limit band (see settings_service.stock_daily_price_limit_pct)
    # while the scenario is expected to resolve within a session or two --
    # a level that band may not let a single day's price reach or that a
    # gap could jump straight through. Informational only, stock-only (see
    # app.services.trade_scenario._create_scenarios); never blocks creation.
    price_limit_caution: bool = False
    # "live" (forward-tracked, created as events happen -- see
    # app.services.trade_scenario.sync_scenarios) or "backtest" (walked
    # retroactively over already-known history -- see
    # app.services.scenario_backtest). Trust Layer stats (Monte Carlo,
    # Bootstrap, Walk-Forward) default to "live" only: backtested numbers can
    # be inflated by hindsight (detector thresholds tuned by eye against the
    # very history being scored) and must never be silently pooled with the
    # genuinely out-of-sample live-tracked ones.
    source: str = Field(default="live")
    updated_at: datetime = Field(default_factory=_utcnow)


class PotentialScreenResult(SQLModel, table=True):
    """Latest AI-only growth-potential verdict per ticker -- deliberately
    bypasses every quantitative strategy (Wyckoff/SMC/SonicR): the AI reads
    raw OHLCV candles directly and scores/explains on its own judgment. One
    row per ticker, overwritten each run -- no history kept."""

    ticker: str = Field(primary_key=True)
    score: float  # 0-100, the AI's own call -- unrelated to any strategy's confidence
    reason: str
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


class UserDrawing(SQLModel, table=True):
    """User-drawn trend lines on a ticker's chart -- one row per
    (ticker, timeframe), all of that chart's shapes stored together as one
    JSON blob (a user typically draws a handful of lines at a time, not
    enough to warrant a row-per-shape design). Overwritten in full on every
    save, same as PotentialScreenResult -- no per-shape history kept."""

    __table_args__ = (UniqueConstraint("ticker", "timeframe", name="uq_user_drawing"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    ticker: str
    timeframe: str
    shapes_json: str  # JSON list of {points: [{time, price}, {time, price}], color}
    updated_at: datetime = Field(default_factory=_utcnow)
