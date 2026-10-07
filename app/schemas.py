"""Pydantic request/response schemas.

Validation philosophy
---------------------
* The request envelope and every recipient are validated *before* a job is
  created. Invalid input is a deterministic client error, so the whole request
  is rejected with a structured 422 that pinpoints the offending field
  (e.g. ``body.recipients.2.email``) and the client can fix and resubmit.
* Anything that can fail *at generation time* (rendering, disk, fonts) is
  handled per recipient during processing so one bad certificate never blocks
  the rest of the job.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator

# Deliberately a pragmatic regex instead of the ``email-validator`` package:
# it keeps runtime dependencies minimal while still rejecting obvious typos.
# Empty strings are accepted and coerced to "no email".
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$")

JobStatusLiteral = Literal[
    "queued", "processing", "completed", "completed_with_failures", "failed"
]
CertificateStatusLiteral = Literal["pending", "completed", "failed"]


class RecipientIn(BaseModel):
    """A single recipient of a certificate."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=200, description="Recipient's full name")
    email: str | None = Field(default=None, max_length=320, description="Optional contact email")
    course: str = Field(
        default="this program",
        min_length=1,
        max_length=200,
        description="Course/event text printed on the certificate",
    )
    completion_date: date | None = Field(
        default=None,
        description="Completion date (YYYY-MM-DD). Defaults to the job creation date.",
    )

    @field_validator("name")
    @classmethod
    def name_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("recipient name must not be blank")
        return value

    @field_validator("course")
    @classmethod
    def course_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("course must not be blank")
        return value

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            return None  # tolerate empty string as "no email"
        if not EMAIL_RE.match(value):
            raise ValueError("invalid email address")
        return value


class JobCreate(BaseModel):
    """Payload for creating a bulk certificate generation job."""

    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "example": {
                "title": "Certificate of Completion",
                "organization": "Acme Learning",
                "issued_by": "Jane Doe, Program Director",
                "notes": "Awarded for completing the 2026 Spring Workshop.",
                "recipients": [
                    {"name": "Ada Lovelace", "email": "ada@example.com", "course": "Data Science 101"},
                    {"name": "Grace Hopper", "course": "Data Science 101", "completion_date": "2026-04-01"},
                ],
            }
        },
    )

    title: str = Field(default="Certificate of Completion", min_length=1, max_length=200)
    organization: str = Field(default="Acme Learning", min_length=1, max_length=200)
    issued_by: str = Field(default="Program Director", min_length=1, max_length=200)
    notes: str | None = Field(default=None, max_length=500)
    recipients: list[RecipientIn] = Field(
        min_length=1, description="1..MAX_RECIPIENTS_PER_JOB recipients"
    )


class JobStatusOut(BaseModel):
    """Job status/progress document returned by the API."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    status: JobStatusLiteral
    title: str
    organization: str
    issued_by: str
    notes: str | None
    error: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    total_count: int
    completed_count: int
    failed_count: int

    @computed_field
    @property
    def progress(self) -> dict[str, int]:
        """Live progress counters for the job."""
        processed = self.completed_count + self.failed_count
        return {
            "total": self.total_count,
            "processed": processed,
            "completed": self.completed_count,
            "failed": self.failed_count,
            "pending": max(self.total_count - processed, 0),
            "percent": round(100 * processed / self.total_count) if self.total_count else 0,
        }


class CertificateOut(BaseModel):
    """Per-recipient certificate record."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    job_id: str
    recipient_index: int
    name: str
    email: str | None
    course: str
    completion_date: date | None
    status: CertificateStatusLiteral
    error: str | None
    generated_at: datetime | None

    @computed_field
    @property
    def download_url(self) -> str | None:
        """Absolute-path URL to download the PNG, or ``None`` if not generated."""
        if self.status == "completed":
            return f"/api/v1/certificates/{self.id}/download"
        return None
