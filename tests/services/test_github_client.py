"""Unit tests for the GitHub read client (Sprint 2A).

The tests mock transport via ``httpx.MockTransport`` and prime the installation
token cache so no JWT / token exchange is performed.
"""

from __future__ import annotations

import base64
import time
from pathlib import Path

import httpx
import pytest

from nexus.services.github_client import GitHubClient, TreeEntry


@pytest.fixture
def dummy_key(tmp_path: Path) -> Path:
    # The private key is read on __init__ but never used by these tests
    # because we prime the token cache directly.
    key = tmp_path / "private-key.pem"
    key.write_text("dummy-not-a-real-key")
    return key


def _build_client(
    handler,
    dummy_key: Path,
    *,
    installation_id: int = 999,
) -> GitHubClient:
    transport = httpx.MockTransport(handler)
    client = GitHubClient(
        app_id="12345",
        private_key_path=str(dummy_key),
        transport=transport,
    )
    # Prime the token cache to bypass JWT signing.
    client._installation_tokens[installation_id] = ("test-token", time.time() + 3600)
    return client


async def test_list_tree_prefix_filters_and_sorts(dummy_key: Path):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/repos/acme/backend/branches/main":
            return httpx.Response(
                200,
                json={"commit": {"commit": {"tree": {"sha": "tree-sha-abc"}}}},
            )
        if request.url.path == "/repos/acme/backend/git/trees/tree-sha-abc":
            assert request.url.params.get("recursive") == "1"
            return httpx.Response(
                200,
                json={
                    "truncated": False,
                    "tree": [
                        {"path": "README.md", "sha": "r1", "type": "blob", "size": 10, "mode": "100644"},
                        {"path": ".nexus/raw", "sha": "d1", "type": "tree", "mode": "040000"},
                        {"path": ".nexus/raw/overview.md", "sha": "o1", "type": "blob", "size": 42, "mode": "100644"},
                        {"path": ".nexus/raw/decisions/a.md", "sha": "a1", "type": "blob", "size": 7, "mode": "100644"},
                        {"path": ".nexus/other/note.md", "sha": "x1", "type": "blob", "size": 3, "mode": "100644"},
                    ],
                },
            )
        raise AssertionError(f"unexpected URL: {request.url}")

    client = _build_client(handler, dummy_key)
    try:
        entries = await client.list_tree_prefix(
            owner="acme", repo="backend", ref="main", installation_id=999
        )
    finally:
        await client.close()

    assert [e.path for e in entries] == [
        ".nexus/raw/decisions/a.md",
        ".nexus/raw/overview.md",
    ]
    assert entries[0] == TreeEntry(
        path=".nexus/raw/decisions/a.md", sha="a1", size=7, type="blob", mode="100644"
    )


async def test_list_tree_prefix_warns_on_truncation(dummy_key: Path, caplog):
    def handler(request: httpx.Request) -> httpx.Response:
        if "/branches/" in request.url.path:
            return httpx.Response(200, json={"commit": {"commit": {"tree": {"sha": "t"}}}})
        return httpx.Response(
            200,
            json={
                "truncated": True,
                "tree": [
                    {"path": ".nexus/raw/a.md", "sha": "s1", "type": "blob", "size": 1, "mode": "100644"},
                ],
            },
        )

    client = _build_client(handler, dummy_key)
    try:
        with caplog.at_level("WARNING", logger="nexus.github"):
            entries = await client.list_tree_prefix(
                owner="o", repo="r", ref="main", installation_id=999
            )
    finally:
        await client.close()

    assert len(entries) == 1
    assert any("truncated" in rec.message for rec in caplog.records)


async def test_get_file_content_base64_decodes(dummy_key: Path):
    body = "hello nexus\n".encode()
    encoded = base64.b64encode(body).decode()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/repos/o/r/contents/.nexus/raw/a.md"
        assert request.url.params.get("ref") == "main"
        return httpx.Response(
            200,
            json={
                "content": encoded,
                "encoding": "base64",
                "sha": "abc",
                "size": len(body),
            },
        )

    client = _build_client(handler, dummy_key)
    try:
        content = await client.get_file_content(
            owner="o", repo="r", path=".nexus/raw/a.md", ref="main", installation_id=999
        )
    finally:
        await client.close()

    assert content == body


