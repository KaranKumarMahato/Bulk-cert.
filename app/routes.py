"""API routes for the Bulk Certificate Generator."""

from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import services
from .models import Certificate, CertificateStatus, Job
from .schemas import CertificateOut, JobCreate, JobStatusOut

router = APIRouter(prefix="/api/v1", tags=["certificates"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_job(session: Session, job_id: str) -> Job:
    job = session.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found")
    return job


def _slug(text: str, max_length: int = 60) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:max_length] or "recipient"


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------


@router.post("/jobs", status_code=202, response_model=JobStatusOut)
def create_generation_job(payload: JobCreate, request: Request) -> JobStatusOut:
    """Create a bulk certificate generation job.

    Returns ``202 Accepted`` immediately; generation runs in the background
    (or inline when ``SYNC_PROCESSING`` is enabled). Poll ``GET /jobs/{id}``
    for progress.
    """
    state = request.app.state
    try:
        job = services.create_job(state.session_factory, state.settings, payload)
    except ValueError as exc:
        # e.g. recipient count exceeds MAX_RECIPIENTS_PER_JOB
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if state.settings.sync_processing:
        services.process_job(job.id, state.session_factory, state.settings)
    else:
        state.worker.submit(job.id)

    with state.session_factory() as session:
        job = _get_job(session, job.id)
        return JobStatusOut.model_validate(job)


@router.get("/jobs", response_model=list[JobStatusOut])
def list_jobs(
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> list[JobStatusOut]:
    """List generation jobs, newest first."""
    with request.app.state.session_factory() as session:
        jobs = session.scalars(
            select(Job).order_by(Job.created_at.desc(), Job.id).limit(limit).offset(offset)
        ).all()
        return [JobStatusOut.model_validate(job) for job in jobs]


@router.get("/jobs/{job_id}", response_model=JobStatusOut)
def get_job_status(job_id: str, request: Request) -> JobStatusOut:
    """Return status and live progress for a job."""
    with request.app.state.session_factory() as session:
        return JobStatusOut.model_validate(_get_job(session, job_id))


@router.get("/jobs/{job_id}/certificates", response_model=list[CertificateOut])
def list_job_certificates(
    job_id: str,
    request: Request,
    status: Literal["pending", "completed", "failed"] | None = Query(None),
) -> list[CertificateOut]:
    """List per-recipient certificate records for a job (optionally filtered)."""
    with request.app.state.session_factory() as session:
        _get_job(session, job_id)
        stmt = (
            select(Certificate)
            .where(Certificate.job_id == job_id)
            .order_by(Certificate.recipient_index)
        )
        if status is not None:
            stmt = stmt.where(Certificate.status == status)
        certificates = session.scalars(stmt).all()
        return [CertificateOut.model_validate(cert) for cert in certificates]


@router.get("/jobs/{job_id}/certificates/download")
def download_job_zip(job_id: str, request: Request) -> Response:
    """Download all *completed* certificates of a job as a single ZIP archive."""
    state = request.app.state
    with state.session_factory() as session:
        _get_job(session, job_id)
        certificates = session.scalars(
            select(Certificate)
            .where(
                Certificate.job_id == job_id,
                Certificate.status == CertificateStatus.COMPLETED.value,
            )
            .order_by(Certificate.recipient_index)
        ).all()

        buffer = io.BytesIO()
        count = 0
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for cert in certificates:
                if not cert.file_path:
                    continue
                path = Path(cert.file_path)
                if not path.is_file():
                    continue
                arcname = f"{cert.recipient_index + 1:03d}_{_slug(cert.name)}_{cert.id[:8]}.png"
                archive.write(path, arcname=arcname)
                count += 1

    if count == 0:
        raise HTTPException(
            status_code=409,
            detail="No completed certificates are available for this job yet",
        )

    filename = f"job_{job_id[:8]}_certificates.zip"
    return Response(
        content=buffer.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---------------------------------------------------------------------------
# Individual certificates
# ---------------------------------------------------------------------------


@router.get("/certificates/{certificate_id}/download")
def download_certificate(certificate_id: str, request: Request) -> FileResponse:
    """Download a single generated certificate as a PNG."""
    with request.app.state.session_factory() as session:
        cert = session.get(Certificate, certificate_id)
        if cert is None:
            raise HTTPException(status_code=404, detail="Certificate not found")
        if cert.status != CertificateStatus.COMPLETED.value:
            raise HTTPException(
                status_code=409,
                detail=f"Certificate is '{cert.status}' and cannot be downloaded",
            )
        file_path = Path(cert.file_path) if cert.file_path else None
        file_name = f"{_slug(cert.name)}_certificate_{cert.id[:8]}.png"

    if file_path is None or not file_path.is_file():
        raise HTTPException(status_code=404, detail="Generated file is missing from storage")
    return FileResponse(file_path, media_type="image/png", filename=file_name)
