"""
workers/settings.py — Phase 9 ARQ Worker Settings and Configuration

Configures Redis connection, worker startup/shutdown hooks, job function registry,
and timeouts per queue.
"""
from __future__ import annotations

import asyncio
import logging
import sys
from arq.connections import RedisSettings

from config import settings

logger = logging.getLogger(__name__)


def parse_redis_url(url: str) -> RedisSettings:
    """Parse redis://host:port/db into ARQ RedisSettings."""
    from urllib.parse import urlparse
    parsed = urlparse(url)
    host = parsed.hostname or "localhost"
    port = parsed.port or 6379
    database = 0
    if parsed.path and parsed.path.strip("/").isdigit():
        database = int(parsed.path.strip("/"))
    return RedisSettings(
        host=host,
        port=port,
        database=database,
        conn_timeout=settings.redis_connect_timeout_seconds,
    )


async def startup(ctx: dict) -> None:
    logger.info("ARQ Worker starting up...")
    if sys.platform == "win32":
        # Ensure Windows event loop handles async calls cleanly
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


async def shutdown(ctx: dict) -> None:
    logger.info("ARQ Worker shutting down cleanly...")


class WorkerSettings:
    redis_settings = parse_redis_url(settings.redis_url)
    queue_name = f"{settings.redis_queue_prefix}:default"
    on_startup = startup
    on_shutdown = shutdown
    max_jobs = settings.document_worker_concurrency
    job_timeout = settings.ocr_job_timeout_seconds
    functions = []
