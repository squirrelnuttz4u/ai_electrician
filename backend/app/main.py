"""FastAPI application entrypoint."""
from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .routers import chat, machines, prints, review, search, system

app = FastAPI(title="AI Electrician", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(system.router)
app.include_router(machines.router)
app.include_router(prints.router)
app.include_router(search.router)
app.include_router(chat.router)
app.include_router(review.router)


@app.on_event("startup")
def _startup() -> None:
    # ensure the data directory tree exists
    for sub in ("prints", "pages"):
        os.makedirs(os.path.join(settings.data_dir, sub), exist_ok=True)


@app.get("/")
def root():
    return {"name": "AI Electrician API", "docs": "/docs", "health": "/api/health"}
