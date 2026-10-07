"""Application configuration.

Settings can be overridden with environment variables so the same code runs in
development, tests, and production without modification:

* ``DATABASE_URL``            -- SQLAlchemy database URL (default: local SQLite file)
* ``CERT_STORAGE_DIR``        -- directory where generated PNG files are written
* ``MAX_RECIPIENTS_PER_JOB``  -- upper bound on recipients in a single request
* ``SYNC_PROCESSING``         -- ``1``/``true`` to process jobs inline instead of
                                 in the background worker (useful for debugging)
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_MAX_RECIPIENTS = 1000


def _env_bool(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    database_url: str = "sqlite:///./bulk_certificates.db"
    storage_dir: Path = Path("storage/certificates")
    max_recipients_per_job: int = DEFAULT_MAX_RECIPIENTS
    sync_processing: bool = False

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            database_url=os.getenv("DATABASE_URL", cls.database_url),
            storage_dir=Path(os.getenv("CERT_STORAGE_DIR", str(cls.storage_dir))),
            max_recipients_per_job=int(
                os.getenv("MAX_RECIPIENTS_PER_JOB", str(cls.max_recipients_per_job))
            ),
            sync_processing=_env_bool("SYNC_PROCESSING"),
        )
