from __future__ import annotations

from datetime import datetime, timezone
from functools import lru_cache
from typing import Generator

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from backend.config import get_settings


class Base(DeclarativeBase):
    pass


class ReviewRecord(Base):
    __tablename__ = "reviews"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    repository: Mapped[str | None] = mapped_column(String(300), nullable=True, index=True)
    file_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    language: Mapped[str] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(30), default="completed")
    summary: Mapped[str] = mapped_column(Text)
    overall_risk: Mapped[str] = mapped_column(String(20))
    issue_count: Mapped[int] = mapped_column(Integer, default=0)
    issues_json: Mapped[str] = mapped_column(Text, default="[]")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True
    )


class ReviewJob(Base):
    __tablename__ = "review_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    status: Mapped[str] = mapped_column(String(30), default="queued", index=True)
    task_id: Mapped[str | None] = mapped_column(String(100), nullable=True)
    repository: Mapped[str | None] = mapped_column(String(300), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    review_id: Mapped[int | None] = mapped_column(ForeignKey("reviews.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True
    )


class GitHubWebhookDelivery(Base):
    __tablename__ = "github_webhook_deliveries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    delivery_id: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    job_id: Mapped[int | None] = mapped_column(ForeignKey("review_jobs.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="received")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), index=True
    )


class CodeChunk(Base):
    __tablename__ = "code_chunks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    repository: Mapped[str] = mapped_column(String(300), index=True)
    path: Mapped[str] = mapped_column(String(500), index=True)
    chunk_index: Mapped[int] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text)


@lru_cache(maxsize=1)
def _engine():
    url = get_settings().database_url
    kwargs = {"check_same_thread": False} if url.startswith("sqlite") else {}
    return create_engine(url, pool_pre_ping=True, connect_args=kwargs)


@lru_cache(maxsize=1)
def _session_factory():
    return sessionmaker(bind=_engine(), autoflush=False, expire_on_commit=False)


def initialize_database() -> None:
    Base.metadata.create_all(_engine())


def get_session() -> Generator:
    session = _session_factory()()
    try:
        yield session
    finally:
        session.close()


def create_session():
    return _session_factory()()
