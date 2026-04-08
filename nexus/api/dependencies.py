from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from nexus.database import get_session
from nexus.services.github_client import GitHubClient


async def get_db(session: AsyncSession = Depends(get_session)) -> AsyncSession:
    return session


def get_github_client(request: Request) -> GitHubClient:
    return request.app.state.github_client
