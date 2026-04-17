import asyncio
import base64
import logging
import time
from pathlib import Path

import httpx
import jwt
from pydantic import BaseModel

from nexus.config import settings

logger = logging.getLogger("nexus.github")

GITHUB_API_BASE = "https://api.github.com"


class TreeEntry(BaseModel):
    """A single entry returned by the Git Trees API."""

    path: str
    sha: str
    size: int = 0
    type: str  # "blob" or "tree"
    mode: str = ""


class GitHubClient:
    """GitHub App API client with JWT auth and installation token management."""

    def __init__(
        self,
        app_id: str | None = None,
        private_key_path: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.app_id = app_id or settings.github_app_id
        key_path = private_key_path or settings.github_private_key_path
        self._private_key = Path(key_path).read_text()
        self._installation_tokens: dict[int, tuple[str, float]] = {}
        self._http = httpx.AsyncClient(
            base_url=GITHUB_API_BASE,
            headers={
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=30.0,
            transport=transport,
        )

    def _generate_jwt(self) -> str:
        """Generate a short-lived JWT for GitHub App authentication."""
        now = int(time.time())
        payload = {
            "iat": now - 60,
            "exp": now + (10 * 60),
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

    async def _request_with_retry(
        self,
        method: str,
        url: str,
        installation_id: int,
        *,
        max_retries: int = 3,
        **kwargs,
    ) -> httpx.Response:
        """Authenticated request with exponential backoff on 403/429.

        - 403: GitHub returns this for both token expiry and secondary rate limits.
          We invalidate the cached token so the next attempt mints a fresh one.
        - 429: honor Retry-After when present, otherwise fall back to exponential backoff.
        """
        delay = 1.0
        last_response: httpx.Response | None = None
        for attempt in range(max_retries + 1):
            token = await self.get_installation_token(installation_id)
            response = await self._http.request(
                method,
                url,
                headers={"Authorization": f"token {token}"},
                **kwargs,
            )
            last_response = response

            if response.status_code in (403, 429) and attempt < max_retries:
                if response.status_code == 403:
                    self._installation_tokens.pop(installation_id, None)

                retry_after = response.headers.get("Retry-After")
                sleep_for: float
                if retry_after:
                    try:
                        sleep_for = float(retry_after)
                    except ValueError:
                        sleep_for = delay
                else:
                    reset_raw = response.headers.get("X-RateLimit-Reset")
                    if reset_raw:
                        try:
                            sleep_for = max(1.0, float(reset_raw) - time.time())
                        except ValueError:
                            sleep_for = delay
                    else:
                        sleep_for = delay

                logger.warning(
                    "GitHub %s %s returned %d; retrying in %.1fs (attempt %d/%d)",
                    method,
                    url,
                    response.status_code,
                    sleep_for,
                    attempt + 1,
                    max_retries,
                )
                await asyncio.sleep(sleep_for)
                delay *= 2
                continue

            response.raise_for_status()
            return response

        assert last_response is not None
        last_response.raise_for_status()
        return last_response

    async def _request(
        self,
        method: str,
        url: str,
        installation_id: int,
        **kwargs,
    ) -> httpx.Response:
        """Back-compat wrapper around the retry-aware request."""
        return await self._request_with_retry(method, url, installation_id, **kwargs)

    async def list_tree_prefix(
        self,
        owner: str,
        repo: str,
        ref: str,
        installation_id: int,
        *,
        prefix: str = ".nexus/raw/",
    ) -> list[TreeEntry]:
        """List every blob under ``prefix`` at ``ref`` via the recursive Trees API.

        Resolves the branch ref to its tree SHA via the Branches API, then fetches
        the full recursive tree and filters in Python. Logs a warning if GitHub
        returns a truncated tree (>100K entries or >7MB payload).
        """
        branch_resp = await self._request_with_retry(
            "GET",
            f"/repos/{owner}/{repo}/branches/{ref}",
            installation_id,
        )
        branch_data = branch_resp.json()
        try:
            tree_sha = branch_data["commit"]["commit"]["tree"]["sha"]
        except (KeyError, TypeError) as exc:
            raise ValueError(
                f"Unexpected branch payload for {owner}/{repo}@{ref}: missing tree sha"
            ) from exc

        tree_resp = await self._request_with_retry(
            "GET",
            f"/repos/{owner}/{repo}/git/trees/{tree_sha}",
            installation_id,
            params={"recursive": 1},
        )
        tree_data = tree_resp.json()
        if tree_data.get("truncated"):
            logger.warning(
                "Recursive tree for %s/%s@%s was truncated by GitHub; "
                "some files may be missing",
                owner,
                repo,
                ref,
            )

        normalized_prefix = prefix.rstrip("/") + "/" if prefix else ""
        entries: list[TreeEntry] = []
        for item in tree_data.get("tree", []):
            if item.get("type") != "blob":
                continue
            path = item.get("path", "")
            if normalized_prefix and not path.startswith(normalized_prefix):
                continue
            entries.append(
                TreeEntry(
                    path=path,
                    sha=item.get("sha", ""),
                    size=int(item.get("size", 0) or 0),
                    type=item.get("type", "blob"),
                    mode=item.get("mode", ""),
                )
            )
        entries.sort(key=lambda e: e.path)
        return entries

    async def get_file_content(
        self,
        owner: str,
        repo: str,
        path: str,
        ref: str,
        installation_id: int,
    ) -> bytes:
        """Fetch a single file's content via the Contents API.

        Files up to 1MB are returned base64-encoded inline; larger blobs arrive
        with an empty content field and we fall back to the Git Blobs API using
        the SHA from the Contents response.
        """
        response = await self._request_with_retry(
            "GET",
            f"/repos/{owner}/{repo}/contents/{path}",
            installation_id,
            params={"ref": ref},
        )
        data = response.json()
        if isinstance(data, list):
            raise ValueError(f"Path {path} refers to a directory, not a file")

        encoding = data.get("encoding", "")
        content = data.get("content", "") or ""
        if encoding == "base64" and content:
            return base64.b64decode(content)

        if not content and data.get("sha") and int(data.get("size", 0) or 0) > 0:
            blob_resp = await self._request_with_retry(
                "GET",
                f"/repos/{owner}/{repo}/git/blobs/{data['sha']}",
                installation_id,
            )
            blob_data = blob_resp.json()
            blob_encoding = blob_data.get("encoding", "base64")
            blob_content = blob_data.get("content", "") or ""
            if blob_encoding == "base64":
                return base64.b64decode(blob_content)
            return blob_content.encode("utf-8")

        return content.encode("utf-8")

    async def get_file_contents_batch(
        self,
        owner: str,
        repo: str,
        paths: list[str],
        ref: str,
        installation_id: int,
        *,
        concurrency: int = 8,
    ) -> dict[str, bytes]:
        """Fetch many files in parallel, capped by a semaphore.

        Failures for individual paths are logged and skipped; the returned dict
        contains only paths that fetched successfully.
        """
        if not paths:
            return {}

        semaphore = asyncio.Semaphore(concurrency)

        async def _fetch(path: str) -> tuple[str, bytes | BaseException]:
            async with semaphore:
                try:
                    content = await self.get_file_content(
                        owner, repo, path, ref, installation_id
                    )
                    return path, content
                except BaseException as exc:  # noqa: BLE001
                    return path, exc

        results = await asyncio.gather(*(_fetch(p) for p in paths))
        out: dict[str, bytes] = {}
        for path, value in results:
            if isinstance(value, BaseException):
                logger.error("Failed to fetch %s: %s", path, value)
                continue
            out[path] = value
        return out

    async def close(self):
        await self._http.aclose()
