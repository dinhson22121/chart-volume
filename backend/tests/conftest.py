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


@pytest.fixture(autouse=True)
def _neutral_entry_quality_filters(monkeypatch):
    """Turn OFF the entry-quality filters (POI zone, minimum RR -- see
    app.services.trade_scenario) for every test by default.

    Those two gates decide WHETHER a setup is worth taking; almost every
    scenario test here is about something else entirely (entry/SL/TP
    formulas, max_bars, the trailing-stop exit, backtest replay ordering,
    idempotency) and builds a minimal synthetic fixture -- a flat 90/110
    range entered near 100 -- that the production defaults reject outright,
    which would leave those tests asserting on a scenario that was never
    created. Neutralizing the gates keeps each test exercising the one thing
    it names; the gates themselves are covered directly by their own tests
    (see test_trade_scenario.py's POI/RR section), which set the knobs
    explicitly rather than relying on this fixture."""
    from app.services import trade_scenario

    monkeypatch.setattr(trade_scenario, "POI_ZONE_THRESHOLD_PCT", 0.0)
    monkeypatch.setattr(trade_scenario, "MIN_RR_RATIO", 0.0)
