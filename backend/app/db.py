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


def init_db() -> None:
    """Create tables if they do not exist."""
    SQLModel.metadata.create_all(_engine)


def get_session() -> Iterator[Session]:
    """FastAPI dependency yielding a scoped session."""
    with Session(_engine) as session:
        yield session
