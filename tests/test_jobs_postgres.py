"""Double-click protection on real Postgres: two sends at the same moment (D20).

Two requests with the same Idempotency-Key reach POST /jobs at the very same
instant: neither finds the other's job, so both try to save one. The
database's "one key per user" rule must refuse the second save, and that
request must answer with the first one's ticket: one job, one ticket. The
SQLite test in `tests/test_jobs.py` can only pretend this (a lookup that
misses once); only a real database server handles two requests truly at once
(D32).

Like `tests/test_budget_postgres.py`, this runs only when POSTGRES_TEST_URL
points at a Postgres database that may be wiped; otherwise it is skipped.
"""

import threading
import time

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app import jobs
from app.main import app
from app.models import Job, JobItem
from tests.test_budget_postgres import (  # noqa: F401 - fixtures used by name
    POSTGRES_TEST_URL,
    pg_app,
    pg_engine,
)

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(not POSTGRES_TEST_URL, reason="POSTGRES_TEST_URL not set"),
]

# How long each request pauses after looking for an earlier job with its key.
# Long enough that both requests surely look before either has saved.
PAUSE_SECONDS = 0.5


# The fixtures are imported above; pytest hands them over by name, which ruff
# sees as a redefinition (F811).
def test_two_sends_with_one_key_at_once_make_one_job(
    pg_app,  # noqa: F811
    pg_engine,  # noqa: F811
    monkeypatch,
):
    """Both requests get 202 and the same ticket; the database holds one job."""
    real_find = jobs.find_job_by_key

    def slow_find(*args):
        """Look up as usual, then pause, so the two requests overlap."""
        found = real_find(*args)
        time.sleep(PAUSE_SECONDS)
        return found

    monkeypatch.setattr(jobs, "find_job_by_key", slow_find)

    setup = TestClient(app)
    account = {"username": "ayse", "password": "gizli1234"}
    setup.post("/register", json=account)
    token = setup.post("/token", data=account).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}", "Idempotency-Key": "tik-1"}
    body = {"task": "normalise", "texts": ["Bir", "İki", "Üç"]}

    # A barrier is a starting line: both threads are let go at the same instant.
    start_together = threading.Barrier(2)
    replies = []

    def send() -> None:
        """Wait for the other thread, then send the job."""
        client = TestClient(app)
        start_together.wait()
        replies.append(client.post("/jobs", json=body, headers=headers))

    threads = [threading.Thread(target=send) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert [reply.status_code for reply in replies] == [202, 202]
    tickets = {reply.json()["id"] for reply in replies}
    assert len(tickets) == 1
    with Session(pg_engine) as session:
        assert len(session.exec(select(Job)).all()) == 1
        assert len(session.exec(select(JobItem)).all()) == 3
