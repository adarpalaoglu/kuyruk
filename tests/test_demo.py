"""Tests for the demo crash, `POST /demo/crash-worker` (`app/demo.py`, D74).

The API side: the address doesn't exist outside demo mode (404, even before
the login check); in demo mode it puts a crash mark on the caller's oldest
running job. The worker side: in demo mode, a worker that sees the mark at a
heartbeat exits on the spot. A real exit would end the test run, so the tests
replace `crash_now` with a stand-in that only records the crash and stops the
worker. The real abrupt exit is shown by the Postgres kill test and the
manual run.
"""

import pytest
from sqlalchemy import update
from sqlmodel import Session, select

from app import worker as worker_module
from app.models import ItemState, Job, JobState, User
from app.settings import settings
from app.worker import take_over_stuck_job
from tests.test_recovery import make_running_job
from tests.test_worker import ScriptedAI, make_job, make_worker, reload, texts

CRASH = "/demo/crash-worker"


@pytest.fixture
def demo_mode(monkeypatch):
    """Switch demo mode on for one test."""
    monkeypatch.setattr(settings, "demo_mode", True)


def user_id(engine, username):
    """The id of the user with this username."""
    with Session(engine) as session:
        return session.exec(select(User.id).where(User.username == username)).one()


def running_job_of(engine, username, count=3, **changes):
    """Save a running job for `username` (claimed by "w1"), and return it."""
    with Session(engine) as session:
        owner = session.get(User, user_id(engine, username))
        job = make_running_job(
            session, texts(count), owner=owner, claimed_by="w1", **changes
        )
        # Load its fields now, so they can still be read after the session.
        session.refresh(job)
        session.expunge(job)
    return job


def crash_mark(engine, job):
    """Whether this job carries a demo crash mark."""
    return reload(engine, job)[0].crash_requested


def assert_error(reply, status_code, code):
    """The reply is an error in Kuyruk's one error shape, with a request ID."""
    assert reply.status_code == status_code
    assert reply.json()["error"]["code"] == code
    assert "x-request-id" in reply.headers


# --- The API side ------------------------------------------------------------


def test_outside_demo_mode_the_address_does_not_exist(client, log_in_as):
    """404 with and without login, exactly like an unknown address."""
    headers = log_in_as("ayse")
    unknown = client.post("/no-such-address")
    for reply in (client.post(CRASH), client.post(CRASH, headers=headers)):
        assert_error(reply, 404, "not_found")
        assert reply.json() == unknown.json()


def test_outside_demo_mode_nothing_is_marked(client, log_in_as, engine):
    """Even a logged-in user with a running job changes nothing."""
    headers = log_in_as("ayse")
    job = running_job_of(engine, "ayse")
    client.post(CRASH, headers=headers)
    assert crash_mark(engine, job) is False


def test_outside_demo_mode_it_is_not_on_the_docs_page(client):
    """The address isn't listed on /docs when demo mode is off (the default)."""
    assert CRASH not in client.get("/openapi.json").json()["paths"]


def test_demo_mode_needs_a_login(client, demo_mode):
    """In demo mode, no login pass gives 401."""
    assert_error(client.post(CRASH), 401, "not_authenticated")


def test_no_running_job_is_409(client, log_in_as, engine, demo_mode):
    """Only a queued job: nothing to crash, so 409 `no_running_job`."""
    headers = log_in_as("ayse")
    with Session(engine) as session:
        make_job(session, ["a"], owner=session.get(User, user_id(engine, "ayse")))
    assert_error(client.post(CRASH, headers=headers), 409, "no_running_job")


def test_crash_marks_your_oldest_running_job(client, log_in_as, engine, demo_mode):
    """202 with the ticket; only the oldest of your running jobs is marked."""
    headers = log_in_as("ayse")
    older = running_job_of(engine, "ayse", seconds_ago=60)
    newer = running_job_of(engine, "ayse", seconds_ago=10)
    reply = client.post(CRASH, headers=headers)
    assert reply.status_code == 202
    assert reply.json()["ticket"] == str(older.id)
    assert "stop abruptly" in reply.json()["message"]
    assert crash_mark(engine, older) is True
    assert crash_mark(engine, newer) is False


def test_another_users_job_is_never_marked(client, log_in_as, engine, demo_mode):
    """Mehmet has no running job; Ayşe's running job is left alone."""
    log_in_as("ayse")
    ayse_job = running_job_of(engine, "ayse")
    mehmet = log_in_as("mehmet")
    assert_error(client.post(CRASH, headers=mehmet), 409, "no_running_job")
    assert crash_mark(engine, ayse_job) is False


def test_a_job_that_finished_meanwhile_is_not_marked(
    client, log_in_as, engine, demo_mode, monkeypatch
):
    """The job ends between the look and the mark: 409, no mark."""
    headers = log_in_as("ayse")
    job = running_job_of(engine, "ayse")
    real_execute = Session.execute
    finished = []

    def finish_first(self, statement, *args, **kwargs):
        """Just before the mark is written, the job finishes."""
        if not finished and statement.is_dml and statement.table.name == "jobs":
            finished.append(True)
            with Session(engine) as other:
                other.execute(
                    update(Job).where(Job.id == job.id).values(state=JobState.DONE)
                )
                other.commit()
        return real_execute(self, statement, *args, **kwargs)

    monkeypatch.setattr(Session, "execute", finish_first)
    assert_error(client.post(CRASH, headers=headers), 409, "no_running_job")
    monkeypatch.undo()
    assert crash_mark(engine, job) is False


# --- The worker side ---------------------------------------------------------


class SimulatedCrash(Exception):
    """Raised by the stand-in for the real exit, which never returns."""


