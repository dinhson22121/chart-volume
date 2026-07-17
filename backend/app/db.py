"""Database engine + session management (SQLite via SQLModel)."""

from __future__ import annotations

from collections.abc import Iterator

from sqlmodel import Session, SQLModel, create_engine

from app.config import get_settings

# Import models so their tables register on SQLModel.metadata before create_all.
from app import models  # noqa: F401

_settings = get_settings()
_engine = create_engine(
    f"sqlite:///{_settings.db_path}",
    # timeout=30: safety margin for "database is locked" under concurrent
    # writes from the scheduler's thread-pooled batch jobs (default is
    # sqlite3's 5s, too tight once several workers write around the same time).
    connect_args={"check_same_thread": False, "timeout": 30},
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
    },
    "signaloutcome": {
        "aligned": "aligned BOOLEAN",
    },
    "analysis": {
        "sub_agents_json": "sub_agents_json TEXT",
    },
}


def _ensure_columns(engine) -> None:
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
