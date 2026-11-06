"""Shared test helpers, loaded automatically by pytest.

A *fixture* is a helper that pytest hands to any test that names it as an
argument. The fixtures here give each test its own brand-new, empty SQLite
database kept in memory, so tests never touch a real database or each other.
"""

from collections.abc import Iterator

import pytest
from sqlalchemy import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

# Importing the models registers the tables so `create_all` below can make them.
from app import models  # noqa: F401


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
    SQLModel.metadata.create_all(test_engine)
    yield test_engine
    test_engine.dispose()


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    """A database session on the test database, closed after the test."""
    with Session(engine) as test_session:
        yield test_session
