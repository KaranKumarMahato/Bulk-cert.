"""Job services: creation and processing of bulk generation jobs."""

from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from . import certificate
from .certificate import CertificateContent
from .config import Settings
from .models import (
    TERMINAL_JOB_STATUSES,
    Certificate,
    CertificateStatus,
    Job,
    JobStatus,
    utcnow,
)
from .schemas import JobCreate

logger = logging.getLogger(__name__)


def create_job(session_factory: sessionmaker, settings: Settings, payload: JobCreate) -> Job:
    """Persist a new job with one pending certificate row per recipient."""
    if len(payload.recipients) > settings.max_recipients_per_job:
        raise ValueError(
            f"a job may contain at most {settings.max_recipients_per_job} recipients "
            f"(got {len(payload.recipients)})"
        )

    with session_factory() as session:
        job = Job(
            title=payload.title,
            organization=payload.organization,
            issued_by=payload.issued_by,
            notes=payload.notes,
            status=JobStatus.QUEUED.value,
            total_count=len(payload.recipients),
        )
        session.add(job)
        for index, recipient in enumerate(payload.recipients):
            session.add(
                Certificate(
                    job=job,
                    recipient_index=index,
                    name=recipient.name,
                    email=recipient.email,
                    course=recipient.course,
                    completion_date=recipient.completion_date,
                    status=CertificateStatus.PENDING.value,
                )
            )
        session.commit()
        return job


def _build_content(job: Job, cert: Certificate) -> CertificateContent:
    completion = cert.completion_date or (job.created_at.date() if job.created_at else utcnow().date())
    return CertificateContent(
        recipient_name=cert.name,
        course=cert.course,
        completion_date=completion,
        heading=job.title,
        organization=job.organization,
        issued_by=job.issued_by,
        notes=job.notes,
        metadata={
            "certificate_id": cert.id,
            "job_id": job.id,
            "recipient_name": cert.name,
            "recipient_email": cert.email or "",
            "course": cert.course,
            "completion_date": completion.isoformat(),
            "heading": job.title,
            "organization": job.organization,
            "issued_by": job.issued_by,
        },
    )


def _generate_one(job: Job, cert: Certificate, settings: Settings) -> Path:
    """Render a single certificate and return the path of the written PNG."""
    output_path = Path(settings.storage_dir) / job.id / f"{cert.id}.png"
    # Called via module attribute so tests can monkeypatch the renderer.
    certificate.render_certificate(_build_content(job, cert), output_path)
    if not output_path.is_file():
        raise RuntimeError(f"renderer did not produce {output_path}")
    return output_path


def process_job(job_id: str, session_factory: sessionmaker, settings: Settings) -> None:
    """Generate every pending certificate of a job.

    * Each recipient is processed inside its own try/except: a failure marks
      that one certificate ``failed`` and the loop continues with the rest.
    * State is committed after every recipient, so ``GET /jobs/{id}`` shows
      live progress while a large job is running.
    * Job-level status is finalised as ``completed`` /
      ``completed_with_failures`` / ``failed``.
    """
    with session_factory() as session:
        job = session.get(Job, job_id)
        if job is None:
            logger.warning("process_job: job %s not found", job_id)
            return
        if job.status in TERMINAL_JOB_STATUSES:
            return  # already finished (e.g. duplicate submit)

        job.status = JobStatus.PROCESSING.value
        job.started_at = utcnow()
        session.commit()

        certificates = session.scalars(
            select(Certificate)
            .where(Certificate.job_id == job.id)
            .order_by(Certificate.recipient_index)
        ).all()

        try:
            for cert in certificates:
                if cert.status != CertificateStatus.PENDING.value:
                    continue
                try:
                    cert.file_path = str(_generate_one(job, cert, settings))
                    cert.status = CertificateStatus.COMPLETED.value
                    cert.error = None
                    cert.generated_at = utcnow()
                    job.completed_count += 1
                except Exception as exc:  # noqa: BLE001 - isolate per-recipient failures
                    cert.status = CertificateStatus.FAILED.value
                    cert.error = f"{type(exc).__name__}: {exc}"
                    job.failed_count += 1
                    logger.exception("certificate %s (%s) failed", cert.id, cert.name)
                session.commit()  # publish progress incrementally

            # Finalise job status.
            if job.failed_count == 0:
                job.status = JobStatus.COMPLETED.value
            elif job.completed_count == 0:
                job.status = JobStatus.FAILED.value
                job.error = "All certificates failed to generate"
            else:
                job.status = JobStatus.COMPLETED_WITH_FAILURES.value
            job.completed_at = utcnow()
            session.commit()
        except Exception as exc:  # noqa: BLE001 - unexpected infrastructure failure
            logger.exception("job %s hit an unexpected error", job_id)
            session.rollback()
            try:
                # Rollback expires ORM objects; re-read the row before updating.
                job = session.get(Job, job_id)
                if job is None or job.status in TERMINAL_JOB_STATUSES:
                    return
                job.status = JobStatus.FAILED.value
                job.error = f"{type(exc).__name__}: {exc}"
                job.completed_at = utcnow()
                session.commit()
            except Exception:  # pragma: no cover - database itself unavailable
                logger.exception("could not persist failure state for job %s", job_id)
