import pytest
from fastapi.testclient import TestClient

from app.db import get_session
from app.main import app


@pytest.fixture
def client(session):
    app.dependency_overrides[get_session] = lambda: session
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_get_drawings_requires_token(client):
    assert client.get("/drawings/HPG?timeframe=daily").status_code == 401


def test_get_drawings_returns_empty_list_when_none_saved(client, auth_header):
    resp = client.get("/drawings/HPG?timeframe=daily", headers=auth_header)
    assert resp.status_code == 200
    assert resp.json() == {"shapes": []}


def test_save_and_get_drawings_round_trip(client, auth_header):
    shapes = {
        "shapes": [
            {
                "points": [
                    {"time": "2025-01-01T00:00:00", "price": 100.0},
                    {"time": "2025-01-05T00:00:00", "price": 110.0},
                ],
                "color": "#4fc3f7",
            }
        ]
    }
    put_resp = client.put("/drawings/hpg?timeframe=daily", json=shapes, headers=auth_header)
    assert put_resp.status_code == 200
    assert put_resp.json() == shapes

    get_resp = client.get("/drawings/HPG?timeframe=daily", headers=auth_header)
    assert get_resp.json() == shapes


def test_saving_again_overwrites_rather_than_appending(client, auth_header):
    first = {"shapes": [{"points": [{"time": "2025-01-01T00:00:00", "price": 100.0}], "color": "#4fc3f7"}]}
    second = {"shapes": [{"points": [{"time": "2025-02-01T00:00:00", "price": 200.0}], "color": "#e0574b"}]}

    client.put("/drawings/HPG?timeframe=daily", json=first, headers=auth_header)
    client.put("/drawings/HPG?timeframe=daily", json=second, headers=auth_header)

    resp = client.get("/drawings/HPG?timeframe=daily", headers=auth_header)
    assert resp.json() == second


def test_drawings_are_scoped_per_timeframe(client, auth_header):
    daily = {"shapes": [{"points": [{"time": "2025-01-01T00:00:00", "price": 100.0}], "color": "#4fc3f7"}]}
    client.put("/drawings/HPG?timeframe=daily", json=daily, headers=auth_header)

    resp = client.get("/drawings/HPG?timeframe=weekly", headers=auth_header)
    assert resp.json() == {"shapes": []}
