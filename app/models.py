"""SQLAlchemy ORM models.

One *job* represents a bulk generation request. Each recipient inside the job
becomes a *certificate* row, which is tracked individually so that a failure
for one recipient never blocks the others.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from enum import Enum

from sqlalchemy import Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


class JobStatus(str, Enum):
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    COMPLETED_WITH_FAILURES = "completed_with_failures"
    FAILED = "failed"


TERMINAL_JOB_STATUSES = frozenset(
    {JobStatus.COMPLETED.value, JobStatus.COMPLETED_WITH_FAILURES.value, JobStatus.FAILED.value}
)


class CertificateStatus(str, Enum):
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"


def _uuid() -> str:
    return uuid.uuid4().hex


def utcnow() -> datetime:
    """Current UTC time as a naive datetime.

    SQLite does not preserve timezone offsets, so every timestamp stored by
    this application is naive UTC to keep comparisons and serialisation
    consistent.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    title: Mapped[str] = mapped_column(String(200), default="Certificate of Completion")
    organization: Mapped[str] = mapped_column(String(200), default="Acme Learning")
    issued_by: Mapped[str] = mapped_column(String(200), default="Program Director")
    notes: Mapped[str | None] = mapped_column(String(500), nullable=True)

    status: Mapped[str] = mapped_column(
        String(32), default=JobStatus.QUEUED.value, index=True
    )
    total_count: Mapped[int] = mapped_column(Integer, default=0)
    completed_count: Mapped[int] = mapped_column(Integer, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)

    certificates: Mapped[list["Certificate"]] = relationship(
        back_populates="job",
        cascade="all, delete-orphan",
        order_by="Certificate.recipient_index",
    )


class Certificate(Base):
    __tablename__ = "certificates"
    __table_args__ = (
        UniqueConstraint("job_id", "recipient_index", name="uq_certificate_job_index"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    job_id: Mapped[str] = mapped_column(String(32), ForeignKey("jobs.id"), index=True)
    recipient_index: Mapped[int] = mapped_column(Integer)

    name: Mapped[str] = mapped_column(String(200))
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    course: Mapped[str] = mapped_column(String(200), default="this program")
    completion_date: Mapped[date | None] = mapped_column(Date, nullable=True)

    status: Mapped[str] = mapped_column(
        String(16), default=CertificateStatus.PENDING.value, index=True
    )
    file_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    generated_at: Mapped[datetime | None] = mapped_column(DateTime(), nullable=True)

    job: Mapped[Job] = relationship(back_populates="certificates")
