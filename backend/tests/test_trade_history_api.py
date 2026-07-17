import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.db import get_session
from app.main import app
from app.models import Timeframe, TradeScenario


@pytest.fixture
def client(session):
    app.dependency_overrides[get_session] = lambda: session
    yield TestClient(app)
    app.dependency_overrides.clear()


def _add_scenario(session, **overrides):
    defaults = dict(
        ticker="FPT", timeframe=Timeframe.DAILY, strategy="wyckoff", event_type="SOS",
        event_ts=pd.Timestamp("2025-01-01").to_pydatetime(), is_bullish=True,
        entry=100.0, stop_loss=95.0, take_profit=110.0, max_bars=10, status="hit_tp",
    )
    defaults.update(overrides)
    row = TradeScenario(**defaults)
    session.add(row)
    session.commit()
    return row


def test_trade_history_requires_token(client):
    assert client.get("/trade-history").status_code == 401


def test_trade_history_stats_requires_token(client):
    assert client.get("/trade-history/stats").status_code == 401


def test_trade_history_returns_paginated_envelope(session, client, auth_header):
    _add_scenario(session)

    resp = client.get("/trade-history", headers=auth_header)

    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["page"] == 1
    assert body["page_size"] == 50
    item = body["items"][0]
    assert item["ticker"] == "FPT"
    assert item["status"] == "hit_tp"
    assert item["entry"] == 100.0
    assert item["take_profit"] == 110.0


def test_trade_history_filters_by_query_params(session, client, auth_header):
    _add_scenario(session, ticker="FPT", status="hit_tp")
    _add_scenario(session, ticker="HPG", status="hit_sl", event_ts=pd.Timestamp("2025-01-02").to_pydatetime())

    resp = client.get("/trade-history?ticker=HPG", headers=auth_header)

    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["ticker"] == "HPG"


def test_trade_history_stats_returns_expected_shape(session, client, auth_header):
    _add_scenario(session, status="hit_tp")
    _add_scenario(session, status="hit_sl", event_ts=pd.Timestamp("2025-01-02").to_pydatetime())

    resp = client.get("/trade-history/stats", headers=auth_header)

    assert resp.status_code == 200
    body = resp.json()
    assert body["total_count"] == 2
    assert body["decided_count"] == 2
    assert body["win_count"] == 1
    assert body["loss_count"] == 1
    assert body["win_rate"] == pytest.approx(0.5)
    assert body["avg_pnl_pct"] is not None
