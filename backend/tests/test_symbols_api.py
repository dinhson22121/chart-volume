import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from app.api import symbols as symbols_api
from app.db import get_session
from app.main import app
from app.models import Symbol, SystemActionLog


@pytest.fixture
def client(session):
    app.dependency_overrides[get_session] = lambda: session
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_seed_vn30_requires_token(client):
    assert client.post("/symbols/seed-vn30").status_code == 401


def test_seed_vn30_reports_live_source(session, client, auth_header, mocker):
    mocker.patch.object(symbols_api, "fetch_vn30", return_value=(["FPT", "HPG"], "live"))

    resp = client.post("/symbols/seed-vn30", headers=auth_header)

    assert resp.status_code == 200
    assert resp.json() == {"count": 2, "source": "live"}
    tickers = {s.ticker for s in session.exec(select(Symbol)).all()}
    assert tickers == {"FPT", "HPG"}

    entries = session.exec(select(SystemActionLog)).all()
    assert len(entries) == 1
    assert entries[0].action == "vn30_seed"
    assert entries[0].trigger == "manual"
    assert entries[0].status == "success"
    assert "2 mã" in entries[0].detail


def test_seed_vn30_reports_fallback_source(session, client, auth_header, mocker):
    mocker.patch.object(symbols_api, "fetch_vn30", return_value=(["ACB", "BCM"], "fallback"))

    resp = client.post("/symbols/seed-vn30", headers=auth_header)

    assert resp.status_code == 200
    assert resp.json() == {"count": 2, "source": "fallback"}


def test_seed_vn30_marks_existing_symbol_as_vn30_without_duplicating(session, client, auth_header, mocker):
    session.add(Symbol(ticker="FPT", display_symbol="FPT", is_watchlist=True, is_vn30=False))
    session.commit()
    mocker.patch.object(symbols_api, "fetch_vn30", return_value=(["FPT"], "live"))

    client.post("/symbols/seed-vn30", headers=auth_header)

    symbol = session.get(Symbol, "FPT")
    assert symbol.is_vn30 is True
    assert symbol.is_watchlist is True  # untouched
