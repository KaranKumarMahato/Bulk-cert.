"""Tests: creating a generation job."""

from tests.conftest import make_recipients


def test_create_job_returns_accepted_with_status_document(client):
    payload = {"recipients": make_recipients(3)}
    response = client.post("/api/v1/jobs", json=payload)

    assert response.status_code == 202
    data = response.json()
    assert data["id"]
    assert data["status"] in {"queued", "processing", "completed"}
    assert data["progress"]["total"] == 3
    assert data["progress"]["processed"] + data["progress"]["pending"] == 3
    assert data["created_at"]


def test_created_job_is_queryable_with_all_recipients(client, wait_for_job):
    payload = {
        "title": "Certificate of Completion",
        "organization": "Acme Learning",
        "issued_by": "Jane Doe",
        "recipients": [
            {"name": "Ada Lovelace", "email": "ada@example.com", "course": "Analytical Engines"},
            {"name": "Grace Hopper", "course": "Compilers"},
            {"name": "Alan Turing", "completion_date": "2026-04-01"},
        ],
    }
    job_id = client.post("/api/v1/jobs", json=payload).json()["id"]

    final = wait_for_job(job_id)
    assert final["status"] == "completed"
    assert final["title"] == "Certificate of Completion"
    assert final["organization"] == "Acme Learning"

    certificates = client.get(f"/api/v1/jobs/{job_id}/certificates").json()
    assert [c["name"] for c in certificates] == [
        "Ada Lovelace",
        "Grace Hopper",
        "Alan Turing",
    ]
    assert certificates[0]["email"] == "ada@example.com"
    assert certificates[1]["email"] is None
    assert certificates[2]["completion_date"] == "2026-04-01"
    assert all(c["recipient_index"] == i for i, c in enumerate(certificates))


def test_job_uses_documented_defaults(client, wait_for_job):
    job_id = client.post(
        "/api/v1/jobs", json={"recipients": [{"name": "Default Person"}]}
    ).json()["id"]

    final = wait_for_job(job_id)
    assert final["title"] == "Certificate of Completion"
    assert final["organization"] == "Acme Learning"
    assert final["issued_by"] == "Program Director"
    assert final["notes"] is None


def test_list_jobs_returns_newest_first(client, wait_for_job):
    first = client.post("/api/v1/jobs", json={"recipients": make_recipients(1)}).json()
    second = client.post("/api/v1/jobs", json={"recipients": make_recipients(1)}).json()
    wait_for_job(first["id"])
    wait_for_job(second["id"])

    response = client.get("/api/v1/jobs")
    assert response.status_code == 200
    jobs = response.json()
    ids = [job["id"] for job in jobs]
    assert first["id"] in ids and second["id"] in ids
    assert ids.index(second["id"]) < ids.index(first["id"])


def test_unknown_job_returns_404(client):
    assert client.get("/api/v1/jobs/does-not-exist").status_code == 404
    assert client.get("/api/v1/jobs/does-not-exist/certificates").status_code == 404


def test_health_and_root(client):
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/").json()["docs"] == "/docs"
