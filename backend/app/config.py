"""Application configuration loaded from environment / .env.

Electron mints ``LOCAL_API_TOKEN`` per launch and passes it via env; when the
backend is started standalone (dev) without one, a random token is generated so
the auth gate is always active. The value is logged at startup for dev use.
"""

from __future__ import annotations

import secrets
from functools import lru_cache

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
