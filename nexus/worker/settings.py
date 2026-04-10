"""ARQ worker configuration."""

import logging

from arq.connections import RedisSettings

from nexus.config import settings
from nexus.worker.tasks import init_repo_task, process_pr_task

logger = logging.getLogger("nexus.worker")


def parse_redis_url(url: str) -> RedisSettings:
    """Convert a redis:// URL into ARQ RedisSettings."""
    url = url.replace("redis://", "")
    if ":" in url and "@" in url:
        auth, hostport = url.rsplit("@", 1)
        password = auth.split(":", 1)[1] if ":" in auth else None
    else:
        hostport = url
        password = None

    host, _, port_str = hostport.partition(":")
    port = int(port_str) if port_str else 6379

    return RedisSettings(host=host or "redis", port=port, password=password)


async def startup(ctx: dict):
    logger.info("Worker started")


async def shutdown(ctx: dict):
    logger.info("Worker shutting down")


class WorkerSettings:
    functions = [init_repo_task, process_pr_task]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = parse_redis_url(settings.redis_url)
