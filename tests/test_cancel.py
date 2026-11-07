"""Tests for cancelling a job: `POST /jobs/{ticket}/cancel` with the worker.

Requests go through the real app (the `client` fixture); the worker runs in the
same test with the pretend `ScriptedAI` from test_worker.py. The cases that
matter most are the races: a cancel that arrives while the worker is writing
"done", or while it is claiming the job, must still end cancelled (D51).
"""

import uuid
from types import SimpleNamespace

import pytest

from app import jobs
from app.models import ItemState, JobState
from app.states import change_job_state
from app.worker import claim_next_job
from tests.test_worker import ScriptedAI, make_worker, reload, texts

pytestmark = pytest.mark.anyio


def send_job(client, headers, count=3, task="llm"):
    """Send a job with `count` numbered texts and return its ticket."""
    body = {"task": task, "texts": texts(count)}
    return client.post("/jobs", json=body, headers=headers).json()["id"]


def cancel(client, headers, ticket):
    """Ask to cancel `ticket` and return the reply."""
    return client.post(f"/jobs/{ticket}/cancel", headers=headers)


def job_row(engine, ticket):
    """The job and its texts, read fresh from the database."""
    # `reload` only needs something with the job's id.
    return reload(engine, SimpleNamespace(id=uuid.UUID(ticket)))


async def test_cancel_queued_job_at_once(client, log_in_as, engine):
    """A queued job is cancelled at once, and the worker never touches it."""
    headers = log_in_as("ayse")
    ticket = send_job(client, headers)
    reply = cancel(client, headers, ticket)
    assert reply.status_code == 200
    assert reply.json()["state"] == "cancelled"
    assert reply.json()["finished_at"] is not None
    ai = ScriptedAI()
    assert await make_worker(engine, ai).run_one_job() is False
    job, items = job_row(engine, ticket)
    assert job.state == JobState.CANCELLED
    assert ai.received == []
    assert all(item.state == ItemState.WAITING for item in items)


async def test_cancel_mid_run_keeps_finished_texts(client, log_in_as, engine):
    """Cancel while text 10 of 20 is with the AI.

    Every text started before the cancel finishes and keeps its result. With
    4 at once that is texts 1-10, plus up to 3 more if several places freed up
    at the same moment and the worker started them just before the cancel
    arrived. Every text after those is never started and stays waiting.
    Nothing is lost: each text is either ok with a result or untouched.
    """
    headers = log_in_as("ayse")
    ticket = send_job(client, headers, count=20)

    async def cancel_at_ten(text):
        """Press "cancel" when the 10th text reaches the AI."""
        if text == "metin 10":
            assert cancel(client, headers, ticket).status_code == 200

    ai = ScriptedAI(on_call=cancel_at_ten)
    await make_worker(engine, ai).run_one_job()
    job, items = job_row(engine, ticket)
    assert job.state == JobState.CANCELLED
    started = len(ai.received)
    assert 10 <= started <= 13
    assert ai.received == texts(started)
    assert all(item.state == ItemState.OK and item.result for item in items[:started])
    assert all(
        item.state == ItemState.WAITING and item.tries == 0 for item in items[started:]
    )
    assert (job.succeeded, job.failed, job.total) == (started, 0, 20)
    # The results page shows the same thing to the user.
    results = client.get(f"/jobs/{ticket}/results", headers=headers).json()
    assert [row["state"] for row in results] == ["ok"] * started + ["waiting"] * (
        20 - started
    )


async def test_cancel_during_final_write_still_ends_cancelled(
    client, log_in_as, engine
):
    """The cancel arrives after the last text, just before "done" is written.

    The worker's "running -> done" only works if the job is still running, so
    it does nothing and the cancel stays.
    """
    headers = log_in_as("ayse")
    ticket = send_job(client, headers, count=1)
    worker = make_worker(engine, ScriptedAI())
    real_finish = worker.finish_job

    def cancel_then_finish(job_id, reason):
        """Let the user cancel at the very moment the worker finishes."""
        assert cancel(client, headers, ticket).status_code == 200
        real_finish(job_id, reason)

    worker.finish_job = cancel_then_finish
    await worker.run_one_job()
    job, items = job_row(engine, ticket)
    assert job.state == JobState.CANCELLED
    assert items[0].state == ItemState.OK


def test_cancel_while_worker_claims_still_ends_cancelled(
    client, log_in_as, engine, session, monkeypatch
):
    """The worker claims the job between the cancel's read and its write.

    The cancel's "queued -> cancelled" then does nothing; it reads the job
    again, sees running, and cancels that instead.
    """
    headers = log_in_as("ayse")
    ticket = send_job(client, headers)
    calls = []

    def worker_claims_first(session_, job_id, from_state, to_state, **fields):
        """On the cancel's first try, let the worker claim the job just before."""
        if not calls:
            claim_next_job(session, "test-worker")
        calls.append(from_state)
        return change_job_state(session_, job_id, from_state, to_state, **fields)

    monkeypatch.setattr(jobs, "change_job_state", worker_claims_first)
    reply = cancel(client, headers, ticket)
    assert reply.status_code == 200
    assert reply.json()["state"] == "cancelled"
    assert calls == [JobState.QUEUED, JobState.RUNNING]


@pytest.mark.parametrize("final", [JobState.DONE, JobState.FAILED])
def test_cancel_finished_job_is_409(client, log_in_as, session, final):
    """A done or failed job can't be cancelled: 409 in the shared error shape."""
    headers = log_in_as("ayse")
    ticket = send_job(client, headers)
    job_id = uuid.UUID(ticket)
    change_job_state(session, job_id, JobState.QUEUED, JobState.RUNNING)
    change_job_state(session, job_id, JobState.RUNNING, final)
    reply = cancel(client, headers, ticket)
    assert reply.status_code == 409
    assert reply.json() == {
        "error": {
            "code": "job_finished",
            "message": f"This job has already finished ({final}).",
        }
    }
    assert "X-Request-ID" in reply.headers


def test_cancel_twice_is_409(client, log_in_as):
    """Cancelling an already cancelled job is 409 too."""
    headers = log_in_as("ayse")
    ticket = send_job(client, headers)
    assert cancel(client, headers, ticket).status_code == 200
    reply = cancel(client, headers, ticket)
    assert reply.status_code == 409
    assert reply.json()["error"]["code"] == "job_finished"


def test_cancel_other_users_job_is_404_and_changes_nothing(client, log_in_as):
    """Mehmet can't cancel Ayşe's job; he gets the same 404 as for no job."""
    ayse = log_in_as("ayse")
    ticket = send_job(client, ayse)
    reply = cancel(client, log_in_as("mehmet"), ticket)
    assert reply.status_code == 404
    assert reply.json()["error"]["code"] == "not_found"
    assert client.get(f"/jobs/{ticket}", headers=ayse).json()["state"] == "queued"


@pytest.mark.parametrize("ticket", [str(uuid.uuid4()), "not-a-ticket"])
def test_cancel_missing_or_malformed_ticket_is_404(client, log_in_as, ticket):
    """A ticket that doesn't exist, or isn't a ticket at all, is 404."""
    reply = cancel(client, log_in_as("ayse"), ticket)
    assert reply.status_code == 404
    assert reply.json()["error"]["code"] == "not_found"


def test_cancel_needs_login(client):
    """Without logging in, cancel is 401."""
    assert cancel(client, {}, str(uuid.uuid4())).status_code == 401
