"""Tests: input validation is rejected before any job is created."""

from tests.conftest import make_recipients


def _locs(response_json: list[dict]) -> list[tuple]:
    return [tuple(error["loc"]) for error in response_json]


def test_empty_recipient_list_is_rejected(client):
    response = client.post("/api/v1/jobs", json={"recipients": []})
    assert response.status_code == 422
    assert ("body", "recipients") in _locs(response.json()["detail"])


def test_missing_recipients_field_is_rejected(client):
    response = client.post("/api/v1/jobs", json={"title": "No recipients"})
    assert response.status_code == 422


def test_missing_recipient_name_is_rejected(client):
    response = client.post("/api/v1/jobs", json={"recipients": [{"email": "x@example.com"}]})
    assert response.status_code == 422
    assert ("body", "recipients", 0, "name") in _locs(response.json()["detail"])


def test_blank_recipient_name_is_rejected(client):
    response = client.post("/api/v1/jobs", json={"recipients": [{"name": "   "}]})
    assert response.status_code == 422
    assert ("body", "recipients", 0, "name") in _locs(response.json()["detail"])


def test_invalid_email_is_rejected_with_precise_location(client):
    recipients = make_recipients(2)
    recipients[1]["email"] = "not-an-email"
    response = client.post("/api/v1/jobs", json={"recipients": recipients})

    assert response.status_code == 422
    assert ("body", "recipients", 1, "email") in _locs(response.json()["detail"])


def test_empty_email_string_is_tolerated_as_missing(client, wait_for_job):
    recipients = [{"name": "Lenient Client", "email": ""}]
    response = client.post("/api/v1/jobs", json={"recipients": recipients})
    assert response.status_code == 202
    final = wait_for_job(response.json()["id"])
    assert final["status"] == "completed"


def test_invalid_completion_date_is_rejected(client):
    response = client.post(
        "/api/v1/jobs",
        json={"recipients": [{"name": "Date Person", "completion_date": "next Tuesday"}]},
    )
    assert response.status_code == 422
    assert ("body", "recipients", 0, "completion_date") in _locs(response.json()["detail"])


def test_unknown_fields_are_rejected(client):
    response = client.post(
        "/api/v1/jobs",
        json={"recipients": [{"name": "Strict Person"}], "surprise": True},
    )
    assert response.status_code == 422
    assert ("body", "surprise") in _locs(response.json()["detail"])


def test_recipient_unknown_fields_are_rejected(client):
    response = client.post(
        "/api/v1/jobs",
        json={"recipients": [{"name": "Strict Person", "extra_field": 1}]},
    )
    assert response.status_code == 422
    assert ("body", "recipients", 0, "extra_field") in _locs(response.json()["detail"])


def test_too_many_recipients_is_rejected(client):
    # settings fixture caps jobs at 25 recipients
    response = client.post("/api/v1/jobs", json={"recipients": make_recipients(26)})
    assert response.status_code == 422
    assert "25" in response.json()["detail"]


def test_oversized_name_is_rejected(client):
    response = client.post(
        "/api/v1/jobs", json={"recipients": [{"name": "x" * 201}]}
    )
    assert response.status_code == 422
