"""Ollama (local LLM) connectivity: list installed models, stream a pull's progress.

Ollama is a separate app the user installs themselves (free, runs on
localhost:11434) -- we never bundle model weights into chart-volume itself.
This module just proxies Ollama's own HTTP API so the renderer only ever talks
to our backend (consistent auth/CORS story, no cross-origin surprises).
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import httpx

BASE_URL = "http://localhost:11434"
_STATUS_TIMEOUT = 3.0  # fail fast if Ollama isn't running -- don't hang the UI


async def get_status() -> dict:
    try:
        async with httpx.AsyncClient(timeout=_STATUS_TIMEOUT) as client:
            resp = await client.get(f"{BASE_URL}/api/tags")
            resp.raise_for_status()
            data = resp.json()
        models = [m["name"] for m in data.get("models", [])]
        return {"available": True, "models": models}
    except httpx.HTTPError:
        return {"available": False, "models": []}


async def stream_pull(model: str) -> AsyncIterator[bytes]:
    """Proxy Ollama's streaming pull progress as newline-delimited JSON lines,
    each like {"status": "...", "completed": N, "total": M}."""
    async with httpx.AsyncClient(timeout=None) as client:
        async with client.stream(
            "POST", f"{BASE_URL}/api/pull", json={"name": model, "stream": True}
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if line:
                    yield (line + "\n").encode()
