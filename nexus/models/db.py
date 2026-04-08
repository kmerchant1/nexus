import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from nexus.database import Base


class Installation(Base):
    """A GitHub App installation (one per org/user that installs Nexus)."""

    __tablename__ = "installations"

    github_installation_id: Mapped[int] = mapped_column(unique=True, index=True)
    account_login: Mapped[str] = mapped_column(String(255))
    account_type: Mapped[str] = mapped_column(String(50))  # "Organization" or "User"
    status: Mapped[str] = mapped_column(String(50), default="active")

    repos: Mapped[list["Repo"]] = relationship(
        back_populates="installation", cascade="all, delete-orphan"
    )


class Repo(Base):
    """A repo connected to an installation."""

    __tablename__ = "repos"

    installation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("installations.id", ondelete="CASCADE")
    )
    github_repo_id: Mapped[int] = mapped_column(unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(255), index=True)  # e.g. "acme/backend"
    default_branch: Mapped[str] = mapped_column(String(255), default="main")
    status: Mapped[str] = mapped_column(String(50), default="pending_init")

    installation: Mapped["Installation"] = relationship(back_populates="repos")
    jobs: Mapped[list["Job"]] = relationship(back_populates="repo", cascade="all, delete-orphan")


class Job(Base):
    """A background processing job tracked in Postgres."""

    __tablename__ = "jobs"

    repo_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("repos.id", ondelete="CASCADE"))
    job_type: Mapped[str] = mapped_column(String(50))  # "init" or "pr_update"
    trigger_ref: Mapped[str] = mapped_column(String(255))  # "installation" or "PR #42"
    status: Mapped[str] = mapped_column(String(50), default="queued")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    repo: Mapped["Repo"] = relationship(back_populates="jobs")
