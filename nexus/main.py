import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from nexus.api.health import router as health_router
from nexus.database import engine

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger("nexus")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Nexus starting up...")
    logger.info("Database engine initialized")
    yield
    logger.info("Nexus shutting down...")
    await engine.dispose()
    logger.info("Database engine closed")


app = FastAPI(
    title="Nexus",
    description="Autonomous LLM-powered knowledge base guardian",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(health_router)
