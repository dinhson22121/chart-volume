"""End-to-end wiring test: switching the active strategy to Sonic R and
running the full app.services.analysis.run_analysis() pipeline against it,
including the SignalOutcome.is_bullish persistence fixed in Milestone 1.
"""

import pandas as pd
import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from app.db import get_session
from app.main import app
from app.models import Analysis, Candle, SignalOutcome, Timeframe
from app.sonicr.phase import PHASE_DOWNTREND, PHASE_RANGING, PHASE_UPTREND
from app.services import analysis as analysis_svc
from app.services import settings_service


def _seed_uptrend_candles(session, n=60, ticker="BTC"):
    t0 = pd.Timestamp("2025-01-01")
    for i in range(n):
        price = 100.0 + i * 0.8
        session.add(
            Candle(
                ticker=ticker,
                timeframe=Timeframe.DAILY,
                bucket_start=(t0 + pd.Timedelta(days=i)).to_pydatetime(),
                open=price,
                high=price + 1,
                low=price - 1,
                close=price,
                volume=1000.0,
            )
        )
    session.commit()


def test_run_analysis_uses_sonicr_when_selected(session):
    settings_service.update(session, {"strategy": "sonicr"})
    _seed_uptrend_candles(session)

    result = analysis_svc.run_analysis(session, "BTC", Timeframe.DAILY, use_ai=False)

    assert result is not None
    assert result.strategy == "sonicr"
    assert result.phase in {PHASE_UPTREND, PHASE_DOWNTREND, PHASE_RANGING}


def test_run_analysis_sonicr_persists_correct_is_bullish_on_signal_outcomes(session):
    settings_service.update(session, {"strategy": "sonicr"})
    _seed_uptrend_candles(session)

    analysis_svc.run_analysis(session, "BTC", Timeframe.DAILY, use_ai=False)

    from app.sonicr import BULLISH_EVENTS

    outcomes = session.exec(
        select(SignalOutcome).where(SignalOutcome.ticker == "BTC", SignalOutcome.strategy == "sonicr")
    ).all()
    for outcome in outcomes:
        assert outcome.is_bullish == (outcome.event_type in BULLISH_EVENTS)


def test_switching_strategy_to_sonicr_creates_a_separate_analysis_row(session):
    _seed_uptrend_candles(session)

    wyckoff_result = analysis_svc.run_analysis(session, "BTC", Timeframe.DAILY, use_ai=False)
    assert wyckoff_result.strategy == "wyckoff"

    settings_service.update(session, {"strategy": "sonicr"})
    sonicr_result = analysis_svc.run_analysis(session, "BTC", Timeframe.DAILY, use_ai=False)
    assert sonicr_result.strategy == "sonicr"

    rows = session.exec(
        select(Analysis).where(Analysis.ticker == "BTC", Analysis.as_of == wyckoff_result.as_of)
    ).all()
    assert {r.strategy for r in rows} == {"wyckoff", "sonicr"}


@pytest.fixture
def client(session):
    app.dependency_overrides[get_session] = lambda: session
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_get_indicators_requires_token(client):
    assert client.get("/analysis/BTC/indicators?timeframe=daily").status_code == 401


def test_get_indicators_returns_dragon_and_t3_series(session, client, auth_header):
    _seed_uptrend_candles(session)

    resp = client.get("/analysis/BTC/indicators?timeframe=daily", headers=auth_header)

    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {"dragon", "t3_fast", "t3_slow"}
    assert len(body["dragon"]) == 60
    assert all("ts" in p and "value" in p for p in body["dragon"])


def test_get_indicators_rejects_invalid_timeframe(client, auth_header):
    resp = client.get("/analysis/BTC/indicators?timeframe=weekly", headers=auth_header)
    assert resp.status_code == 400


def test_get_indicators_empty_when_no_candles(client, auth_header):
    resp = client.get("/analysis/ZZZ/indicators?timeframe=daily", headers=auth_header)
    assert resp.status_code == 200
    assert resp.json() == {"dragon": [], "t3_fast": [], "t3_slow": []}
