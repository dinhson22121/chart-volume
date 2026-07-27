"""Stock batch analysis: manual "run analysis now for the whole tracked
stock universe" trigger, status, cancel.

Refreshing the HOSE+HNX universe + ingesting + analysing potentially hundreds
of tickers takes minutes, so the manual trigger runs in a background task and
returns immediately; the UI polls /stock-batch-analysis/status for progress,
the same way crypto's screener/potential screener do.
"""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends
from sqlmodel import Session

from app.auth import require_token
from app.db import get_engine
from app.services import stock_batch_analysis

router = APIRouter(
    prefix="/stock-batch-analysis", tags=["stock-batch-analysis"], dependencies=[Depends(require_token)]
)


def _run_task() -> None:
    # Background tasks outlive the request, so they need their own session --
    # a multi-minute run can't reuse the request-scoped session dependency.
    with Session(get_engine()) as session:
        stock_batch_analysis.run_full_universe_analysis(session)


@router.post("/run")
def trigger_run(background_tasks: BackgroundTasks) -> dict:
    status = stock_batch_analysis.get_status()
    if status["running"]:
        return {"status": "already_running"}
    background_tasks.add_task(_run_task)
    return {"status": "started"}


@router.post("/cancel")
def cancel_run() -> dict:
    return stock_batch_analysis.request_cancel()


@router.get("/status")
def get_status() -> dict:
    return stock_batch_analysis.get_status()
