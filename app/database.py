"""Database engine and session factory helpers."""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    """Declarative base for all ORM models."""


def create_db_engine(database_url: str) -> Engine:
    """Create an engine for the given URL.

    SQLite needs ``check_same_thread=False`` because the FastAPI request
    handlers run in a threadpool while the background worker runs in its own
    thread; both use short-lived sessions from the same engine.
    """
    connect_args = {"check_same_thread": False} if database_url.startswith("sqlite") else {}
    return create_engine(database_url, connect_args=connect_args, future=True)


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Create a session factory bound to *engine*.

    ``expire_on_commit=False`` lets us read ORM attributes after the session
    closes (needed when returning the created job from a route handler).
    """
    return sessionmaker(bind=engine, expire_on_commit=False)
