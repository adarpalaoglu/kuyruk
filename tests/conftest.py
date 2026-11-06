"""Shared test helpers, loaded automatically by pytest.

A *fixture* is a helper that pytest hands to any test that names it as an
argument. The fixtures here give each test its own brand-new, empty SQLite
database kept in memory, so tests never touch a real database or each other,
and a `client` that sends pretend web requests to the app using that database.
"""

import os

# Kuyruk refuses to start without JWT_SECRET. Give the tests a throwaway one
# before any Kuyruk code is imported (importing reads the settings), so tests
# work in CI and never use the real secret from a local `.env`. (A value in the
# environment wins over the one in `.env`.)
os.environ["JWT_SECRET"] = "test-only-secret-not-used-anywhere-else-0123456789"

from collections.abc import Iterator  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import Engine  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402
from sqlmodel import Session, SQLModel, create_engine  # noqa: E402

# Importing the models registers the tables so `create_all` below can make them.
from app import models  # noqa: E402, F401
from app.db import enforce_sqlite_foreign_keys, get_session  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture
def engine() -> Iterator[Engine]:
    """An engine for a fresh in-memory SQLite database with all tables created.

    "sqlite://" with no file name means the database lives only in memory and
    disappears after the test. StaticPool keeps one single connection open;
    without it every new connection would see a different, empty database.
    """
    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    # Check foreign keys like Postgres does, so tests can't pass on rows that
    # Postgres would refuse.
    enforce_sqlite_foreign_keys(test_engine)
    SQLModel.metadata.create_all(test_engine)
    yield test_engine
    test_engine.dispose()


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    """A database session on the test database, closed after the test."""
    with Session(engine) as test_session:
        yield test_session


@pytest.fixture
def client(engine: Engine) -> Iterator[TestClient]:
    """A test client for the real app, using the test database.

    `dependency_overrides` swaps one dependency for another: every route that
    asks for `get_session` gets a session on the in-memory test database
    instead of the real one. The swap is undone after the test.
    """

    def get_test_session() -> Iterator[Session]:
        """Give each request a session on the test database."""
        with Session(engine) as test_session:
            yield test_session

    app.dependency_overrides[get_session] = get_test_session
    yield TestClient(app)
    app.dependency_overrides.clear()
