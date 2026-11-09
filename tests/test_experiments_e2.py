"""Tests for E2's broken design: a job run inside the API (`app/experiments_e2.py`).

- Off by default: the address answers 404 in Kuyruk's error shape and is
  hidden from /docs.
- On: a queued job of yours is claimed for the API and done by
  BackgroundTasks, with the worker's own code (every text ok, one "text
  started" line per text, D75); login required; someone else's job is 404;
  a job that is not queued is 409.
"""

import logging

import pytest
from sqlmodel import select

from app import experiments_e2
from app.ai import FakeAI
from app.main import app
from app.models import ItemState, Job, JobItem, JobState
from app.settings import settings
from app.states import change_job_state

ADDRESS = "/experiments/e2/jobs/{}/run-in-api"


@pytest.fixture
def e2_on(monkeypatch):
    """Switch the E2 address on for one test, with a fake AI that doesn't wait.

    app/main.py adds it only when EXPERIMENTS_ENABLED is true at start-up,
    which it isn't in tests; so this adds it for one test, as start-up would,
    and takes it away again afterwards.
    """
    monkeypatch.setattr(settings, "experiments_enabled", True)
    fake = FakeAI(
        wait_mode="fixed",
        wait_seconds=0,
        replay_file="",
        seed=1,
        answer="Özet.",
        fail_every=0,
        fail_kind="temporary",
    )
    monkeypatch.setattr(experiments_e2, "ai_override", fake)
    routes = list(app.router.routes)
    app.include_router(experiments_e2.router)
    yield
    app.router.routes[:] = routes


def send_job(client, headers, count=6):
    """Send a job with `count` texts for the AI and return its ticket."""
    body = {"task": "llm", "texts": [f"Metin {n}." for n in range(1, count + 1)]}
    reply = client.post("/jobs", json=body, headers=headers)
    assert reply.status_code == 202
    return reply.json()["id"]


def assert_error(reply, status_code, code):
    """The reply is an error in Kuyruk's one error shape, with a request ID."""
    assert reply.status_code == status_code
    assert reply.json()["error"]["code"] == code
    assert "x-request-id" in reply.headers


def test_address_is_404_when_off(client, log_in_as):
    """Switched off, the address looks like one that doesn't exist."""
    headers = log_in_as("ayse")
    ticket = send_job(client, headers)
    assert_error(client.post(ADDRESS.format(ticket), headers=headers), 404, "not_found")


def test_address_is_hidden_from_docs(client):
    """Nothing under /experiments/e2 appears in the API description (/docs)."""
    paths = client.get("/openapi.json").json()["paths"]
    assert not [path for path in paths if path.startswith("/experiments/e2")]


def test_address_exists_only_when_switched_on_at_start(monkeypatch):
    """app/main.py adds the E2 address only when the setting is on (D95)."""
    import importlib

    from fastapi.testclient import TestClient

    from app import main

    def status_without_login() -> int:
        """Load app/main.py afresh and call the address without logging in."""
        fresh = importlib.reload(main).app
        return TestClient(fresh).post(ADDRESS.format("x")).status_code

    try:
        monkeypatch.setattr(settings, "experiments_enabled", True)
        # It exists: the login check answers (401), not "no such address".
        assert status_without_login() == 401
    finally:
        monkeypatch.setattr(settings, "experiments_enabled", False)
        assert status_without_login() == 404


def test_job_runs_inside_the_api_to_done(client, log_in_as, e2_on, engine, caplog):
    """The job is done by BackgroundTasks after the reply, like the worker would.

    The test client runs background tasks before it hands back the reply, so
    the job is already finished when the test looks.
    """
    headers = log_in_as("ayse")
    ticket = send_job(client, headers, count=6)
    with caplog.at_level(logging.INFO, logger="kuyruk.worker"):
        reply = client.post(ADDRESS.format(ticket), headers=headers)
    assert reply.status_code == 202
    body = reply.json()
    # The reply shows the job as claimed, before any text is done.
    assert body["state"] == "running"
    assert "claimed_by" not in body
    job = client.get(f"/jobs/{ticket}", headers=headers).json()
    assert job["state"] == "done"
    assert job["succeeded"] == 6
    results = client.get(f"/jobs/{ticket}/results", headers=headers).json()
    assert [row["state"] for row in results] == ["ok"] * 6
    # One "text started" line per text, with the API's name on it (D75).
    started = [r.getMessage() for r in caplog.records if "text started" in r.message]
    assert len(started) == 6
    assert all("worker=api-" in line for line in started)


def test_claimed_with_the_apis_name(client, log_in_as, e2_on, session):
    """The job's claimed_by starts with "api-", so the logs and data show who."""
    headers = log_in_as("ayse")
    ticket = send_job(client, headers, count=2)
    client.post(ADDRESS.format(ticket), headers=headers)
    job = session.exec(select(Job)).one()
    assert job.claimed_by.startswith("api-")
    assert job.started_at is not None
    items = session.exec(select(JobItem)).all()
    assert {item.state for item in items} == {ItemState.OK}


def test_login_required(client, log_in_as, e2_on):
    """Without a login the address answers 401."""
    headers = log_in_as("ayse")
    ticket = send_job(client, headers)
    assert_error(client.post(ADDRESS.format(ticket)), 401, "not_authenticated")


def test_someone_elses_job_is_404(client, log_in_as, e2_on):
    """Another user's job is 404, never 403, and is left queued."""
    owner = log_in_as("ayse")
    ticket = send_job(client, owner)
    other = log_in_as("mehmet")
    assert_error(client.post(ADDRESS.format(ticket), headers=other), 404, "not_found")
    assert client.get(f"/jobs/{ticket}", headers=owner).json()["state"] == "queued"


def test_unknown_ticket_is_404(client, log_in_as, e2_on):
    """A ticket that doesn't exist (or isn't a ticket at all) is 404."""
    headers = log_in_as("ayse")
    for ticket in ["00000000-0000-0000-0000-000000000000", "not-a-ticket"]:
        reply = client.post(ADDRESS.format(ticket), headers=headers)
        assert_error(reply, 404, "not_found")


@pytest.mark.parametrize("state", [JobState.RUNNING, JobState.CANCELLED])
def test_job_not_queued_is_409(client, log_in_as, e2_on, session, state):
    """A job a worker already has, or one that has ended, can't be run here."""
    headers = log_in_as("ayse")
    ticket = send_job(client, headers)
    job = session.exec(select(Job)).one()
    change_job_state(session, job.id, JobState.QUEUED, state)
    reply = client.post(ADDRESS.format(ticket), headers=headers)
    assert_error(reply, 409, "job_not_queued")
    assert state in reply.json()["error"]["message"]
