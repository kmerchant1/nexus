import logging
import time
from pathlib import Path

import httpx
import jwt

from nexus.config import settings

logger = logging.getLogger("nexus.github")

GITHUB_API_BASE = "https://api.github.com"


class GitHubClient:
    """GitHub App API client with JWT auth and installation token management."""

    def __init__(
        self,
        app_id: str = settings.github_app_id,
        private_key_path: str = settings.github_private_key_path,
    ):
        self.app_id = app_id
        self._private_key = Path(private_key_path).read_text()
        self._installation_tokens: dict[int, tuple[str, float]] = {}
        self._http = httpx.AsyncClient(
            base_url=GITHUB_API_BASE,
            headers={
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=30.0,
        )

    def _generate_jwt(self) -> str:
        """Generate a short-lived JWT for GitHub App authentication."""
        now = int(time.time())
        payload = {
            "iat": now - 60,  # issued-at, 60s in the past for clock drift
            "exp": now + (10 * 60),  # expires in 10 minutes (GitHub max)
            "iss": self.app_id,
        }
        return jwt.encode(payload, self._private_key, algorithm="RS256")

    async def get_installation_token(self, installation_id: int) -> str:
        """Get an installation access token, using cache if still valid."""
        cached = self._installation_tokens.get(installation_id)
        if cached:
            token, expires_at = cached
            if time.time() < expires_at - 60:
                return token

        app_jwt = self._generate_jwt()
        response = await self._http.post(
            f"/app/installations/{installation_id}/access_tokens",
            headers={"Authorization": f"Bearer {app_jwt}"},
        )
        response.raise_for_status()
        data = response.json()

        token = data["token"]
        # GitHub tokens expire in 1 hour; cache with buffer
        expires_at = time.time() + 3500
        self._installation_tokens[installation_id] = (token, expires_at)
        logger.info("Obtained installation token for installation %d", installation_id)
        return token

    async def get_authenticated_app(self) -> dict:
        """Call GET /app to verify the App credentials are valid."""
        app_jwt = self._generate_jwt()
        response = await self._http.get(
            "/app",
            headers={"Authorization": f"Bearer {app_jwt}"},
        )
        response.raise_for_status()
        return response.json()

    async def _request(
        self,
        method: str,
        url: str,
        installation_id: int,
        **kwargs,
    ) -> httpx.Response:
        """Make an authenticated API request using an installation token."""
        token = await self.get_installation_token(installation_id)
        response = await self._http.request(
            method,
            url,
            headers={"Authorization": f"token {token}"},
            **kwargs,
        )
        response.raise_for_status()
        return response

    async def close(self):
        await self._http.aclose()
