import uuid
from contextlib import asynccontextmanager
from datetime import datetime

from sqlalchemy import DateTime, func
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from nexus.config import settings

engine = create_async_engine(settings.database_url, echo=False)

async_session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy models."""

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )


async def get_session():
    """FastAPI dependency that yields an async DB session."""
    async with async_session_factory() as session:
        yield session


@asynccontextmanager
async def get_session_context():
    """Standalone async context manager for use outside FastAPI (e.g. ARQ workers)."""
    async with async_session_factory() as session:
        yield session
