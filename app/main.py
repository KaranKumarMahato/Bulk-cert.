"""FastAPI application factory.

``create_app`` builds an isolated app instance (own engine, session factory
and worker) which keeps tests fully independent of each other and of the
developer's local database. ``uvicorn app.main:app`` uses the module-level
``app`` created from environment settings.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from .config import Settings
from .database import Base, create_db_engine, create_session_factory
from .routes import router
from .worker import CertificateWorker

API_VERSION = "1.0.0"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Storage and schema are initialised once per app instance.
        # (Models are registered because routes/services import them.)
        settings.storage_dir.mkdir(parents=True, exist_ok=True)
        engine = create_db_engine(settings.database_url)
        Base.metadata.create_all(engine)
        session_factory = create_session_factory(engine)

        worker = CertificateWorker(session_factory, settings)
        if not settings.sync_processing:
            worker.start()

        app.state.settings = settings
        app.state.engine = engine
        app.state.session_factory = session_factory
        app.state.worker = worker
        try:
            yield
        finally:
            worker.shutdown()
            engine.dispose()

    app = FastAPI(
        title="Bulk Certificate Generator",
        version=API_VERSION,
        description=(
            "Submit a list of recipients, generate PNG certificates from a "
            "predefined template in the background, track progress, and "
            "download the results."
        ),
        lifespan=lifespan,
    )
    app.include_router(router)

    @app.get("/", tags=["meta"])
    def root() -> dict[str, str]:
        return {
            "service": "Bulk Certificate Generator",
            "version": API_VERSION,
            "docs": "/docs",
            "health": "/health",
        }

    @app.get("/health", tags=["meta"])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
