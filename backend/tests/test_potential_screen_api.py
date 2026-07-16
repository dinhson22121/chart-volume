import pytest
from fastapi.testclient import TestClient

from app.db import get_session
from app.main import app
from app.models import PotentialScreenResult, Symbol
from app.services import potential_screener


@pytest.fixture
def client(session):
    app.dependency_overrides[get_session] = lambda: session
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def _reset_state():
    yield
    potential_screener._state.update(
        {"running": False, "total": None, "scored": None, "last_completed_at": None, "last_error": None}
    )
    if potential_screener._lock.locked():
        potential_screener._lock.release()


def test_run_requires_token(client):
    assert client.post("/potential-screen/run").status_code == 401


def test_status_requires_token(client):
    assert client.get("/potential-screen/status").status_code == 401


def test_results_requires_token(client):
    assert client.get("/potential-screen/results").status_code == 401


def test_run_starts_background_task(client, auth_header, mocker):
    mocker.patch.object(potential_screener, "run_potential_screen", return_value={"running": False})

    resp = client.post("/potential-screen/run", headers=auth_header)

    assert resp.status_code == 200
    assert resp.json()["status"] == "started"


def test_run_reports_already_running(client, auth_header):
    potential_screener._state["running"] = True

    resp = client.post("/potential-screen/run", headers=auth_header)

    assert resp.json()["status"] == "already_running"


def test_get_status(client, auth_header):
    resp = client.get("/potential-screen/status", headers=auth_header)
    assert resp.status_code == 200
    assert resp.json()["running"] is False


def test_get_results_returns_joined_and_sorted(session, client, auth_header):
    session.add(Symbol(ticker="FPT", display_symbol="FPT", is_vn30=True))
    session.add(Symbol(ticker="HPG", display_symbol="HPG", is_vn30=True))
    session.add(PotentialScreenResult(ticker="FPT", score=40.0, reason="low"))
    session.add(PotentialScreenResult(ticker="HPG", score=90.0, reason="high"))
    session.commit()

    resp = client.get("/potential-screen/results", headers=auth_header)

    assert resp.status_code == 200
    body = resp.json()
    assert [r["ticker"] for r in body] == ["HPG", "FPT"]
    assert body[0]["display_symbol"] == "HPG"
