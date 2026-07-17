"""Ollama connectivity + model pull, proxied through our own backend so the
renderer never talks to a third-party local service directly."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, field_validator

from app.auth import require_token
from app.services import ollama_service
from app.validation import is_valid_ollama_model

router = APIRouter(prefix="/ollama", tags=["ollama"], dependencies=[Depends(require_token)])


class PullIn(BaseModel):
    model: str

    @field_validator("model")
    @classmethod
    def _validate_model(cls, value: str) -> str:
        if not is_valid_ollama_model(value):
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
