"""Ollama connectivity + model pull, proxied through our own backend so the
renderer never talks to a third-party local service directly."""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, field_validator

from app.auth import require_token
from app.services import ollama_service

router = APIRouter(prefix="/ollama", tags=["ollama"], dependencies=[Depends(require_token)])

# Plain "name" or "name:tag" only -- no "/" anywhere. Ollama's pull API lets a
# model string embed an alternate registry host as a leading path segment
# (e.g. "some-host.example/library/model"), which would let this endpoint
# direct the user's own local Ollama daemon to fetch from a host of the
# caller's choosing. None of the models this app suggests or supports need a
# namespace/host prefix, so it's simplest and safest to disallow "/" outright.
_MODEL_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}(:[a-zA-Z0-9._-]{1,20})?$")


class PullIn(BaseModel):
    model: str

    @field_validator("model")
    @classmethod
    def _validate_model(cls, value: str) -> str:
        if not _MODEL_PATTERN.match(value):
            raise ValueError("model must be a plain 'name' or 'name:tag' (letters/digits/._- only, no '/')")
        return value


@router.get("/status")
async def get_status() -> dict:
    return await ollama_service.get_status()


@router.post("/pull")
async def pull_model(payload: PullIn) -> StreamingResponse:
    return StreamingResponse(
        ollama_service.stream_pull(payload.model), media_type="application/x-ndjson"
    )
