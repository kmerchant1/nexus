"""POST /webhooks/github — receive and process GitHub App webhook events."""

import hashlib
import hmac
import logging

from arq import ArqRedis
from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from nexus.api.dependencies import get_arq_pool, get_db
from nexus.config import settings
from nexus.models.db import Installation, Job, Repo
from nexus.models.events import InstallationEvent, PullRequestEvent

logger = logging.getLogger("nexus.webhooks")

router = APIRouter()


def _verify_signature(payload: bytes, signature_header: str) -> None:
    """Validate the X-Hub-Signature-256 HMAC sent by GitHub."""
    if not signature_header.startswith("sha256="):
        raise HTTPException(status_code=400, detail="Invalid signature format")

    expected = hmac.new(
        settings.github_webhook_secret.encode(),
        payload,
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(f"sha256={expected}", signature_header):
        raise HTTPException(status_code=401, detail="Invalid signature")


async def _handle_installation_created(
    event: InstallationEvent, db: AsyncSession, arq_pool: ArqRedis
) -> dict:
    result = await db.execute(
        select(Installation).where(
            Installation.github_installation_id == event.installation.id
        )
    )
    installation = result.scalar_one_or_none()

    if installation:
        installation.account_login = event.installation.account.login
        installation.account_type = event.installation.account.type
        installation.status = "active"
        logger.info(
            "Installation %d already exists, refreshing",
            event.installation.id,
        )
    else:
        installation = Installation(
            github_installation_id=event.installation.id,
            account_login=event.installation.account.login,
            account_type=event.installation.account.type,
            status="active",
        )
        db.add(installation)
        await db.flush()

    repos_created = 0
    jobs_created = 0
    for repo_payload in event.repositories or []:
        repo_result = await db.execute(
            select(Repo).where(Repo.github_repo_id == repo_payload.id)
        )
        repo = repo_result.scalar_one_or_none()

        if repo:
            repo.full_name = repo_payload.full_name
            repo.default_branch = repo_payload.default_branch
            repo.installation_id = installation.id
        else:
            repo = Repo(
                installation_id=installation.id,
                github_repo_id=repo_payload.id,
                full_name=repo_payload.full_name,
                default_branch=repo_payload.default_branch,
                status="pending_init",
            )
            db.add(repo)
            await db.flush()

            job = Job(
                repo_id=repo.id,
                job_type="init",
                trigger_ref="installation",
                status="queued",
            )
            db.add(job)
            await db.flush()
            await arq_pool.enqueue_job(
                "init_repo_task",
                job_id=str(job.id),
                repo_id=str(repo.id),
            )
            logger.info("Enqueued init_repo job %s for repo %s", job.id, repo.full_name)
            jobs_created += 1

        repos_created += 1

    await db.commit()

    logger.info(
        "Saved installation %d (%s) with %d repo(s), %d new job(s)",
        event.installation.id,
        event.installation.account.login,
        repos_created,
        jobs_created,
    )
    return {
        "status": "accepted",
        "installation_id": event.installation.id,
        "repos_created": repos_created,
        "jobs_created": jobs_created,
    }


async def _handle_installation_deleted(
    event: InstallationEvent, db: AsyncSession
) -> dict:
    result = await db.execute(
        select(Installation).where(
            Installation.github_installation_id == event.installation.id
        )
    )
    installation = result.scalar_one_or_none()
    if installation:
        installation.status = "deleted"
        await db.commit()
        logger.info("Marked installation %d as deleted", event.installation.id)
    else:
        logger.warning("Received delete for unknown installation %d", event.installation.id)

    return {"status": "accepted", "action": "deleted"}


async def _handle_pull_request(event: PullRequestEvent, db: AsyncSession, arq_pool: ArqRedis) -> dict:
    if event.action not in ("opened", "synchronize"):
        logger.info("Ignoring pull_request action=%s for PR #%d", event.action, event.number)
        return {"status": "ignored", "reason": f"action={event.action}"}

    result = await db.execute(
        select(Repo).where(Repo.github_repo_id == event.repository.id)
    )
    repo = result.scalar_one_or_none()
    if not repo:
        logger.warning(
            "Received PR event for unknown repo %s (id=%d)",
            event.repository.full_name,
            event.repository.id,
        )
        return {"status": "ignored", "reason": "unknown repo"}

    job = Job(
        repo_id=repo.id,
        job_type="pr_update",
        trigger_ref=f"PR #{event.number}",
        status="queued",
    )
    db.add(job)
    await db.flush()
    await arq_pool.enqueue_job(
        "process_pr_task",
        job_id=str(job.id),
        repo_id=str(repo.id),
        pr_number=event.number,
    )
    await db.commit()

    logger.info(
        "Enqueued pr_update job %s for %s PR #%d",
        job.id,
        event.repository.full_name,
        event.number,
    )
    return {"status": "accepted", "job_type": "pr_update", "pr_number": event.number}


@router.post("/webhooks/github")
async def github_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
    arq_pool: ArqRedis = Depends(get_arq_pool),
    x_github_event: str = Header(...),
    x_github_delivery: str = Header(...),
    x_hub_signature_256: str = Header(...),
):
    body = await request.body()
    _verify_signature(body, x_hub_signature_256)

    payload = await request.json()
    logger.info("Webhook received: event=%s delivery=%s", x_github_event, x_github_delivery)

    if x_github_event == "installation":
        event = InstallationEvent(**payload)
        if event.action == "created":
            return await _handle_installation_created(event, db, arq_pool)
        if event.action == "deleted":
            return await _handle_installation_deleted(event, db)
        logger.info("Ignoring installation action=%s", event.action)
        return {"status": "ignored", "reason": f"action={event.action}"}

    if x_github_event == "pull_request":
        event = PullRequestEvent(**payload)
        return await _handle_pull_request(event, db, arq_pool)

    logger.info("Ignoring event type=%s", x_github_event)
    return {"status": "ignored", "reason": f"event={x_github_event}"}
