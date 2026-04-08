"""ARQ worker settings. Placeholder until tasks are implemented in S1.5."""

from arq.connections import RedisSettings

from nexus.config import settings


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
    pass


async def shutdown(ctx: dict):
    pass


class WorkerSettings:
    functions: list = []
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = parse_redis_url(settings.redis_url)
