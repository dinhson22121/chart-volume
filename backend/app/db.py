"""Database engine + session management.

Defaults to a local SQLite file (the desktop app's normal mode). Setting
``DATABASE_URL`` (e.g. to a ``postgresql://...`` URL -- see backend/Dockerfile)
switches to that instead, for a self-hosted/cloud deployment; this is opt-in
only and never required for the desktop flow."""

from __future__ import annotations

from collections.abc import Iterator

from sqlmodel import Session, SQLModel, create_engine

from app.config import get_settings

# Import models so their tables register on SQLModel.metadata before create_all.
from app import models  # noqa: F401

_settings = get_settings()
_DATABASE_URL = _settings.database_url or f"sqlite:///{_settings.db_path}"
_IS_SQLITE = _DATABASE_URL.startswith("sqlite")
_engine = create_engine(
    _DATABASE_URL,
    # timeout=30: safety margin for "database is locked" under concurrent
    # writes from the scheduler's thread-pooled batch jobs (default is
    # sqlite3's 5s, too tight once several workers write around the same
    # time). SQLite-only -- Postgres has no such argument and handles
    # concurrent writes at the server, not the client library, level.
    connect_args={"check_same_thread": False, "timeout": 30} if _IS_SQLITE else {},
)


def get_engine():
    return _engine


# Columns added to models after the first release. create_all only creates
# missing *tables*, never alters existing ones, so a user's live DB needs
# these backfilled explicitly. Per table: column name -> ALTER clause.
_COLUMN_MIGRATIONS = {
    "symbol": {
        "is_top100": "is_top100 BOOLEAN NOT NULL DEFAULT 0",
        "top100_rank": "top100_rank INTEGER",
        "exchange": "exchange TEXT",
        "is_hose_hnx": "is_hose_hnx BOOLEAN NOT NULL DEFAULT 0",
    },
    "signaloutcome": {
        "aligned": "aligned BOOLEAN",
        "config_version": "config_version TEXT NOT NULL DEFAULT ''",
    },
    "analysis": {
        "sub_agents_json": "sub_agents_json TEXT",
        "vp_alignment": "vp_alignment TEXT",
    },
    "tradescenario": {
        "exit_price": "exit_price FLOAT",
        "config_version": "config_version TEXT NOT NULL DEFAULT ''",
        "price_limit_caution": "price_limit_caution BOOLEAN NOT NULL DEFAULT 0",
        "source": "source TEXT NOT NULL DEFAULT 'live'",
        "partial_exit_price": "partial_exit_price FLOAT",
        "partial_exit_bar_ts": "partial_exit_bar_ts DATETIME",
    },
}


def _ensure_columns(engine) -> None:
    # PRAGMA table_info is SQLite-only syntax -- a Postgres deployment is
    # always a fresh database (create_all alone builds the complete
    # up-to-date schema for it), so this incremental-migration step, which
    # exists only to backfill columns onto an existing user's local SQLite
    # file from before those columns existed, doesn't apply there at all.
    # Checked on the engine actually passed in (not the module-level
    # _IS_SQLITE) so a test engine's own dialect always decides this, not
    # whatever DATABASE_URL happens to be set in the environment.
    if engine.dialect.name != "sqlite":
        return
    with engine.connect() as conn:
        for table, migrations in _COLUMN_MIGRATIONS.items():
            existing = {row[1] for row in conn.exec_driver_sql(f"PRAGMA table_info({table})")}
            if not existing:
                continue  # table doesn't exist yet; create_all will build it complete
            for column, clause in migrations.items():
                if column not in existing:
                    conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {clause}")
        conn.commit()


def init_db() -> None:
    """Create tables if they do not exist, and backfill columns added later."""
    _ensure_columns(_engine)
    SQLModel.metadata.create_all(_engine)


def get_session() -> Iterator[Session]:
    """FastAPI dependency yielding a scoped session."""
    with Session(_engine) as session:
        yield session
