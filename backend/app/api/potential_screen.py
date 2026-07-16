"""AI-only growth-potential screener: manual run trigger, status, results.

A run makes one real AI call per 10-ticker batch across the whole tracked
universe, so it runs as a background task (like crypto_screener) and the UI
polls /potential-screen/status for progress.
"""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends
from sqlmodel import Session

from app.auth import require_token
from app.db import get_engine, get_session
from app.services import potential_screener

router = APIRouter(prefix="/potential-screen", tags=["potential-screen"], dependencies=[Depends(require_token)])


def _run_task() -> None:
    with Session(get_engine()) as session:
        potential_screener.run_potential_screen(session)


@router.post("/run")
def trigger_run(background_tasks: BackgroundTasks) -> dict:
    status = potential_screener.get_status()
    if status["running"]:
        return {"status": "already_running"}
    background_tasks.add_task(_run_task)
    return {"status": "started"}


@router.get("/status")
def get_status() -> dict:
    return potential_screener.get_status()


@router.get("/results")
def get_results(session: Session = Depends(get_session)) -> list[dict]:
    return potential_screener.get_results(session)
