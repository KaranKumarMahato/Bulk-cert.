"""Tests: retrieving generated certificates (list, single PNG download, ZIP)."""

import io
import zipfile

from app.models import Certificate, Job
from tests.conftest import make_recipients

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _generate_job(client, wait_for_job, count=2) -> dict:
    job = client.post("/api/v1/jobs", json={"recipients": make_recipients(count)}).json()
    final = wait_for_job(job["id"])
    assert final["status"] == "completed"
    return final


def test_download_single_certificate_returns_png(client, wait_for_job):
    job = _generate_job(client, wait_for_job, count=1)
    cert = client.get(f"/api/v1/jobs/{job['id']}/certificates").json()[0]

    response = client.get(cert["download_url"])
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert "attachment" in response.headers["content-disposition"]
    assert response.content.startswith(PNG_MAGIC)
    assert len(response.content) > 10_000  # a real rendered image, not a stub


def test_download_unknown_certificate_returns_404(client):
    assert client.get("/api/v1/certificates/nope/download").status_code == 404


def test_download_pending_certificate_returns_conflict(app, client):
    with app.state.session_factory() as session:
        job = Job(title="Pending Job", total_count=1)
        cert = Certificate(job=job, recipient_index=0, name="Pending Person", status="pending")
        session.add(job)
        session.add(cert)
        session.commit()
        cert_id = cert.id

    response = client.get(f"/api/v1/certificates/{cert_id}/download")
    assert response.status_code == 409
    assert "pending" in response.json()["detail"]


def test_zip_download_contains_all_completed_certificates(client, wait_for_job):
    job = _generate_job(client, wait_for_job, count=3)

    response = client.get(f"/api/v1/jobs/{job['id']}/certificates/download")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert f"job_{job['id'][:8]}_certificates.zip" in response.headers["content-disposition"]

    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        names = archive.namelist()
        assert len(names) == 3
        assert all(name.endswith(".png") for name in names)
        for name in names:
            assert archive.read(name).startswith(PNG_MAGIC)


def test_zip_download_conflicts_when_nothing_completed(app, client):
    with app.state.session_factory() as session:
        job = Job(title="Empty Job", status="queued", total_count=1)
        session.add(job)
        session.commit()
        job_id = job.id

    response = client.get(f"/api/v1/jobs/{job_id}/certificates/download")
    assert response.status_code == 409

    assert client.get("/api/v1/jobs/missing/certificates/download").status_code == 404
