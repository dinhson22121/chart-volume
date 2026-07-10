"""List available analysis strategies (see app.strategies.registry)."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.auth import require_token
from app.strategies import registry as strategy_registry

router = APIRouter(prefix="/strategies", tags=["strategies"], dependencies=[Depends(require_token)])


@router.get("")
def list_strategies() -> list[dict[str, str]]:
    return strategy_registry.list_strategies()
