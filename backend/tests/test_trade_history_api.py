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
    assert item["price_limit_caution"] is False


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
    # New WP1/WP3/WP4 fields: current_config_count needs a strategy filter to
    # mean anything (see app/api/trade_history.py), benchmark/drawdown are
    # always computed.
    assert body["current_config_count"] is None
    assert "benchmark_buy_hold_pct" in body
    assert "max_drawdown_r" in body
    assert "max_consecutive_losses" in body
    assert "monte_carlo" in body
    assert "bootstrap" in body
    assert "walk_forward" in body


def test_trade_history_stats_current_config_count_set_with_strategy_filter(session, client, auth_header):
    _add_scenario(session, status="hit_tp", strategy="wyckoff")

    resp = client.get("/trade-history/stats?strategy=wyckoff", headers=auth_header)

    assert resp.status_code == 200
    body = resp.json()
    # Row predates config_version (stored as "" by the _add_scenario helper,
    # which doesn't go through trade_scenario._create_scenarios), so it never
    # matches today's computed version -- 0, not None, since a strategy WAS named.
    assert body["current_config_count"] == 0


def test_trade_history_item_exposes_price_limit_caution(session, client, auth_header):
    _add_scenario(session, price_limit_caution=True)

    resp = client.get("/trade-history", headers=auth_header)

    assert resp.json()["items"][0]["price_limit_caution"] is True


def test_trade_history_item_exposes_source(session, client, auth_header):
    _add_scenario(session)

    resp = client.get("/trade-history", headers=auth_header)

    assert resp.json()["items"][0]["source"] == "live"


def test_trade_history_defaults_to_live_source_only(session, client, auth_header):
    _add_scenario(session, ticker="FPT", source="live")
    _add_scenario(
        session, ticker="HPG", source="backtest", event_ts=pd.Timestamp("2025-01-02").to_pydatetime()
    )

    resp = client.get("/trade-history", headers=auth_header)

    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["ticker"] == "FPT"


def test_trade_history_source_all_pools_both(session, client, auth_header):
    _add_scenario(session, ticker="FPT", source="live")
    _add_scenario(
        session, ticker="HPG", source="backtest", event_ts=pd.Timestamp("2025-01-02").to_pydatetime()
    )

    resp = client.get("/trade-history?source=all", headers=auth_header)

    assert resp.json()["total"] == 2


def test_trade_history_stats_defaults_to_live_source_only(session, client, auth_header):
    _add_scenario(session, status="hit_tp", source="live")
    _add_scenario(
        session, status="hit_sl", source="backtest", event_ts=pd.Timestamp("2025-01-02").to_pydatetime()
    )

    resp = client.get("/trade-history/stats", headers=auth_header)

    assert resp.json()["decided_count"] == 1


def test_trade_history_backtest_requires_token(client):
    assert client.post("/trade-history/backtest?ticker=FPT").status_code == 401


def test_trade_history_backtest_returns_zero_created_when_no_candles(client, auth_header):
    resp = client.post("/trade-history/backtest?ticker=NOPE", headers=auth_header)

    assert resp.status_code == 200
    assert resp.json() == {"ticker": "NOPE", "timeframe": "daily", "created": 0}


def test_trade_history_backtest_rejects_invalid_ticker(client, auth_header):
    resp = client.post("/trade-history/backtest?ticker=bad%20ticker", headers=auth_header)

    assert resp.status_code == 400


def test_trade_history_backtest_rejects_invalid_timeframe(client, auth_header):
    resp = client.post("/trade-history/backtest?ticker=FPT&timeframe=nope", headers=auth_header)

    assert resp.status_code == 400


def test_trade_history_backtest_rejects_unknown_strategy(client, auth_header):
    resp = client.post("/trade-history/backtest?ticker=FPT&strategy=nope", headers=auth_header)

    assert resp.status_code == 400
