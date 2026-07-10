"""Tests for ollama_service.py. Uses hand-written fake async context managers
instead of a new test dependency (no pytest-asyncio/respx needed) -- run the
async functions directly via asyncio.run()."""

import asyncio

import httpx

from app.services import ollama_service


class _FakeResponse:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


class _FakeAsyncClient:
    def __init__(self, get_result=None, raise_exc=None):
        self._get_result = get_result
        self._raise_exc = raise_exc

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, url):
        if self._raise_exc:
            raise self._raise_exc
        return self._get_result


def test_get_status_when_ollama_running(mocker):
    fake_response = _FakeResponse({"models": [{"name": "qwen2.5:7b"}, {"name": "llama3.1:8b"}]})
    mocker.patch(
        "app.services.ollama_service.httpx.AsyncClient",
        return_value=_FakeAsyncClient(get_result=fake_response),
    )

    status = asyncio.run(ollama_service.get_status())

    assert status == {"available": True, "models": ["qwen2.5:7b", "llama3.1:8b"]}


def test_get_status_when_ollama_not_running(mocker):
    mocker.patch(
        "app.services.ollama_service.httpx.AsyncClient",
        return_value=_FakeAsyncClient(raise_exc=httpx.ConnectError("connection refused")),
    )

    status = asyncio.run(ollama_service.get_status())

    assert status == {"available": False, "models": []}


class _FakeStreamResponse:
    def __init__(self, lines):
        self._lines = lines

    def raise_for_status(self):
        pass

    async def aiter_lines(self):
        for line in self._lines:
            yield line


class _FakeStreamCM:
    def __init__(self, response):
        self._response = response

    async def __aenter__(self):
        return self._response

    async def __aexit__(self, *args):
        return False


class _FakeStreamClient:
    def __init__(self, lines):
        self._lines = lines

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def stream(self, method, url, json=None):
        return _FakeStreamCM(_FakeStreamResponse(self._lines))


def test_stream_pull_yields_ndjson_lines(mocker):
    mocker.patch(
        "app.services.ollama_service.httpx.AsyncClient",
        return_value=_FakeStreamClient(['{"status":"pulling"}', '{"status":"success"}']),
    )

    async def collect():
        return [chunk async for chunk in ollama_service.stream_pull("qwen2.5:7b")]

    chunks = asyncio.run(collect())

    assert chunks == [b'{"status":"pulling"}\n', b'{"status":"success"}\n']
