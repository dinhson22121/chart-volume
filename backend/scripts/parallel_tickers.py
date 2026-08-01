"""Runs a backtest's per-ticker body across CPU cores.

Every backtest script here has the same outer shape: loop over tickers,
and for each one load its candles, analyze, then walk its events through
one or more variants. No ticker's result depends on any other ticker's, so
that loop is embarrassingly parallel -- this module is the one place that
knows how, instead of each script growing its own copy.

Why processes and not threads: the work is pure CPU (pandas/numpy feature
engineering plus a bar-by-bar Python scan), and CPython's GIL lets only one
thread run bytecode at a time, so threads would add overhead and no speed.

Each worker process gets its OWN Session -- SQLAlchemy connections are not
safe to share across processes, and on macOS Python spawns rather than
forks anyway, so a child starts from a fresh import of app.db and builds its
own engine. Concurrent SQLite READERS are fine; nothing here writes.

Scripts patch strategy-module globals per variant (POI_ZONE_THRESHOLD_PCT,
PARTIAL_EXIT_*, ...). That stays correct here precisely because these are
processes: each has a private copy of every module, so one worker's patch
can't leak into another's run.
"""

from __future__ import annotations

import os
import sys
from concurrent.futures import ProcessPoolExecutor
from typing import Any, Callable, Iterator, Sequence

# The parent hands each worker a picklable (function, ticker, extra-args)
# payload rather than relying on inherited globals -- with spawn there is no
# inheritance, and the child re-imports __main__ from scratch.
_session: Any = None


def _init_worker() -> None:
    global _session
    from sqlmodel import Session

    from app.db import get_engine

    _session = Session(get_engine())


def _call(payload: tuple) -> tuple[str, Any]:
    worker, ticker, args = payload
    return ticker, worker(_session, ticker, *args)


def default_workers() -> int:
    """Physical cores, not logical. This work is ALU/memory-bound, so the
    second hyperthread on a core buys very little while still multiplying
    memory traffic and pandas import cost. Override with BACKTEST_WORKERS."""
    override = os.environ.get("BACKTEST_WORKERS")
    if override:
        return max(1, int(override))
    if sys.platform == "darwin":
        try:
            import subprocess

            out = subprocess.run(
                ["sysctl", "-n", "hw.physicalcpu"], capture_output=True, text=True, timeout=5
            )
            return max(1, int(out.stdout.strip()))
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
    return max(1, (os.cpu_count() or 2) // 2)


def map_tickers(
    tickers: Sequence[str],
    worker: Callable[..., Any],
    *,
    args: tuple = (),
    workers: int | None = None,
) -> Iterator[tuple[int, str, Any]]:
    """Yields ``(position, ticker, worker_result)`` in the order tickers were
    given, so a caller's aggregation is deterministic and identical to the
    serial version's.

    ``worker`` must be a module-level function (picklable by qualified name)
    taking ``(session, ticker, *args)`` and returning something picklable.
    ``args`` carries per-run config the child can't otherwise see -- with
    spawn the child re-imports the script but never runs its ``main()``, so
    anything parsed from argv has to be passed through here.

    ``workers=1`` runs everything in this process with no pool at all, which
    keeps tracebacks readable and is the fallback when debugging a worker.
    """
    n = workers if workers is not None else default_workers()

    if n <= 1:
        from sqlmodel import Session

        from app.db import get_engine

        with Session(get_engine()) as session:
            for i, ticker in enumerate(tickers, 1):
                yield i, ticker, worker(session, ticker, *args)
        return

    payloads = [(worker, ticker, args) for ticker in tickers]
    with ProcessPoolExecutor(max_workers=n, initializer=_init_worker) as pool:
        # .map preserves input order, so results arrive as the caller expects
        # even though the workers finish out of order.
        for i, (ticker, result) in enumerate(pool.map(_call, payloads), 1):
            yield i, ticker, result
