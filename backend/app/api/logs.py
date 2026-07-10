"""Activity log: config changes (Settings) and system action runs (scheduled
jobs, crypto screener scans, VN30 seeds)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlmodel import Session

from app.auth import require_token
from app.db import get_session
from app.models import ConfigChangeLog, SystemActionLog
from app.services import activity_log

router = APIRouter(prefix="/logs", tags=["logs"], dependencies=[Depends(require_token)])

DEFAULT_PAGE_SIZE = 50


def _config_out(e: ConfigChangeLog) -> dict:
    return {
        "id": e.id,
        "changed_at": e.changed_at,
        "key": e.key,
        "old_value": e.old_value,
        "new_value": e.new_value,
    }


def _action_out(e: SystemActionLog) -> dict:
    return {
        "id": e.id,
        "action": e.action,
        "trigger": e.trigger,
        "started_at": e.started_at,
        "finished_at": e.finished_at,
        "status": e.status,
        "detail": e.detail,
    }


@router.get("/config")
def get_config_logs(
    page: int = Query(1, ge=1),
    page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=200),
    session: Session = Depends(get_session),
) -> dict:
    items, total = activity_log.list_config_changes(session, page, page_size)
    return {
        "items": [_config_out(e) for e in items],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@router.get("/system")
def get_system_logs(
    page: int = Query(1, ge=1),
    page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=200),
    session: Session = Depends(get_session),
) -> dict:
    items, total = activity_log.list_system_actions(session, page, page_size)
    return {
        "items": [_action_out(e) for e in items],
        "total": total,
        "page": page,
        "page_size": page_size,
    }
