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


def test_symbols_requires_token(client):
    assert client.get("/symbols").status_code == 401


def test_add_and_list_symbol(client, auth_header):
    resp = client.post("/symbols", json={"ticker": "hpg", "name": "Hoa Phat"}, headers=auth_header)
    assert resp.status_code == 200
    assert resp.json()["ticker"] == "HPG"

    listed = client.get("/symbols", headers=auth_header).json()
    assert any(s["ticker"] == "HPG" and s["is_watchlist"] for s in listed)


def test_refresh_then_get_analysis_and_candles(client, auth_header, mocker):
    mocker.patch.object(ingest.vnstock_client, "fetch_daily", return_value=_daily_df())
    mocker.patch.object(analysis_svc.narrative_mod, "_call_claude", return_value=CANNED)

    refreshed = client.post("/analysis/FPT/refresh?timeframe=daily", headers=auth_header)
    assert refreshed.status_code == 200
    body = refreshed.json()
    assert body["ticker"] == "FPT"
    assert body["phase"] == "Accumulation"
    assert "tích lũy" in body["narrative"]
    assert "support" in body["levels"]

    got = client.get("/analysis/FPT?timeframe=daily", headers=auth_header)
    assert got.status_code == 200 and got.json()["phase"] == "Accumulation"

    candles = client.get("/candles/FPT?timeframe=daily", headers=auth_header).json()
    assert len(candles) == 26
    # chronological order
    assert candles[0]["bucket_start"] < candles[-1]["bucket_start"]


def test_get_analysis_missing_is_404(client, auth_header):
    assert client.get("/analysis/ZZZ?timeframe=daily", headers=auth_header).status_code == 404


def test_invalid_timeframe_rejected(client, auth_header):
    assert client.get("/analysis/FPT?timeframe=weekly", headers=auth_header).status_code == 400
