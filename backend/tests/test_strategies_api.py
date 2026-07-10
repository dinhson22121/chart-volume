import pytest
from fastapi.testclient import TestClient

from app.db import get_session
from app.main import app


@pytest.fixture
def client(session):
    app.dependency_overrides[get_session] = lambda: session
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_list_strategies_requires_token(client):
    assert client.get("/strategies").status_code == 401


def test_list_strategies_includes_wyckoff(client, auth_header):
    resp = client.get("/strategies", headers=auth_header)
    assert resp.status_code == 200
    body = resp.json()
    assert any(s["key"] == "wyckoff" for s in body)
    assert all("label" in s for s in body)


def test_list_strategies_includes_sonicr(client, auth_header):
    resp = client.get("/strategies", headers=auth_header)
    body = resp.json()
    assert any(s["key"] == "sonicr" for s in body)
