"""Shared test fixtures: isolated in-memory-ish SQLite + deterministic token."""

from __future__ import annotations

import os

# Set env before any app import so config picks it up.
os.environ.setdefault("LOCAL_API_TOKEN", "test-token")
os.environ.setdefault("DB_PATH", "test_chart_volume.db")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-key")
# Fixed key so app.crypto never writes a settings.key file during test runs.
os.environ.setdefault("SETTINGS_KEY", "0" * 64)

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

# Register tables on SQLModel.metadata.
from app import models  # noqa: F401,E402

TEST_TOKEN = "test-token"


@pytest.fixture
def auth_header() -> dict[str, str]:
    return {"Authorization": f"Bearer {TEST_TOKEN}"}


@pytest.fixture
def session() -> Session:
    """Isolated in-memory SQLite session per test."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as sess:
        yield sess
