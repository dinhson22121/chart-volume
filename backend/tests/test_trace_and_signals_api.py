import pandas as pd
import pytest
from fastapi.testclient import TestClient

from app.db import get_session
from app.main import app
from app.services import analysis as analysis_svc
from app.services import ingest

BASE = dict(open=100.0, high=101.0, low=99.0, close=100.0, volume=1000.0)
SPRING = dict(open=98.0, high=99.8, low=97.0, close=99.3, volume=1500.0)
CANNED = "NHẬN ĐỊNH:\nĐang tích lũy.\n\nLỜI KHUYÊN:\n- Theo dõi hỗ trợ."


@pytest.fixture
def client(session):
    app.dependency_overrides[get_session] = lambda: session
    yield TestClient(app)
    app.dependency_overrides.clear()


def _daily_df():
    t0 = pd.Timestamp("2025-01-01")
    bars = [dict(BASE) for _ in range(25)] + [SPRING]
    return pd.DataFrame([{"time": t0 + pd.Timedelta(days=i), **b} for i, b in enumerate(bars)])


def _refresh_fpt(client, auth_header, mocker):
    mocker.patch.object(ingest.vnstock_client, "fetch_daily", return_value=_daily_df())
    mocker.patch.object(analysis_svc.narrative_mod, "_call_claude", return_value=CANNED)
    resp = client.post("/analysis/FPT/refresh?timeframe=daily", headers=auth_header)
    assert resp.status_code == 200
    return resp.json()


def test_trace_requires_token(client):
    assert client.get("/analysis/FPT/trace?timeframe=daily&bar_ts=2025-01-01T00:00:00").status_code == 401


def test_trace_explains_the_matched_spring_bar(client, auth_header, mocker):
    _refresh_fpt(client, auth_header, mocker)
    candles = client.get("/candles/FPT?timeframe=daily", headers=auth_header).json()
    spring_bar_ts = candles[-1]["bucket_start"]  # Spring is the last (26th) candle

    resp = client.get(
        f"/analysis/FPT/trace?timeframe=daily&bar_ts={spring_bar_ts}", headers=auth_header
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["detectors"]) == 8
    spring = next(d for d in body["detectors"] if d["type"] == "Spring")
    assert spring["matched"] is True
    assert all(c["passed"] for c in spring["checks"])
    upthrust = next(d for d in body["detectors"] if d["type"] == "Upthrust")
    assert upthrust["matched"] is False


def test_trace_missing_bar_is_404(client, auth_header, mocker):
    _refresh_fpt(client, auth_header, mocker)
    resp = client.get(
        "/analysis/FPT/trace?timeframe=daily&bar_ts=2099-01-01T00:00:00", headers=auth_header
    )
    assert resp.status_code == 404


def test_trace_rejects_unsupported_strategy(client, auth_header, mocker):
    # Sonic R has no decision-tracing support in v1 -- must degrade gracefully
    # to a 400 rather than error out.
    _refresh_fpt(client, auth_header, mocker)
    client.put("/settings", json={"strategy": "sonicr"}, headers=auth_header)

    resp = client.get(
        "/analysis/FPT/trace?timeframe=daily&bar_ts=2025-01-01T00:00:00", headers=auth_header
    )
    assert resp.status_code == 400


def test_signal_stats_requires_token(client):
    assert client.get("/signals/stats").status_code == 401


def test_signal_stats_reflects_refreshed_analysis(client, auth_header, mocker):
    _refresh_fpt(client, auth_header, mocker)

    resp = client.get("/signals/stats", headers=auth_header)
    assert resp.status_code == 200
    stats = resp.json()
    spring_stats = next(s for s in stats if s["type"] == "Spring")
    assert spring_stats["count"] == 1
    assert spring_stats["is_bullish"] is True


def test_signal_stats_filters_by_ticker(client, auth_header, mocker):
    _refresh_fpt(client, auth_header, mocker)

    assert client.get("/signals/stats?ticker=FPT", headers=auth_header).json()
    assert client.get("/signals/stats?ticker=ZZZ", headers=auth_header).json() == []
