"""Tests for `app/db.py`: table creation on startup and the per-request session."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect
from sqlmodel import Session

from app import db
from app.main import app


def test_startup_creates_the_three_tables(tmp_path, monkeypatch):
    # Point the app at an empty database file in a temporary folder. A file is
    # used rather than memory because the app starts up on another thread, and
    # an in-memory database can't be seen from a different thread.
    empty_engine = db.make_engine(f"sqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setattr(db, "engine", empty_engine)
    assert inspect(empty_engine).get_table_names() == []

    # TestClient used in a `with` block runs the start-up code (the lifespan).
    with TestClient(app):
        pass

    tables = inspect(empty_engine).get_table_names()
    assert sorted(tables) == ["job_items", "jobs", "users"]
    empty_engine.dispose()


class SpySession(Session):
    """A normal session that also remembers whether it was closed."""

    was_closed = False

    def close(self) -> None:
        """Record the close, then close as usual."""
        self.was_closed = True
        super().close()


def test_get_session_closes_session_after_request(engine, monkeypatch):
    monkeypatch.setattr(db, "engine", engine)
    monkeypatch.setattr(db, "Session", SpySession)
    sessions = db.get_session()

    session = next(sessions)
    assert session.was_closed is False
    # Finishing the generator is what FastAPI does when a request ends.
    with pytest.raises(StopIteration):
        next(sessions)

    assert session.was_closed is True


def test_get_session_closes_session_when_request_fails(engine, monkeypatch):
    monkeypatch.setattr(db, "engine", engine)
    monkeypatch.setattr(db, "Session", SpySession)
    sessions = db.get_session()
    session = next(sessions)

    # This is what FastAPI does when the route raises an error.
    with pytest.raises(RuntimeError):
        sessions.throw(RuntimeError("route failed"))

    assert session.was_closed is True


def test_make_engine_for_postgres_url_has_no_sqlite_options():
    # Only builds the engine object; it doesn't connect, so no server is needed.
    pg_engine = db.make_engine("postgresql+psycopg://u:p@localhost:5432/kuyruk")

    assert pg_engine.dialect.name == "postgresql"
    assert pg_engine.dialect.driver == "psycopg"
