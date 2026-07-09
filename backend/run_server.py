"""PyInstaller entry point: run the FastAPI app via uvicorn.

Passing the app object directly (not an import string) avoids uvicorn spawning a
reloader subprocess, which does not work inside a frozen PyInstaller bundle.
Configuration comes from env vars injected by the Electron main process.

vnstock transitively imports matplotlib (via its charting helper), which rebuilds
a font cache on first run (~85s). We redirect that cache to a persistent,
writable directory and pre-seed it from a bundled copy so startup stays fast.
"""

from __future__ import annotations

import os
import shutil
import sys


def _find_seed_dir() -> str | None:
    """Locate the bundled mpl_cache across PyInstaller layouts (onedir/onefile)."""
    candidates: list[str] = []
    mei = getattr(sys, "_MEIPASS", None)
    if mei:
        candidates += [os.path.join(mei, "mpl_cache"), os.path.join(mei, "_internal", "mpl_cache")]
    exe_dir = os.path.dirname(os.path.abspath(sys.executable))
    candidates += [os.path.join(exe_dir, "_internal", "mpl_cache"), os.path.join(exe_dir, "mpl_cache")]
    candidates.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "mpl_cache"))
    return next((c for c in candidates if os.path.isdir(c)), None)


def _prime_matplotlib_cache() -> None:
    base = os.path.dirname(os.environ.get("DB_PATH", "")) or os.getcwd()
    cache_dir = os.path.join(base, "mpl-cache")
    os.makedirs(cache_dir, exist_ok=True)
    # Force-override: PyInstaller's matplotlib runtime hook points MPLCONFIGDIR at
    # a throwaway temp dir every launch, which would rebuild the font cache (~85s)
    # each time. Pin it to our persistent, pre-seeded directory instead.
    os.environ["MPLCONFIGDIR"] = cache_dir
    target = cache_dir

    seed_dir = _find_seed_dir()
    if seed_dir:
        for name in os.listdir(seed_dir):
            dst = os.path.join(target, name)
            if not os.path.exists(dst):
                try:
                    shutil.copy2(os.path.join(seed_dir, name), dst)
                except OSError:
                    pass


_prime_matplotlib_cache()

import uvicorn  # noqa: E402

from app.main import app  # noqa: E402


def main() -> None:
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8787"))
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
