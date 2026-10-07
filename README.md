# Bulk Certificate Generator

A backend API that accepts **bulk certificate generation requests** for a list of
recipients, generates PNG certificates from a single predefined template,
tracks per-request progress, and serves the generated files.

Built with **FastAPI + SQLAlchemy + SQLite + Pillow (Python)**.

---

## Table of contents

1. [Setup](#setup)
2. [Running the application](#running-the-application)
3. [Running the tests](#running-the-tests)
4. [How to submit a generation request](#how-to-submit-a-generation-request)
5. [How to check status / progress](#how-to-check-status--progress)
6. [How to retrieve generated certificates](#how-to-retrieve-generated-certificates)
7. [API reference](#api-reference)
8. [Validation & failure handling](#validation--failure-handling)
9. [Important design decisions](#important-design-decisions)
10. [Project structure](#project-structure)

---

## Setup

Requires **Python 3.11+** (developed and tested on Python 3.13).

```bash
# from the project root
python -m venv .venv

# Linux/macOS
source .venv/bin/activate
# Windows
.venv\Scripts\activate

pip install -r requirements.txt
```

No other setup is needed: the default database is a local SQLite file
(`bulk_certificates.db`, created automatically) and certificates are written
to `storage/certificates/<job_id>/`.

## Running the application

```bash
uvicorn app.main:app --reload
```

Then open the interactive API docs at <http://127.0.0.1:8000/docs>.

Optional environment variables:

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | `sqlite:///./bulk_certificates.db` | Any SQLAlchemy URL (e.g. PostgreSQL) |
| `CERT_STORAGE_DIR` | `storage/certificates` | Where PNG files are written |
| `MAX_RECIPIENTS_PER_JOB` | `1000` | Upper bound on recipients per request |
| `SYNC_PROCESSING` | off | `1` = generate inline before responding (debugging) |

## Running the tests

```bash
python -m pytest
```

Tests use an isolated temporary SQLite database and storage directory per test
(see `tests/conftest.py`); they never touch your local data or the network.

Coverage includes: creating a job, input validation, certificate generation,
job status/progress, individual certificate failure isolation, and retrieving
certificates (single PNG + ZIP).

## How to submit a generation request

`POST /api/v1/jobs` — one request for **all** recipients (no per-certificate calls).

```bash
curl -X POST http://127.0.0.1:8000/api/v1/jobs \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Certificate of Completion",
    "organization": "Acme Learning",
    "issued_by": "Jane Doe, Program Director",
    "notes": "Awarded for completing the 2026 Spring Workshop.",
    "recipients": [
      {"name": "Ada Lovelace", "email": "ada@example.com", "course": "Data Science 101"},
      {"name": "Grace Hopper", "email": "grace@example.com", "course": "Data Science 101",
       "completion_date": "2026-04-01"},
      {"name": "Alan Turing", "course": "Cryptography"}
    ]
  }'
```

**Response `202 Accepted`:**

```json
{
  "id": "9f2c...",
  "status": "queued",
  "title": "Certificate of Completion",
  "organization": "Acme Learning",
  "issued_by": "Jane Doe, Program Director",
  "notes": "Awarded for completing the 2026 Spring Workshop.",
  "error": null,
  "created_at": "2026-10-07T12:00:00",
  "started_at": null,
  "completed_at": null,
  "total_count": 3,
  "completed_count": 0,
  "failed_count": 0,
  "progress": {"total": 3, "processed": 0, "completed": 0, "failed": 0, "pending": 3, "percent": 0}
}
```

Recipient fields: `name` (required), `email` (optional), `course` (optional,
defaults to `"this program"`), `completion_date` (optional `YYYY-MM-DD`,
defaults to the job creation date).

## How to check status / progress

```bash
curl http://127.0.0.1:8000/api/v1/jobs/<job_id>
```

`status` moves `queued → processing → completed | completed_with_failures | failed`.
`progress` updates live while the job runs (state is committed after every
recipient).

Per-recipient results (which succeeded/failed, and why):

```bash
curl http://127.0.0.1:8000/api/v1/jobs/<job_id>/certificates
curl "http://127.0.0.1:8000/api/v1/jobs/<job_id>/certificates?status=failed"
```

Each entry includes `status` (`pending|completed|failed`), an `error` message
for failures, and a `download_url` when completed.

## How to retrieve generated certificates

```bash
# Single certificate (PNG)
curl -OJ http://127.0.0.1:8000/api/v1/certificates/<certificate_id>/download

# All completed certificates of a job in one ZIP
curl -OJ http://127.0.0.1:8000/api/v1/jobs/<job_id>/certificates/download
```

Files are also on disk under `storage/certificates/<job_id>/<certificate_id>.png`.
Each PNG embeds recipient metadata (name, course, job/certificate ids) as text
chunks, so files are self-describing.

## API reference

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/v1/jobs` | Create a bulk generation job → `202` |
| `GET` | `/api/v1/jobs` | List jobs (`limit`, `offset`) |
| `GET` | `/api/v1/jobs/{id}` | Job status + progress |
| `GET` | `/api/v1/jobs/{id}/certificates` | Per-recipient records (`?status=` filter) |
| `GET` | `/api/v1/jobs/{id}/certificates/download` | ZIP of completed certificates |
| `GET` | `/api/v1/certificates/{id}/download` | Single PNG download |
| `GET` | `/health` | Liveness probe |

Status codes: `202` accepted, `200` OK, `404` unknown id, `409` exists but not
downloadable yet, `422` validation error (structured field locations).

## Validation & failure handling

* **Validation happens up front.** The envelope and every recipient are checked
  by Pydantic *before* a job is created. Bad input is a deterministic client
  error, so the whole request is rejected with `422` and a structured `loc`
  pinpointing the field, e.g. `["body", "recipients", 2, "email"]`. The client
  fixes and resubmits once, instead of discovering bad rows scattered across a
  half-finished job. Checks include: non-empty recipient list, blank names,
  malformed emails (empty string tolerated as "no email"), invalid dates,
  unknown fields rejected, size limits (`MAX_RECIPIENTS_PER_JOB`).
* **Generation failures are isolated.** Each certificate renders inside its own
  try/except; a failure records an `error` on that row and the loop continues
  with the remaining recipients. One broken certificate never blocks the job.
* **Job status summarises the outcome**: `completed` (all succeeded),
  `completed_with_failures` (some failed), `failed` (all failed or an
  infrastructure error). Failed rows keep their error message for diagnosis.
* Rendering writes PNGs atomically (temp file + rename), so no half-written
  file is ever served.

## Important design decisions

**FastAPI (over Django/DRF or Flask).** Pydantic gives precise, structured
request validation out of the box (ideal for point 2 of the spec), async-ready
performance for an I/O-bound API, and auto-generated OpenAPI docs that make the
API easy to explore in an interview. Flask would need extra validation
plumbing; Django/DRF brings a larger framework than this service needs.

**SQLite + SQLAlchemy.** A relational database is required; SQLite keeps setup
to zero while SQLAlchemy's `DATABASE_URL` lets you switch to PostgreSQL by
changing one environment variable — the ORM code is unchanged.

**Relational model: `jobs` + `certificates`.** One job per request, one
certificate row per recipient with its own status/error/file path. This is what
makes per-recipient progress, failure isolation, and querying (`?status=failed`)
natural; recipients are not just blobs in a JSON column.

**Background processing with a single worker thread + `queue.Queue`** (default).
Reasoning:
* *Synchronous* generation would hold the HTTP request open for large batches —
  brittle, timeouts, no progress to report.
* *FastAPI `BackgroundTasks`* run tied to the request lifecycle with no queueing
  discipline (many concurrent jobs → many concurrent render storms).
* *Celery/RQ* would be the production answer for horizontal scale, but adds a
  broker + extra processes out of scope for this exercise.
* A dedicated thread decouples acceptance from generation, serialises CPU-bound
  rendering (Pillow) so a 1000-recipient job doesn't starve the event loop,
  preserves FIFO ordering, and is trivially replaceable by Celery later because
  routes only call `worker.submit(job_id)`.
* `SYNC_PROCESSING=1` switches to inline processing for debugging/tests.
  Progress is observable because each recipient's result is committed
  immediately.

**Single predefined template with Pillow.** The spec explicitly excludes a
template editor. Pillow draws a fixed A4-landscape layout, needs no headless
browser or office suite, and runs anywhere. Fonts resolve from common system
locations with a fallback chain (`load_default`), so nothing must be bundled.
Embedding recipient data as PNG text chunks makes outputs verifiable in tests
without OCR. Note on glyph coverage: fonts are chosen for *availability*, not
per-string coverage, so scripts outside the installed fonts' coverage (e.g. CJK
on a minimal Linux container) may fall back to placeholder glyphs -- the
embedded metadata is always correct regardless; adding a per-script font
selection would be a small extension.

**Validation up front, failure isolation during processing** — the split is
deliberate: client mistakes fail fast with precise locations; runtime failures
are recorded per row without aborting the job (details above).

**App factory (`create_app(settings)`) instead of a module-global app.** Every
test builds a fully isolated instance (own engine, worker, storage). The
module-level `app = create_app()` remains for `uvicorn app.main:app`.

## Project structure

```
app/
  config.py       # Settings (env-overridable)
  database.py     # Engine/session factory, declarative base
  models.py       # ORM: Job, Certificate (+ status enums)
  schemas.py      # Pydantic request/response models & validation
  certificate.py  # Pillow template renderer (atomic writes, PNG metadata)
  services.py     # create_job / process_job (per-recipient isolation)
  worker.py       # background queue + worker thread
  routes.py       # API endpoints
  main.py         # create_app factory, lifespan wiring
tests/
  conftest.py           # isolated app/db/storage fixtures
  test_create_job.py    # creating jobs
  test_validation.py    # input validation
  test_generation.py    # generation, progress, failure isolation
  test_retrieval.py     # downloads (single PNG + ZIP)
```

### Known limitations / possible extensions

* Jobs stuck `queued/processing` after a crash are not auto-resumed (a
  `POST /jobs/{id}/retry` endpoint + startup reconciliation would fix this).
* Single-process worker; for horizontal scale move `services.process_job` to
  Celery/RQ and switch SQLite → PostgreSQL.
* No auth/rate limiting (out of scope); add API keys before exposing publicly.
