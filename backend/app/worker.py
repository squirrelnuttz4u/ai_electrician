"""ARQ background worker.

Runs heavy ingestion (PDF render + OCR + vision extraction + embeddings) off the
API request path. Start with:  arq app.worker.WorkerSettings
"""
from __future__ import annotations

from arq.connections import RedisSettings

from .config import settings
from .indexing import ingest_print


async def ingest_print_task(ctx, print_id: int) -> None:
    await ingest_print(print_id)


class WorkerSettings:
    functions = [ingest_print_task]
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    # vision extraction is slow; give jobs plenty of headroom
    job_timeout = 60 * 60
    max_jobs = 2
    keep_result = 3600
