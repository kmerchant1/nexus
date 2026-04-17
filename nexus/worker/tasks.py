"""ARQ task definitions for background job processing."""

import asyncio
import logging
from datetime import datetime, timezone

from nexus.database import get_session_context
from nexus.models.db import Installation, Job, Repo
from nexus.services.github_client import GitHubClient

logger = logging.getLogger("nexus.worker")

# Sprint 2A scaffolding: how many file previews to log from the new read client.
# This will be replaced by the real raw ingester in 2C.
_INIT_PREVIEW_COUNT = 3
_INIT_PREVIEW_BYTES = 160


async def init_repo_task(ctx: dict, job_id: str, repo_id: str) -> None:
    """Initialize a knowledge base for a newly installed repo.

    Sprint 2A scaffolding: exercises the new GitHub read client by listing
    ``.nexus/raw/**`` and logging a short preview for the first few files.
    Sprint 2C/2E will replace the body with the real ingest + compile pipeline.
    """
    github_client: GitHubClient | None = ctx.get("github_client")

    async with get_session_context() as session:
        job = await session.get(Job, job_id)
        if not job:
            logger.error("Job %s not found, skipping", job_id)
            return

        job.status = "running"
        job.started_at = datetime.now(timezone.utc)
        await session.commit()

        try:
            if github_client is None:
                raise RuntimeError(
                    "GitHub client not configured in worker context; "
                    "set GITHUB_APP_ID and GITHUB_PRIVATE_KEY_PATH"
                )

            repo = await session.get(Repo, repo_id)
            if not repo:
                raise RuntimeError(f"Repo {repo_id} not found")
            installation = await session.get(Installation, repo.installation_id)
            if not installation:
                raise RuntimeError(f"Installation {repo.installation_id} not found")

            owner, name = repo.full_name.split("/", 1)
            logger.info(
                "[init_repo] Listing .nexus/raw/ for %s (ref=%s, installation=%d, job=%s)",
                repo.full_name,
                repo.default_branch,
                installation.github_installation_id,
                job_id,
            )

            entries = await github_client.list_tree_prefix(
                owner=owner,
                repo=name,
                ref=repo.default_branch,
                installation_id=installation.github_installation_id,
            )
            logger.info(
                "[init_repo] Found %d file(s) under .nexus/raw/ for %s",
                len(entries),
                repo.full_name,
            )
            for entry in entries:
                logger.info(
                    "[init_repo]   %s (sha=%s, size=%dB)",
                    entry.path,
                    entry.sha[:8],
                    entry.size,
                )

            if entries:
                preview_paths = [e.path for e in entries[:_INIT_PREVIEW_COUNT]]
                contents = await github_client.get_file_contents_batch(
                    owner=owner,
                    repo=name,
                    paths=preview_paths,
                    ref=repo.default_branch,
                    installation_id=installation.github_installation_id,
                )
                for path in preview_paths:
                    raw = contents.get(path)
                    if raw is None:
                        logger.warning("[init_repo]   preview[%s]: <fetch failed>", path)
                        continue
                    preview = raw[:_INIT_PREVIEW_BYTES].decode("utf-8", errors="replace")
                    preview = preview.replace("\n", " \u21b5 ")
                    logger.info(
                        "[init_repo]   preview[%s] (%dB): %s%s",
                        path,
                        len(raw),
                        preview,
                        "..." if len(raw) > _INIT_PREVIEW_BYTES else "",
                    )
            else:
                logger.info(
                    "[init_repo] No .nexus/raw/ folder found in %s -- nothing to ingest yet",
                    repo.full_name,
                )

            job.status = "completed"
            job.completed_at = datetime.now(timezone.utc)
            await session.commit()
            logger.info(
                "[init_repo] Initialization complete for %s (job %s)",
                repo.full_name,
                job_id,
            )
        except Exception as exc:
            logger.exception("[init_repo] Failed for repo %s: %s", repo_id, exc)
            job.status = "failed"
            job.error_message = str(exc)[:1000]
            job.completed_at = datetime.now(timezone.utc)
            await session.commit()
            raise


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