def mark(engine, job):
    """Put a demo crash mark on the job, as the API does."""
    with Session(engine) as session:
        session.execute(
            update(Job).where(Job.id == job.id).values(crash_requested=True)
        )
        session.commit()


def record_crashes(monkeypatch):
    """Replace the real exit with a recorder; return the list of crashes."""
    crashes = []
    monkeypatch.setattr(worker_module, "crash_now", lambda: crashes.append(True))
    return crashes


def test_a_marked_job_makes_its_worker_crash_in_demo_mode(engine, session, monkeypatch):
    """At the heartbeat, the worker sees the mark and exits."""
    crashes = record_crashes(monkeypatch)
    job = make_running_job(session, texts(2), claimed_by="w1", seconds_ago=0)
    mark(engine, job)
    make_worker(engine, ScriptedAI(), worker_id="w1", demo_mode=True).beat(job.id)
    assert crashes == [True]


@pytest.mark.anyio
async def test_the_mark_is_also_seen_before_the_next_text(engine, session, monkeypatch):
    """No heartbeat in time: the worker still crashes before its next text.

    The heartbeat is a minute away, but the job is marked while text 1 is
    with the AI; the worker crashes before starting text 2.
    """
    job = make_job(session, texts(3))

    async def mark_during_text_1(text):
        """The button is pressed while text 1 is with the AI."""
        if text == "metin 1":
            mark(engine, job)

    def crash():
        """Like a real exit, never carry on: end the run with an error."""
        raise SimulatedCrash

    monkeypatch.setattr(worker_module, "crash_now", crash)
    worker = make_worker(
        engine,
        ScriptedAI(on_call=mark_during_text_1),
        texts_at_once=1,
        heartbeat_seconds=60,
        demo_mode=True,
    )
    with pytest.raises(SimulatedCrash):
        await worker.run_one_job()
    _, items = reload(engine, job)
    assert [item.state for item in items] == [ItemState.OK] + [ItemState.WAITING] * 2


def test_outside_demo_mode_the_worker_ignores_the_mark(engine, session, monkeypatch):
    """Double lock: a worker not in demo mode never crashes on purpose."""
    crashes = record_crashes(monkeypatch)
    job = make_running_job(session, texts(2), claimed_by="w1", seconds_ago=0)
    mark(engine, job)
    make_worker(engine, ScriptedAI(), worker_id="w1", demo_mode=False).beat(job.id)
    assert crashes == []


@pytest.mark.anyio
async def test_outside_demo_mode_no_crash_before_a_text(engine, session, monkeypatch):
    """Outside demo mode, a marked job is simply worked through to done."""
    crashes = record_crashes(monkeypatch)
    job = make_job(session, texts(2))
    mark(engine, job)
    await make_worker(engine, ScriptedAI(), demo_mode=False).run_one_job()
    assert crashes == []
    assert reload(engine, job)[0].state == JobState.DONE


def test_no_mark_no_crash(engine, session, monkeypatch):
    """An ordinary heartbeat in demo mode doesn't crash."""
    crashes = record_crashes(monkeypatch)
    job = make_running_job(session, texts(2), claimed_by="w1", seconds_ago=0)
    make_worker(engine, ScriptedAI(), worker_id="w1", demo_mode=True).beat(job.id)
    assert crashes == []


def test_the_worker_taking_over_clears_the_mark(engine, session):
    """So the new worker doesn't crash too."""
    job = make_running_job(session, texts(2))
    mark(engine, job)
    taken = take_over_stuck_job(session, "w2", stale_after_seconds=30)
    assert taken.crash_requested is False


@pytest.mark.anyio
async def test_crash_then_recovery_end_to_end(client, log_in_as, engine, demo_mode):
    """Button pressed mid-job: the worker crashes, a second one finishes the job.

    Worker w1 does texts one at a time. During text 2 the button is pressed;
    at its next heartbeat or text w1 "crashes" (here: its run ends with an
    error, with no clean-up, like a real exit). The job is left
    running with texts waiting. After the stale time, worker w2 takes over and
    finishes, with crash count 1 and every text ok.
    """
    headers = log_in_as("ayse")
    with Session(engine) as session:
        owner = session.get(User, user_id(engine, "ayse"))
        job = make_job(session, texts(6), owner=owner)
        # Load its fields now, so they can still be read after the session.
        session.refresh(job)
        session.expunge(job)

    def crash():
        """Instead of exiting the test run: end w1's run at once, no clean-up."""
        raise SimulatedCrash

    async def press_button_on_text_2(text):
        """The user presses "simulate a crash" while text 2 is with the AI."""
        if text == "metin 2":
            assert client.post(CRASH, headers=headers).status_code == 202

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(worker_module, "crash_now", crash)
        w1 = make_worker(
            engine,
            ScriptedAI(wait=0.05, on_call=press_button_on_text_2),
            worker_id="w1",
            texts_at_once=1,
            heartbeat_seconds=0.01,
            demo_mode=True,
        )
        with pytest.raises(SimulatedCrash):
            await w1.run_one_job()
    crashed, items = reload(engine, job)
    assert crashed.state == JobState.RUNNING
    assert crashed.crash_requested is True
    assert any(item.state == ItemState.WAITING for item in items)

    w2 = make_worker(engine, ScriptedAI(), worker_id="w2", stale_after_seconds=0.05)
    with Session(engine) as session:
        session.execute(update(Job).values(last_active_at=crashed.started_at))
        session.commit()
    assert await w2.run_one_job() is True
    done, items = reload(engine, job)
    assert done.state == JobState.DONE
    assert done.crash_count == 1
    assert done.claimed_by == "w2"
    assert done.crash_requested is False
    assert all(item.state == ItemState.OK for item in items)
