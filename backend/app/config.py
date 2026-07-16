"""Application configuration loaded from environment / .env.

Electron mints ``LOCAL_API_TOKEN`` per launch and passes it via env; when the
backend is started standalone (dev) without one, a random token is generated so
the auth gate is always active. Never logged -- it's the only thing gating
this API, so leaking it into logs would defeat the auth model entirely. When
testing standalone without Electron, read it back via ``get_settings().resolved_token()``
in-process, or set ``LOCAL_API_TOKEN`` explicitly yourself before starting uvicorn.
"""

from __future__ import annotations

import secrets
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# VN market session boundaries (local time, Asia/Ho_Chi_Minh).
TIMEZONE = "Asia/Ho_Chi_Minh"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # Loopback bind — never expose beyond localhost.
    host: str = "127.0.0.1"
    port: int = 8787

    # Per-launch shared secret between Electron and this backend.
    local_api_token: str = ""

    # Anthropic — required before the narrative step (milestone 5).
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-5"

    # Gemini / Google Antigravity
    gemini_api_key: str = ""

    # OpenAI / Codex
    openai_api_key: str = ""
    openai_model: str = "gpt-5"

    # SQLite file location.
    db_path: str = "chart_volume.db"

    def resolved_token(self) -> str:
        """Return the configured token, generating a dev one if unset."""
        if not self.local_api_token:
            object.__setattr__(self, "local_api_token", secrets.token_urlsafe(24))
        return self.local_api_token


@lru_cache
def get_settings() -> Settings:
    return Settings()


def log_file_path() -> Path:
    """Backend technical log lives next to the SQLite DB (same userData dir
    in the packaged app as license.json/settings-key.enc) -- one shared
    source of truth read by both main.py (writes) and api.logs (reads back
    for export). Named after the DB file's own stem (not a fixed literal) so
    a test run's DB_PATH=test_chart_volume.db gets its own
    test_chart_volume.log instead of colliding with real dev-mode logs."""
    db_path = Path(get_settings().db_path).resolve()
    return db_path.with_suffix(".log")
