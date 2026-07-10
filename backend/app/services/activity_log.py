"""Activity log: config changes (Settings) and system action runs (scheduled
jobs, crypto screener scans, VN30 seeds) -- lets the user answer "what
happened, and when" from the UI instead of grepping the process log."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlmodel import Session, func, select

from app.models import ConfigChangeLog, SystemActionLog


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def log_config_change(session: Session, key: str, old_value: str, new_value: str) -> None:
    """No-op when the value didn't actually change -- re-submitting the same
    settings payload shouldn't spam the log."""
    if old_value == new_value:
        return
    session.add(ConfigChangeLog(key=key, old_value=old_value, new_value=new_value))


def log_action_start(session: Session, action: str, trigger: str) -> int:
    entry = SystemActionLog(action=action, trigger=trigger, started_at=_utcnow(), status="running")
    session.add(entry)
    session.commit()
    session.refresh(entry)
    return entry.id


def log_action_finish(session: Session, log_id: int, status: str, detail: str | None = None) -> None:
    entry = session.get(SystemActionLog, log_id)
    if entry is None:
        return
    entry.finished_at = _utcnow()
    entry.status = status
    entry.detail = detail
    session.add(entry)
    session.commit()


def list_config_changes(session: Session, page: int, page_size: int) -> tuple[list[ConfigChangeLog], int]:
    total = session.exec(select(func.count()).select_from(ConfigChangeLog)).one()
    items = session.exec(
        select(ConfigChangeLog)
        .order_by(ConfigChangeLog.changed_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return items, total


def list_system_actions(session: Session, page: int, page_size: int) -> tuple[list[SystemActionLog], int]:
    total = session.exec(select(func.count()).select_from(SystemActionLog)).one()
    items = session.exec(
        select(SystemActionLog)
        .order_by(SystemActionLog.started_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return items, total
