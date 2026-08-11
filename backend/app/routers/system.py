"""System endpoints: health, Ollama status, and (optional) login."""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import text

from .. import ollama_client
from ..auth import login
from ..config import settings
from ..database import engine

router = APIRouter(prefix="/api", tags=["system"])


@router.get("/health")
def health():
    db_ok = True
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001
        db_ok = False
    return {"status": "ok" if db_ok else "degraded", "database": db_ok}


@router.get("/ollama/status")
async def ollama_status():
    """Report whether the configured Ollama server is reachable and which of the
    configured models are pulled. Handy right after setup."""
    return await ollama_client.health()


@router.get("/config")
def public_config():
    return {"auth_enabled": settings.auth_enabled}


class LoginIn(BaseModel):
    password: str = ""


@router.post("/login")
def do_login(payload: LoginIn):
    return {"token": login(payload.password)}