async def test_get_file_content_falls_back_to_blobs_for_large_file(dummy_key: Path):
    big = b"x" * 2048
    encoded = base64.b64encode(big).decode()

    def handler(request: httpx.Request) -> httpx.Response:
        if "/contents/" in request.url.path:
            return httpx.Response(
                200,
                json={"content": "", "encoding": "", "sha": "blob-sha", "size": len(big)},
            )
        if "/git/blobs/blob-sha" in request.url.path:
            return httpx.Response(
                200,
                json={"content": encoded, "encoding": "base64", "sha": "blob-sha", "size": len(big)},
            )
        raise AssertionError(f"unexpected URL: {request.url}")

    client = _build_client(handler, dummy_key)
    try:
        content = await client.get_file_content(
            owner="o", repo="r", path="big.md", ref="main", installation_id=999
        )
    finally:
        await client.close()

    assert content == big


async def test_get_file_contents_batch_parallel(dummy_key: Path):
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path.rsplit("/", 1)[-1]
        body = f"body-{path}".encode()
        return httpx.Response(
            200,
            json={
                "content": base64.b64encode(body).decode(),
                "encoding": "base64",
                "sha": f"sha-{path}",
                "size": len(body),
            },
        )

    client = _build_client(handler, dummy_key)
    try:
        out = await client.get_file_contents_batch(
            owner="o",
            repo="r",
            paths=["a.md", "b.md", "c.md"],
            ref="main",
            installation_id=999,
        )
    finally:
        await client.close()

    assert out == {"a.md": b"body-a.md", "b.md": b"body-b.md", "c.md": b"body-c.md"}


async def test_get_file_contents_batch_skips_failed_paths(dummy_key: Path):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/bad.md"):
            return httpx.Response(404, json={"message": "Not Found"})
        return httpx.Response(
            200,
            json={
                "content": base64.b64encode(b"ok").decode(),
                "encoding": "base64",
                "sha": "s",
                "size": 2,
            },
        )

    client = _build_client(handler, dummy_key)
    try:
        out = await client.get_file_contents_batch(
            owner="o",
            repo="r",
            paths=["good.md", "bad.md"],
            ref="main",
            installation_id=999,
        )
    finally:
        await client.close()

    assert "good.md" in out
    assert "bad.md" not in out


async def test_request_with_retry_retries_on_429(dummy_key: Path, monkeypatch):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "0"}, json={})
        return httpx.Response(
            200,
            json={"commit": {"commit": {"tree": {"sha": "t"}}}},
        )

    client = _build_client(handler, dummy_key)

    async def _instant_sleep(_):
        return None

    monkeypatch.setattr("nexus.services.github_client.asyncio.sleep", _instant_sleep)

    try:
        resp = await client._request_with_retry(
            "GET", "/repos/o/r/branches/main", installation_id=999
        )
    finally:
        await client.close()

    assert resp.status_code == 200
    assert calls["n"] == 2


async def test_request_with_retry_invalidates_token_on_403(dummy_key: Path, monkeypatch):
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(403, json={"message": "Bad credentials"})
        if request.url.path.endswith("/access_tokens"):
            return httpx.Response(
                201,
                json={"token": "refreshed-token", "expires_at": "2099-01-01T00:00:00Z"},
            )
        return httpx.Response(200, json={"ok": True})

    client = _build_client(handler, dummy_key)
    # Monkeypatch JWT generation so we don't need a real key during the refresh.
    monkeypatch.setattr(client, "_generate_jwt", lambda: "fake-jwt")

    async def _instant_sleep(_):
        return None

    monkeypatch.setattr("nexus.services.github_client.asyncio.sleep", _instant_sleep)

    try:
        resp = await client._request_with_retry(
            "GET", "/repos/o/r/branches/main", installation_id=999
        )
    finally:
        await client.close()

    assert resp.status_code == 200
    assert calls["n"] >= 2
    # Cache should now hold the refreshed token.
    assert client._installation_tokens[999][0] == "refreshed-token"
