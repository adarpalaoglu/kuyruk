"""The budget race test on real Postgres (spec 3.1, D14, D61).

Two `llm` jobs from one user are sent at exactly the same moment. Each fits
on its own, but together they are over the budget. Exactly one may be
accepted. This needs real Postgres, because only a real database server
handles two requests truly at once with real row locks (D32).

The test runs only when POSTGRES_TEST_URL points at a Postgres database that
may be wiped (all tables are dropped and made again), for example:

    POSTGRES_TEST_URL=postgresql+psycopg://kuyruk:<password>@localhost:5432/kuyruk_test

Without it the test is skipped, so the normal test run needs no Postgres.
"""

import os
import threading
import time
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlmodel import Session, SQLModel, create_engine, select

from app import budget
from app.db import get_session
from app.main import app
from app.models import Job
from app.settings import settings
from app.tokens import count_tokens

POSTGRES_TEST_URL = os.environ.get("POSTGRES_TEST_URL")

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(not POSTGRES_TEST_URL, reason="POSTGRES_TEST_URL not set"),
]

REVIEW = "Kargo çok hızlı geldi, ürün de güzel ama kutusu ezikti."

# How long each request pauses between "add up today's use" and "save". Long
# enough that, without the lock, both requests would surely be adding up at
# the same time and both see the whole budget free.
PAUSE_SECONDS = 0.5


@pytest.fixture
def pg_engine() -> Iterator[Engine]:
    """An engine for the Postgres test database, with fresh empty tables."""
    engine = create_engine(POSTGRES_TEST_URL)
    SQLModel.metadata.drop_all(engine)
    SQLModel.metadata.create_all(engine)
    yield engine
    SQLModel.metadata.drop_all(engine)
    engine.dispose()


@pytest.fixture
def pg_app(pg_engine: Engine) -> Iterator[None]:
    """Point the app's `get_session` at the Postgres test database."""

    def get_pg_session() -> Iterator[Session]:
        """Give each request its own session (and connection) on Postgres."""
        with Session(pg_engine) as session:
            yield session

    app.dependency_overrides[get_session] = get_pg_session
    yield
    app.dependency_overrides.clear()


def test_two_jobs_at_once_over_budget_only_one_is_accepted(
    pg_app, pg_engine, monkeypatch
):
    """Two jobs sent at the same moment that together exceed the budget.

    Each job fits alone (the budget is 1.5 jobs). Without the lock, both
    requests would add up "0 used" before either saved, and both would pass.
    With it, the second waits until the first is saved, then sees it.
    """
    needed = count_tokens(REVIEW)
    monkeypatch.setattr(settings, "daily_token_budget", needed + needed // 2)

    # Slow down "add up today's use" so the two requests surely overlap.
    real_tokens_used_today = budget.tokens_used_today

    def slow_tokens_used_today(*args):
        """Add up as usual, then pause before the caller saves."""
        used = real_tokens_used_today(*args)
        time.sleep(PAUSE_SECONDS)
        return used

    monkeypatch.setattr(budget, "tokens_used_today", slow_tokens_used_today)

    setup = TestClient(app)
    account = {"username": "ayse", "password": "gizli1234"}
    setup.post("/register", json=account)
    token = setup.post("/token", data=account).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # A barrier is a starting line: each thread waits at it until both have
    # arrived, then both are let go at the same instant.
    start_together = threading.Barrier(2)
    replies = []

    def send() -> None:
        """Wait for the other thread, then send one llm job."""
        client = TestClient(app)
        start_together.wait()
        reply = client.post(
            "/jobs", json={"task": "llm", "texts": [REVIEW]}, headers=headers
        )
        replies.append(reply)

    threads = [threading.Thread(target=send) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sorted(reply.status_code for reply in replies) == [202, 429]
    refused = next(reply for reply in replies if reply.status_code == 429)
    assert refused.json()["error"]["code"] == "budget_exceeded"
    with Session(pg_engine) as session:
        assert len(session.exec(select(Job)).all()) == 1
