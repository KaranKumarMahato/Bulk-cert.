"""Tests: certificate generation, job progress, and per-recipient failure isolation."""

from PIL import Image

from app import certificate as certificate_module
from tests.conftest import make_recipients

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def test_bulk_generation_completes_and_produces_valid_pngs(client, wait_for_job, settings):
    recipients = [
        {"name": "Ada Lovelace", "email": "ada@example.com", "course": "Analytical Engines"},
        {"name": "Grace Hopper", "course": "Compilers"},
        {"name": "Alan Turing", "course": "Cryptography", "completion_date": "2026-04-01"},
        {"name": "Katherine Johnson", "course": "Orbital Mechanics"},
    ]
    job_id = client.post("/api/v1/jobs", json={"recipients": recipients}).json()["id"]

    final = wait_for_job(job_id)
    assert final["status"] == "completed"
    assert final["progress"] == {
        "total": 4,
        "processed": 4,
        "completed": 4,
        "failed": 0,
        "pending": 0,
        "percent": 100,
    }
    assert final["started_at"] and final["completed_at"]

    certificates = client.get(f"/api/v1/jobs/{job_id}/certificates").json()
    assert len(certificates) == 4
    for cert, recipient in zip(certificates, recipients):
        assert cert["status"] == "completed"
        assert cert["error"] is None
        assert cert["download_url"] == f"/api/v1/certificates/{cert['id']}/download"

        # The file exists on disk in this job's storage folder.
        path = settings.storage_dir / job_id / f"{cert['id']}.png"
        assert path.is_file()
        assert path.read_bytes().startswith(PNG_MAGIC)

        # Recipient-specific data reached the certificate (embedded as PNG text).
        with Image.open(path) as image:
            assert image.format == "PNG"
            assert image.size == (1600, 1131)
            assert image.text["recipient_name"] == recipient["name"]
            assert image.text["job_id"] == job_id
            assert image.text["certificate_id"] == cert["id"]

    # Distinct recipients got distinct files with distinct metadata.
    names = {c["name"] for c in certificates}
    assert names == {"Ada Lovelace", "Grace Hopper", "Alan Turing", "Katherine Johnson"}


def test_non_latin_recipient_name_renders_with_correct_metadata(client, wait_for_job, settings):
    """Non-Latin-1 names must survive PNG metadata embedding (iTXt fallback)."""
    job_id = client.post(
        "/api/v1/jobs",
        json={
            "recipients": [
                {"name": "Jos\u00e9 Alvarez", "course": "Espa\u00f1ol"},
                {"name": "\u674e\u660e", "course": "\u4e2d\u6587\u8bfe\u7a0b"},
            ]
        },
    ).json()["id"]

    final = wait_for_job(job_id)
    assert final["status"] == "completed"

    certificates = client.get(f"/api/v1/jobs/{job_id}/certificates").json()
    for cert in certificates:
        path = settings.storage_dir / job_id / f"{cert['id']}.png"
        with Image.open(path) as image:
            assert image.text["recipient_name"] == cert["name"]


def test_job_status_shows_terminal_state_after_completion(client, wait_for_job):
    job_id = client.post(
        "/api/v1/jobs", json={"recipients": make_recipients(2)}
    ).json()["id"]

    final = wait_for_job(job_id)
    assert final["status"] == "completed"
    assert final["progress"]["percent"] == 100
    assert final["error"] is None


def test_individual_failure_does_not_block_other_recipients(
    client, wait_for_job, monkeypatch
):
    original_renderer = certificate_module.render_certificate

    def flaky_renderer(content, output_path):
        if content.recipient_name == "Bad Actor":
            raise RuntimeError("simulated rendering failure")
        return original_renderer(content, output_path)

    monkeypatch.setattr(certificate_module, "render_certificate", flaky_renderer)

    recipients = [
        {"name": "Ada Lovelace"},
        {"name": "Bad Actor"},
        {"name": "Grace Hopper"},
    ]
    job_id = client.post("/api/v1/jobs", json={"recipients": recipients}).json()["id"]

    final = wait_for_job(job_id)
    assert final["status"] == "completed_with_failures"
    assert final["progress"]["completed"] == 2
    assert final["progress"]["failed"] == 1

    certificates = client.get(f"/api/v1/jobs/{job_id}/certificates").json()
    by_name = {c["name"]: c for c in certificates}

    failed = by_name["Bad Actor"]
    assert failed["status"] == "failed"
    assert "simulated rendering failure" in failed["error"]
    assert failed["download_url"] is None

    for name in ("Ada Lovelace", "Grace Hopper"):
        assert by_name[name]["status"] == "completed"
        assert by_name[name]["download_url"] is not None

    # Status endpoint supports filtering so clients can find failures quickly.
    failed_only = client.get(f"/api/v1/jobs/{job_id}/certificates?status=failed").json()
    assert [c["name"] for c in failed_only] == ["Bad Actor"]

    # The failed certificate cannot be downloaded; a successful one can.
    assert (
        client.get(f"/api/v1/certificates/{failed['id']}/download").status_code == 409
    )
    ok_id = by_name["Ada Lovelace"]["id"]
    assert client.get(f"/api/v1/certificates/{ok_id}/download").status_code == 200


def test_sync_processing_mode_finishes_before_response(settings):
    from fastapi.testclient import TestClient

    from app.config import Settings
    from app.main import create_app

    sync_settings = Settings(
        database_url=settings.database_url.replace("test.db", "sync_test.db"),
        storage_dir=settings.storage_dir.parent / "sync_storage",
        max_recipients_per_job=25,
        sync_processing=True,
    )
    sync_app = create_app(sync_settings)
    with TestClient(sync_app) as sync_client:
        response = sync_client.post(
            "/api/v1/jobs", json={"recipients": make_recipients(2)}
        )
        assert response.status_code == 202
        data = response.json()
        # Inline mode processes before returning, so it is already terminal.
        assert data["status"] == "completed"
        assert data["progress"]["percent"] == 100
