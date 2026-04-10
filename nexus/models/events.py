"""Pydantic models for GitHub webhook payloads."""

from __future__ import annotations

from pydantic import BaseModel


class AccountPayload(BaseModel):
    login: str
    type: str  # "Organization" or "User"


class RepoPayload(BaseModel):
    id: int
    name: str
    full_name: str
    default_branch: str = "main"


class InstallationPayload(BaseModel):
    id: int
    account: AccountPayload


class InstallationEvent(BaseModel):
    action: str  # "created", "deleted", "suspend", "unsuspend"
    installation: InstallationPayload
    repositories: list[RepoPayload] | None = None


class InstallationRef(BaseModel):
    id: int


class BranchRef(BaseModel):
    ref: str
    sha: str


class PullRequestPayload(BaseModel):
    title: str
    body: str | None = None
    base: BranchRef
    head: BranchRef
    merged: bool = False


class PullRequestEvent(BaseModel):
    action: str  # "opened", "synchronize", "closed"
    number: int
    pull_request: PullRequestPayload
    repository: RepoPayload
    installation: InstallationRef
