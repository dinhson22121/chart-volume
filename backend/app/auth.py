"""Bearer-token gate.

Every request (except ``/health``) must carry ``Authorization: Bearer <token>``
matching the per-launch token. Uses constant-time comparison.
"""

from __future__ import annotations

import secrets

from fastapi import Header, HTTPException, status

from app.config import get_settings


def require_token(authorization: str = Header(default="")) -> None:
    expected = get_settings().resolved_token()
    prefix = "Bearer "
    token = authorization[len(prefix):] if authorization.startswith(prefix) else ""
    if not token or not secrets.compare_digest(token, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API token",
        )
