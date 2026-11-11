"""Tests for `worker_busy` and the release after a cancel (D130).

After a cancel, the texts already with the AI finish and keep their results;
until then they still look "waiting". The interface must not offer to send
them again meanwhile, so the job reply says whether a worker still holds the
job (`worker_busy`), and the worker lets go of a cancelled job once its last
texts have finished: one write, only for cancelled jobs.
"""

from datetime import timedelta

import pytest
from sqlmodel import Session

from app.models import ItemState, Job, JobState, utc_now
from app.settings import settings
from tests.test_cancel import cancel, job_row, send_job
from tests.test_worker import ScriptedAI, make_worker

pytestmark = pytest.mark.anyio


def read(client, headers, ticket):
    """The job as the API shows it."""
    return client.get(f"/jobs/{ticket}", headers=headers).json()


async def test_busy_while_cancelled_texts_finish_then_released(
    client, log_in_as, engine
):
    """Pressing during the in-flight seconds: busy; afterwards released."""
    headers = log_in_as("ayse")
    ticket = send_job(client, headers, count=20)
    seen = {}

    async def cancel_at_ten(text):
        """Cancel when text 10 reaches the AI, then look at the job at once."""
        if text == "metin 10":
            seen["cancel"] = cancel(client, headers, ticket).json()
            seen["right_after"] = read(client, headers, ticket)

    ai = ScriptedAI(on_call=cancel_at_ten)
    await make_worker(engine, ai).run_one_job()
    # During the in-flight seconds: cancelled, but the worker still holds it,
    # and texts it is working on still look waiting.
    assert seen["cancel"]["state"] == "cancelled"
    assert seen["cancel"]["worker_busy"] is True
    assert seen["right_after"]["worker_busy"] is True
    # Afterwards: those texts finished with results, and the job is free.
    after = read(client, headers, ticket)
    assert after["state"] == "cancelled"
    assert after["worker_busy"] is False
    job, items = job_row(engine, ticket)
    assert job.claimed_by is None
    started = len(ai.received)
    assert all(item.state == ItemState.OK for item in items[:started])
    assert all(item.state == ItemState.WAITING for item in items[started:])


async def test_busy_while_running_and_not_after_done(client, log_in_as, engine):
    """A running job is busy; a done job isn't (and keeps its worker's name)."""
    headers = log_in_as("ayse")
    ticket = send_job(client, headers, count=3)
    seen = []

    async def look(text):
        """Read the job while a text is with the AI."""
        seen.append(read(client, headers, ticket)["worker_busy"])

    await make_worker(engine, ScriptedAI(on_call=look)).run_one_job()
    assert seen and all(seen)
    assert read(client, headers, ticket)["worker_busy"] is False
    # No release write for a job that wasn't cancelled (E2's counts stay).
    job, _ = job_row(engine, ticket)
    assert job.state == JobState.DONE
    assert job.claimed_by == "test-worker"


def test_a_job_cancelled_while_queued_is_never_busy(client, log_in_as):
    headers = log_in_as("ayse")
    ticket = send_job(client, headers)
    assert cancel(client, headers, ticket).json()["worker_busy"] is False
    assert read(client, headers, ticket)["worker_busy"] is False


def job_with(state, claimed_by="w1", seconds_ago=0.0):
    """A job in `state`, held by `claimed_by`, last heartbeat `seconds_ago`."""
    return Job(
        owner_id=1,
        task="llm",
        state=state,
        claimed_by=claimed_by,
        last_active_at=utc_now() - timedelta(seconds=seconds_ago),
    )


@pytest.mark.parametrize(
    ("state", "claimed_by", "seconds_ago", "busy"),
    [
        (JobState.RUNNING, "w1", 0, True),
        (JobState.CANCELLED, "w1", 0, True),
        # Handed back, or released after a cancel: nobody holds it.
        (JobState.RUNNING, None, 0, False),
        (JobState.CANCELLED, None, 0, False),
        # Finished jobs keep their worker's name but nothing can change.
        (JobState.DONE, "w1", 0, False),
        (JobState.FAILED, "w1", 0, False),
        (JobState.QUEUED, None, 0, False),
        # A worker that crashed before letting go: free after the stale time.
        (JobState.CANCELLED, "w1", 10_000, False),
    ],
)
def test_worker_busy_rule(state, claimed_by, seconds_ago, busy):
    # Made inside the test, so the heartbeat age is measured from now.
    job = job_with(state, claimed_by=claimed_by, seconds_ago=seconds_ago)
    assert job.worker_busy is busy


def test_no_heartbeat_yet_is_not_busy():
    job = job_with(JobState.RUNNING)
    job.last_active_at = None
    assert job.worker_busy is False


def test_busy_just_inside_the_stale_time():
    seconds = settings.stale_after_seconds
    assert job_with(JobState.CANCELLED, seconds_ago=seconds - 1).worker_busy is True
    assert job_with(JobState.CANCELLED, seconds_ago=seconds + 1).worker_busy is False


def test_release_only_touches_our_cancelled_job(client, log_in_as, engine):
    """Released only if still cancelled and still this worker's."""
    headers = log_in_as("ayse")
    ticket = send_job(client, headers)
    cancel(client, headers, ticket)
    job, _ = job_row(engine, ticket)
    with Session(engine) as session:
        row = session.get(Job, job.id)
        row.claimed_by = "someone-else"
        session.add(row)
        session.commit()
    make_worker(engine, ScriptedAI()).release_cancelled(job.id)
    job, _ = job_row(engine, ticket)
    assert job.claimed_by == "someone-else"
