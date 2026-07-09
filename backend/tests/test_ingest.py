import pandas as pd
from sqlmodel import select

from app.models import Candle, Timeframe
from app.services import ingest


def _daily_df():
    return pd.DataFrame([
        {"time": "2025-06-30", "open": 100, "high": 105, "low": 99, "close": 104, "volume": 1_000_000},
        {"time": "2025-07-01", "open": 104, "high": 106, "low": 103, "close": 105, "volume": 1_200_000},
    ])


def test_ingest_daily_inserts(session, mocker):
    mocker.patch.object(ingest.vnstock_client, "fetch_daily", return_value=_daily_df())

    count = ingest.ingest_daily(session, "fpt")

    assert count == 2
    rows = session.exec(select(Candle).where(Candle.timeframe == Timeframe.DAILY)).all()
    assert len(rows) == 2
    assert all(r.ticker == "FPT" for r in rows)  # normalised upper-case
    last = [r for r in rows if r.close == 105][0]
    assert last.open == 104 and last.high == 106 and last.volume == 1_200_000


def test_ingest_daily_is_idempotent_and_updates(session, mocker):
    mocker.patch.object(ingest.vnstock_client, "fetch_daily", return_value=_daily_df())
    ingest.ingest_daily(session, "FPT")

    # Re-run with a corrected close for 2025-07-01; must update, not duplicate.
    updated = _daily_df()
    updated.loc[updated["time"] == "2025-07-01", "close"] = 108
    mocker.patch.object(ingest.vnstock_client, "fetch_daily", return_value=updated)
    ingest.ingest_daily(session, "FPT")

    rows = session.exec(select(Candle)).all()
    assert len(rows) == 2  # no duplicates
    jul1 = [r for r in rows if r.bucket_start == pd.Timestamp("2025-07-01").to_pydatetime()][0]
    assert jul1.close == 108


def test_ingest_half_session_from_hourly(session, mocker):
    hourly = pd.DataFrame([
        {"time": "2025-07-03 09:00:00", "open": 100, "high": 101, "low": 99, "close": 100.5, "volume": 1000},
        {"time": "2025-07-03 10:00:00", "open": 100.5, "high": 102, "low": 100, "close": 101.5, "volume": 2000},
        {"time": "2025-07-03 13:00:00", "open": 101.5, "high": 101.6, "low": 100, "close": 100.2, "volume": 1500},
    ])
    mocker.patch.object(ingest.vnstock_client, "fetch_hourly", return_value=hourly)

    count = ingest.ingest_half_session(session, "FPT")

    assert count == 2
    rows = session.exec(select(Candle).where(Candle.timeframe == Timeframe.HALF_SESSION)).all()
    parts = {r.session_part for r in rows}
    assert parts == {"morning", "afternoon"}
