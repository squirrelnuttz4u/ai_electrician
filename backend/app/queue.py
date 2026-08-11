"""Helper to enqueue ingestion jobs onto the ARQ/Redis queue.

If Redis is unreachable (e.g. someone runs the API standalone without the
worker), we fall back to running ingestion in a background thread so uploads
still get processed.
"""
from __future__ import annotations

import threading

from arq import create_pool
from arq.connections import RedisSettings

from .config import settings
from .indexing import ingest_print_sync


async def enqueue_ingest(print_id: int) -> str:
    try:
        pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
        await pool.enqueue_job("ingest_print_task", print_id)
        await pool.close()
        return "queued"
    except Exception:  # noqa: BLE001 - Redis down -> local thread fallback
        threading.Thread(target=ingest_print_sync, args=(print_id,), daemon=True).start()
        return "processing-inline"
