"""Symmetric encryption for secrets at rest (Fernet).

The encryption key comes from env ``SETTINGS_KEY`` (64 hex chars = 32 bytes,
injected by Electron from the OS keychain via safeStorage) or, for standalone
dev, a ``settings.key`` file created next to the SQLite DB (chmod 600).
"""

from __future__ import annotations

import base64
import logging
import os
import secrets
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings

logger = logging.getLogger("chart_volume.crypto")

_ENC_PREFIX = "enc:"


def _key_file_path() -> str:
    db_dir = os.path.dirname(get_settings().db_path) or "."
    return os.path.join(db_dir, "settings.key")


def _load_or_create_hex_key() -> str:
    env_key = os.environ.get("SETTINGS_KEY")
    if env_key:
        return env_key.strip()

    path = _key_file_path()
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            return fh.read().strip()

    hex_key = secrets.token_hex(32)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(hex_key)
    os.chmod(path, 0o600)
    return hex_key


@lru_cache
def _fernet() -> Fernet:
    key_bytes = bytes.fromhex(_load_or_create_hex_key())
    return Fernet(base64.urlsafe_b64encode(key_bytes))


def encrypt(plaintext: str) -> str:
    if not plaintext:
        return ""
    return _ENC_PREFIX + _fernet().encrypt(plaintext.encode()).decode()


def decrypt(value: str) -> str:
    if not value:
        return ""
    if not value.startswith(_ENC_PREFIX):
        return value  # tolerate legacy/plaintext values
    try:
        return _fernet().decrypt(value[len(_ENC_PREFIX):].encode()).decode()
    except (InvalidToken, ValueError):
        # Wrong/rotated key (e.g. settings-key.enc got regenerated after
        # failing to decrypt on the Electron side) -- surface it instead of
        # silently returning empty, since the caller otherwise has no way to
        # tell "no key configured" apart from "key configured but unreadable".
        logger.warning("could not decrypt a stored secret -- encryption key may have changed")
        return ""
