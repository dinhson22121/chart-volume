"""Ollama connectivity + model pull, proxied through our own backend so the
renderer never talks to a third-party local service directly."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.auth import require_token
from app.services import ollama_service

router = APIRouter(prefix="/ollama", tags=["ollama"], dependencies=[Depends(require_token)])


class PullIn(BaseModel):
    model: str


@router.get("/status")
async def get_status() -> dict:
    return await ollama_service.get_status()


@router.post("/pull")
async def pull_model(payload: PullIn) -> StreamingResponse:
    return StreamingResponse(
        ollama_service.stream_pull(payload.model), media_type="application/x-ndjson"
    )
