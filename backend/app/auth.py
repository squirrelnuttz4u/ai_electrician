"""Optional, minimal LAN auth.

When AUTH_ENABLED=false (default) every request is allowed — appropriate for a
trusted shop LAN. When enabled, a single shared password mints a signed token
that the frontend stores and sends as a Bearer header.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

from .config import settings

_bearer = HTTPBearer(auto_error=False)
_ALGO = "HS256"


def create_token() -> str:
    payload = {"sub": "shop", "exp": datetime.now(timezone.utc) + timedelta(days=30)}
    return jwt.encode(payload, settings.auth_secret, algorithm=_ALGO)


def login(password: str) -> str:
    if not settings.auth_enabled:
        return create_token()
    if password != settings.auth_shared_password:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid password")
    return create_token()


def require_auth(creds: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> None:
    if not settings.auth_enabled:
        return
    if creds is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    try:
        jwt.decode(creds.credentials, settings.auth_secret, algorithms=[_ALGO])
    except JWTError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token") from exc
