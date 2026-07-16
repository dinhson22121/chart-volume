"""Activity log: config changes (Settings) and system action runs (scheduled
jobs, crypto screener scans, VN30 seeds)."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from sqlmodel import Session

from app.auth import require_token
from app.config import log_file_path
from app.db import get_session
from app.models import ConfigChangeLog, SystemActionLog
from app.services import activity_log

router = APIRouter(prefix="/logs", tags=["logs"], dependencies=[Depends(require_token)])

DEFAULT_PAGE_SIZE = 50
EXPORT_PAGE_SIZE = 1000  # generous cap for a one-shot debug export, not a paginated view
_MAX_RAW_LOG_CHARS = 500_000  # ~500KB tail -- plenty for debugging, bounds the response


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


def _read_backend_log() -> str:
    path = log_file_path()
    if not path.exists():
        return "(chưa có file log -- backend chưa từng ghi log kỹ thuật, hoặc file đã bị xoá)"
    text = path.read_text(encoding="utf-8", errors="replace")
    if len(text) > _MAX_RAW_LOG_CHARS:
        text = text[-_MAX_RAW_LOG_CHARS:]
    return text


def _format_export(raw_log: str, config_items: list[ConfigChangeLog], system_items: list[SystemActionLog]) -> str:
    lines = [
        "=== CHART-VOLUME DEBUG LOG EXPORT ===",
        f"Generated at: {datetime.now(timezone.utc).isoformat()}",
        "",
        "=== BACKEND LOG (raw, technical) ===",
        raw_log,
        "",
        f"=== CONFIG CHANGES (latest {len(config_items)}) ===",
    ]
    for e in config_items:
        lines.append(f"{e.changed_at} | {e.key} | {e.old_value} -> {e.new_value}")
    lines.append("")
    lines.append(f"=== SYSTEM ACTIONS (latest {len(system_items)}) ===")
    for e in system_items:
        lines.append(
            f"{e.started_at} | {e.action} | {e.trigger} | {e.status} | "
            f"{e.finished_at or '-'} | {e.detail or '-'}"
        )
    return "\n".join(lines)


@router.get("/export")
def export_logs(session: Session = Depends(get_session)) -> dict:
    config_items, _ = activity_log.list_config_changes(session, page=1, page_size=EXPORT_PAGE_SIZE)
    system_items, _ = activity_log.list_system_actions(session, page=1, page_size=EXPORT_PAGE_SIZE)
    content = _format_export(_read_backend_log(), config_items, system_items)
    return {"content": content, "generated_at": datetime.now(timezone.utc).isoformat()}
