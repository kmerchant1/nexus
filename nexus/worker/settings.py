"""ARQ worker configuration."""

import logging
from pathlib import Path

from arq.connections import RedisSettings

from nexus.config import settings
from nexus.services.github_client import GitHubClient
from nexus.worker.tasks import init_repo_task, process_pr_task

# ARQ doesn't configure Python's root logger, so our application log lines
# (nexus.worker, nexus.github, etc.) would otherwise be filtered out at the
# default root level of WARNING. Mirror the app-side logging setup here so
# worker logs actually show up in `docker compose logs worker`.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)

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
    if settings.github_app_id and Path(settings.github_private_key_path).exists():
        ctx["github_client"] = GitHubClient()
        logger.info("Worker GitHub client initialized (app_id=%s)", settings.github_app_id)
    else:
        ctx["github_client"] = None
        logger.warning("Worker GitHub client not configured -- tasks needing GH will fail")
    logger.info("Worker started")


async def shutdown(ctx: dict):
    client = ctx.get("github_client")
    if client is not None:
        await client.close()
        logger.info("Worker GitHub client closed")
    logger.info("Worker shutting down")


class WorkerSettings:
    functions = [init_repo_task, process_pr_task]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = parse_redis_url(settings.redis_url)
