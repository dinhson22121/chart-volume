import pytest
from fastapi.testclient import TestClient

from app.db import get_session
from app.main import app
from app.services import stock_batch_analysis


@pytest.fixture
def client(session):
    app.dependency_overrides[get_session] = lambda: session
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def _reset_state():
    yield
    stock_batch_analysis._cancel_requested.clear()
    stock_batch_analysis._state.update(
        running=False, total=None, completed=None, failed=None, current_ticker=None,
        last_error=None, last_cancelled=False, last_completed_at=None,
    )
    if stock_batch_analysis._lock.locked():
        stock_batch_analysis._lock.release()


def test_run_requires_token(client):
    assert client.post("/stock-batch-analysis/run").status_code == 401


def test_status_requires_token(client):
    assert client.get("/stock-batch-analysis/status").status_code == 401


def test_cancel_requires_token(client):
    assert client.post("/stock-batch-analysis/cancel").status_code == 401


def test_run_starts_background_task(client, auth_header, mocker):
    mocker.patch.object(stock_batch_analysis, "run_full_universe_analysis", return_value={"running": False})

    resp = client.post("/stock-batch-analysis/run", headers=auth_header)

    assert resp.status_code == 200
    assert resp.json()["status"] == "started"


def test_run_reports_already_running(client, auth_header):
    stock_batch_analysis._state["running"] = True

    resp = client.post("/stock-batch-analysis/run", headers=auth_header)

    assert resp.json()["status"] == "already_running"


def test_get_status(client, auth_header):
    resp = client.get("/stock-batch-analysis/status", headers=auth_header)
    assert resp.status_code == 200
    assert resp.json()["running"] is False


def test_cancel_sets_flag(client, auth_header):
    resp = client.post("/stock-batch-analysis/cancel", headers=auth_header)

    assert resp.status_code == 200
    assert stock_batch_analysis._cancel_requested.is_set()
