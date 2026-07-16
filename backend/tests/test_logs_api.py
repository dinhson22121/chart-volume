import pytest
from fastapi.testclient import TestClient

from app.db import get_session
from app.main import app
from app.services import activity_log


@pytest.fixture
def client(session):
    app.dependency_overrides[get_session] = lambda: session
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_config_logs_requires_token(client):
    assert client.get("/logs/config").status_code == 401


def test_system_logs_requires_token(client):
    assert client.get("/logs/system").status_code == 401


def test_config_logs_returns_paginated_envelope(session, client, auth_header):
    activity_log.log_config_change(session, "strategy", "wyckoff", "sonicr")
    session.commit()

    resp = client.get("/logs/config", headers=auth_header)

    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    assert body["page"] == 1
    assert body["items"][0]["key"] == "strategy"
    assert body["items"][0]["old_value"] == "wyckoff"
    assert body["items"][0]["new_value"] == "sonicr"


def test_system_logs_returns_paginated_envelope(session, client, auth_header):
    log_id = activity_log.log_action_start(session, "vn30_seed", "manual")
    activity_log.log_action_finish(session, log_id, "success", "30 mã (live)")

    resp = client.get("/logs/system", headers=auth_header)

    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 1
    item = body["items"][0]
    assert item["action"] == "vn30_seed"
    assert item["trigger"] == "manual"
    assert item["status"] == "success"
    assert item["detail"] == "30 mã (live)"
    assert item["finished_at"] is not None


def test_export_logs_requires_token(client):
    assert client.get("/logs/export").status_code == 401


def test_export_logs_includes_all_three_sections(session, client, auth_header):
    activity_log.log_config_change(session, "strategy", "wyckoff", "smc")
    log_id = activity_log.log_action_start(session, "top100_seed", "scheduled")
    activity_log.log_action_finish(session, log_id, "success", "99 coin")
    session.commit()

    resp = client.get("/logs/export", headers=auth_header)

    assert resp.status_code == 200
    body = resp.json()
    content = body["content"]
    assert "generated_at" in body
    assert "=== BACKEND LOG" in content
    assert "=== CONFIG CHANGES" in content
    assert "=== SYSTEM ACTIONS" in content
    assert "strategy | wyckoff -> smc" in content
    assert "top100_seed | scheduled | success" in content
    assert "99 coin" in content


def test_export_logs_handles_missing_log_file_gracefully(session, client, auth_header, mocker, tmp_path):
    missing = tmp_path / "does-not-exist.log"
    mocker.patch("app.api.logs.log_file_path", return_value=missing)

    resp = client.get("/logs/export", headers=auth_header)

    assert resp.status_code == 200
    assert "chưa có file log" in resp.json()["content"]


def test_export_logs_caps_raw_log_to_the_tail(session, client, auth_header, mocker, tmp_path):
    big_log = tmp_path / "big.log"
    big_log.write_text("A" * 600_000 + "TAIL_MARKER", encoding="utf-8")
    mocker.patch("app.api.logs.log_file_path", return_value=big_log)

    resp = client.get("/logs/export", headers=auth_header)

    content = resp.json()["content"]
    assert "TAIL_MARKER" in content
    assert len(content) < 600_000 + 1000  # capped, not the full 600KB+ echoed back
