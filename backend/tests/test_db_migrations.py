"""Lightweight column migrations: create_all never ALTERs an existing table,
so columns added to Symbol after first release must be backfilled explicitly
(see app.db._ensure_columns)."""

from unittest.mock import MagicMock

from sqlalchemy.pool import StaticPool
from sqlmodel import create_engine

from app.db import _ensure_columns


def _make_engine():
    return create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )


def _symbol_columns(engine) -> set[str]:
    with engine.connect() as conn:
        return {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(symbol)")}


def test_adds_missing_columns_to_pre_existing_symbol_table():
    engine = _make_engine()
    with engine.connect() as conn:
        # Old-release schema: no is_top100/top100_rank columns yet.
        conn.exec_driver_sql(
            "CREATE TABLE symbol (ticker VARCHAR PRIMARY KEY, name VARCHAR, is_vn30 BOOLEAN)"
        )
        conn.exec_driver_sql("INSERT INTO symbol (ticker, name, is_vn30) VALUES ('FPT', 'FPT Corp', 1)")
        conn.commit()

    _ensure_columns(engine)

    cols = _symbol_columns(engine)
    assert {"is_top100", "top100_rank"} <= cols
    with engine.connect() as conn:
        row = conn.exec_driver_sql(
            "SELECT ticker, is_vn30, is_top100, top100_rank FROM symbol"
        ).one()
    assert row == ("FPT", 1, 0, None)  # old data intact, new columns defaulted


def test_adds_hose_hnx_columns_to_pre_existing_symbol_table():
    engine = _make_engine()
    with engine.connect() as conn:
        # Old-release schema: no exchange/is_hose_hnx columns yet.
        conn.exec_driver_sql(
            "CREATE TABLE symbol (ticker VARCHAR PRIMARY KEY, name VARCHAR, is_vn30 BOOLEAN)"
        )
        conn.exec_driver_sql("INSERT INTO symbol (ticker, name, is_vn30) VALUES ('FPT', 'FPT Corp', 1)")
        conn.commit()

    _ensure_columns(engine)

    cols = _symbol_columns(engine)
    assert {"exchange", "is_hose_hnx"} <= cols
    with engine.connect() as conn:
        row = conn.exec_driver_sql("SELECT ticker, exchange, is_hose_hnx FROM symbol").one()
    assert row == ("FPT", None, 0)  # old data intact, new columns defaulted


def test_noop_when_symbol_table_does_not_exist_yet():
    engine = _make_engine()

    _ensure_columns(engine)  # must not raise or create the table

    assert _symbol_columns(engine) == set()


def test_noop_when_columns_already_present():
    engine = _make_engine()
    with engine.connect() as conn:
        conn.exec_driver_sql(
            "CREATE TABLE symbol (ticker VARCHAR PRIMARY KEY, is_top100 BOOLEAN NOT NULL DEFAULT 0, "
            "top100_rank INTEGER)"
        )
        conn.commit()

    _ensure_columns(engine)  # second run must not raise (duplicate column)

    assert {"is_top100", "top100_rank"} <= _symbol_columns(engine)


def test_adds_source_column_to_pre_existing_tradescenario_table():
    engine = _make_engine()
    with engine.connect() as conn:
        conn.exec_driver_sql(
            "CREATE TABLE tradescenario (id INTEGER PRIMARY KEY, ticker VARCHAR, status VARCHAR)"
        )
        conn.exec_driver_sql("INSERT INTO tradescenario (ticker, status) VALUES ('FPT', 'active')")
        conn.commit()

    _ensure_columns(engine)

    with engine.connect() as conn:
        cols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(tradescenario)")}
        assert "source" in cols
        row = conn.exec_driver_sql("SELECT ticker, source FROM tradescenario").one()
    assert row == ("FPT", "live")  # old rows default to "live", not NULL


def test_adds_aligned_column_to_pre_existing_signaloutcome_table():
    engine = _make_engine()
    with engine.connect() as conn:
        # Old-release schema: signaloutcome without the aligned column.
        conn.exec_driver_sql(
            "CREATE TABLE signaloutcome (id INTEGER PRIMARY KEY, ticker VARCHAR, "
            "timeframe VARCHAR, strategy VARCHAR, event_type VARCHAR, is_bullish BOOLEAN)"
        )
        conn.exec_driver_sql(
            "INSERT INTO signaloutcome (ticker, event_type, is_bullish) VALUES ('FPT', 'Spring', 1)"
        )
        conn.commit()

    _ensure_columns(engine)

    with engine.connect() as conn:
        cols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(signaloutcome)")}
        assert "aligned" in cols
        row = conn.exec_driver_sql("SELECT ticker, aligned FROM signaloutcome").one()
    assert row == ("FPT", None)  # old row keeps null alignment


def test_skips_entirely_for_a_non_sqlite_engine():
    # PRAGMA table_info is SQLite-only syntax -- a Postgres deployment (see
    # DATABASE_URL in app/db.py) is always a fresh DB that create_all alone
    # builds completely, so this must never even try to connect for one.
    engine = MagicMock()
    engine.dialect.name = "postgresql"

    _ensure_columns(engine)

    engine.connect.assert_not_called()
