import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from nexus.api.health import router as health_router
from nexus.config import settings
from nexus.database import engine
from nexus.services.github_client import GitHubClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger("nexus")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Nexus starting up...")
    logger.info("Database engine initialized")

    if settings.github_app_id and Path(settings.github_private_key_path).exists():
        app.state.github_client = GitHubClient()
        logger.info("GitHub App client initialized (app_id=%s)", settings.github_app_id)
    else:
        app.state.github_client = None
        logger.warning("GitHub App credentials not configured -- client disabled")

    yield

    logger.info("Nexus shutting down...")
    if app.state.github_client:
        await app.state.github_client.close()
        logger.info("GitHub client closed")
    await engine.dispose()
    logger.info("Database engine closed")


app = FastAPI(
    title="Nexus",
    description="Autonomous LLM-powered knowledge base guardian",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(health_router)
