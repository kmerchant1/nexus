"""ARQ task definitions for background job processing."""

import asyncio
import logging
from datetime import datetime, timezone

from nexus.database import get_session_context
from nexus.models.db import Job

logger = logging.getLogger("nexus.worker")


async def init_repo_task(ctx: dict, job_id: str, repo_id: str) -> None:
    """Initialize a knowledge base for a newly installed repo.

    Placeholder — will be replaced with the real initialization pipeline in Sprint 2.
    """
    async with get_session_context() as session:
        job = await session.get(Job, job_id)
        if not job:
            logger.error("Job %s not found, skipping", job_id)
            return

        job.status = "running"
        job.started_at = datetime.now(timezone.utc)
        await session.commit()

        logger.info("[init_repo] Starting initialization for repo %s (job %s)", repo_id, job_id)

        # Placeholder: simulates work. Sprint 2 replaces this with the real pipeline.
        await asyncio.sleep(2)

        job.status = "completed"
        job.completed_at = datetime.now(timezone.utc)
        await session.commit()

        logger.info("[init_repo] Initialization complete for repo %s (job %s)", repo_id, job_id)


async def process_pr_task(ctx: dict, job_id: str, repo_id: str, pr_number: int) -> None:
    """Process a PR and generate wiki updates.

    Placeholder — will be replaced with the real PR update pipeline in Sprint 3.
    """
    async with get_session_context() as session:
        job = await session.get(Job, job_id)
        if not job:
            logger.error("Job %s not found, skipping", job_id)
            return

        job.status = "running"
        job.started_at = datetime.now(timezone.utc)
        await session.commit()

        logger.info(
            "[process_pr] Processing PR #%d for repo %s (job %s)",
            pr_number, repo_id, job_id,
        )

        await asyncio.sleep(2)

        job.status = "completed"
        job.completed_at = datetime.now(timezone.utc)
        await session.commit()

        logger.info(
            "[process_pr] Done processing PR #%d for repo %s (job %s)",
            pr_number, repo_id, job_id,
        )
