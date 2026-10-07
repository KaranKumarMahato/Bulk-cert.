"""Shared pytest fixtures.

Every test gets its own temporary SQLite database and storage directory via
``tmp_path``, plus its own app instance (``create_app`` isolates engines,
session factories and worker threads), so tests are fully independent and can
run in any order.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

TERMINAL_STATUSES = {"completed", "completed_with_failures", "failed"}


def make_recipients(count: int) -> list[dict]:
    """Build *count* valid, distinct sample recipients."""
    return [
        {
            "name": f"Recipient {i + 1}",
            "email": f"recipient{i + 1}@example.com",
            "course": "Sample Course",
        }
        for i in range(count)
    ]


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        database_url=f"sqlite:///{(tmp_path / 'test.db').as_posix()}",
        storage_dir=tmp_path / "storage",
        max_recipients_per_job=25,
    )


@pytest.fixture
def app(settings):
    return create_app(settings)


@pytest.fixture
def client(app) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def wait_for_job(client) -> Callable[..., dict]:
    """Return a helper that polls ``GET /jobs/{id}`` until the job finishes."""

    def _wait(job_id: str, timeout: float = 20.0) -> dict:
        deadline = time.monotonic() + timeout
        while True:
            response = client.get(f"/api/v1/jobs/{job_id}")
            assert response.status_code == 200, response.text
            data = response.json()
            if data["status"] in TERMINAL_STATUSES:
                return data
            assert time.monotonic() < deadline, (
                f"job {job_id} did not finish in time; last status={data['status']}"
            )
            time.sleep(0.05)

    return _wait
