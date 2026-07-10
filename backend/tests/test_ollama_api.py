import pytest
from fastapi.testclient import TestClient

from app.db import get_session
from app.main import app
from app.services import ollama_service


@pytest.fixture
def client(session):
    app.dependency_overrides[get_session] = lambda: session
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_ollama_status_requires_token(client):
    assert client.get("/ollama/status").status_code == 401


def test_ollama_status_proxies_service(client, auth_header, mocker):
    async def fake_status():
        return {"available": True, "models": ["qwen2.5:7b"]}

    mocker.patch.object(ollama_service, "get_status", side_effect=fake_status)

    resp = client.get("/ollama/status", headers=auth_header)

    assert resp.status_code == 200
    assert resp.json() == {"available": True, "models": ["qwen2.5:7b"]}


def test_ollama_pull_requires_token(client):
    assert client.post("/ollama/pull", json={"model": "qwen2.5:7b"}).status_code == 401


def test_ollama_pull_streams_progress(client, auth_header, mocker):
    async def fake_stream(model):
        yield b'{"status":"pulling manifest"}\n'
        yield b'{"status":"success"}\n'

    mocker.patch.object(ollama_service, "stream_pull", side_effect=fake_stream)

    resp = client.post("/ollama/pull", json={"model": "qwen2.5:7b"}, headers=auth_header)

    assert resp.status_code == 200
    assert b'"status":"pulling manifest"' in resp.content
    assert b'"status":"success"' in resp.content
