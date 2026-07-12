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
    connect_args={"check_same_thread": False},
)


def get_engine():
    return _engine


# Columns added to the Symbol model after the first release. create_all only
# creates missing *tables*, never alters existing ones, so a user's live DB
# needs these backfilled explicitly. Maps column name -> ALTER clause.
_SYMBOL_COLUMN_MIGRATIONS = {
    "is_top100": "is_top100 BOOLEAN NOT NULL DEFAULT 0",
    "top100_rank": "top100_rank INTEGER",
}


def _ensure_symbol_columns(engine) -> None:
    with engine.connect() as conn:
        existing = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(symbol)")}
        if not existing:
            return  # table doesn't exist yet; create_all will build it complete
        for column, clause in _SYMBOL_COLUMN_MIGRATIONS.items():
            if column not in existing:
                conn.exec_driver_sql(f"ALTER TABLE symbol ADD COLUMN {clause}")
        conn.commit()


def init_db() -> None:
    """Create tables if they do not exist, and backfill columns added later."""
    _ensure_symbol_columns(_engine)
    SQLModel.metadata.create_all(_engine)


def get_session() -> Iterator[Session]:
    """FastAPI dependency yielding a scoped session."""
    with Session(_engine) as session:
        yield session
