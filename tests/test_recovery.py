"""Tests for crash recovery in `app/worker.py` (spec 7.1, D68-D75), on SQLite.

A crash is set up by hand: a job is saved as `running`, claimed by a worker
called "dead-worker", with a heartbeat (`last_active_at`) some seconds in the
past and some texts already finished. Then a new worker looks for work, and
the tests check what it takes over, what it redoes, and what it leaves alone.

The Postgres tests with real worker processes and a real `kill -9` are in
`tests/test_recovery_postgres.py`.
"""

import asyncio
import logging
from datetime import timedelta

import pytest
from sqlalchemy import update
from sqlmodel import Session

from app import worker as worker_module
from app.ai import TemporaryAIError
from app.models import ItemState, Job, JobItem, JobState, utc_now
from app.settings import Settings
from app.states import change_item_state, change_job_state
from app.worker import CRASHED_TOO_OFTEN, claim_next_job, take_over_stuck_job
from tests.test_worker import (
    ANSWER,
    ScriptedAI,
    make_job,
    make_user,
    make_worker,
    reload,
    texts,
)

pytestmark = pytest.mark.anyio

STALE = 30.0


def make_running_job(
    session,
    job_texts,
    *,
    seconds_ago=60.0,
    claimed_by="dead-worker",
    crash_count=0,
    finished=None,
    tries=None,
    owner=None,
    state=JobState.RUNNING,
) -> Job:
    """Save a job as a crashed worker left it.

    `seconds_ago` is the age of its last heartbeat. `finished` maps a
    position to "ok" or "failed" for texts the dead worker already finished;
    `tries` maps a position to tries already used by a still-waiting text.
    """
    job = make_job(session, job_texts, owner=owner)
    finished = finished or {}
    tries = tries or {}
    session.execute(
        update(Job)
        .where(Job.id == job.id)
        .values(
            state=state,
            claimed_by=claimed_by,
            crash_count=crash_count,
            started_at=utc_now() - timedelta(seconds=seconds_ago + 5),
            last_active_at=utc_now() - timedelta(seconds=seconds_ago),
            succeeded=sum(1 for done in finished.values() if done == "ok"),
            failed=sum(1 for done in finished.values() if done == "failed"),
        )
    )
    for position, done in finished.items():
        session.execute(
            update(JobItem)
            .where(JobItem.job_id == job.id, JobItem.position == position)
            .values(
                state=ItemState(done),
                result="dead worker's answer" if done == "ok" else None,
                error_message="dead worker's error" if done == "failed" else None,
                tries=1,
            )
        )
    for position, used in tries.items():
        session.execute(
            update(JobItem)
            .where(JobItem.job_id == job.id, JobItem.position == position)
            .values(tries=used)
        )
    session.commit()
    return job


# --- Stuck or still working? (D68) -----------------------------------------


def test_a_fresh_heartbeat_is_never_taken_over(session):
    """29 s without a beat, with "stale after" 30 s: still working, left alone."""
    job = make_running_job(session, texts(3), seconds_ago=STALE - 1)
    assert take_over_stuck_job(session, "w2", stale_after_seconds=STALE) is None
    session.refresh(job)
    assert job.claimed_by == "dead-worker"
    assert job.crash_count == 0


def test_a_stale_heartbeat_is_taken_over(session):
    """31 s without a beat: stuck. The new worker takes it and beats at once."""
    job = make_running_job(session, texts(3), seconds_ago=STALE + 1)
    taken = take_over_stuck_job(session, "w2", stale_after_seconds=STALE)
    assert taken.id == job.id
    assert taken.state == JobState.RUNNING
    assert taken.claimed_by == "w2"
    assert taken.crash_count == 1
    assert utc_now() - taken.last_active_at < timedelta(seconds=5)
    assert taken.resumed_from_position == 1


async def test_a_slow_worker_that_keeps_beating_is_never_taken_over(engine, session):
    """A text that waits long for the AI doesn't stop the heartbeat (D68).

    Each AI call takes over 0.3 s while "stale after" is 0.15 s: without the
    heartbeat the job would look stuck. Another worker looks 0.25 s into
    every call and never finds it stuck.
    """
    looked = []

    async def other_worker_looks(text):
        """Well into the AI call, have a second worker look for stuck jobs."""
        await asyncio.sleep(0.25)
        with Session(engine) as other:
            looked.append(
                take_over_stuck_job(other, "w2", stale_after_seconds=0.15) is None
            )

    ai = ScriptedAI(wait=0.05, on_call=other_worker_looks)
    job = make_job(session, texts(3))
    worker = make_worker(engine, ai, texts_at_once=1, heartbeat_seconds=0.02)
    await worker.run_one_job()
    job, _ = reload(engine, job)
    assert job.state == JobState.DONE
    assert job.crash_count == 0
    assert looked == [True, True, True]


@pytest.mark.parametrize("hours", [1, -1])
async def test_a_wrong_worker_clock_changes_nothing(
    engine, session, monkeypatch, hours
):
    """Heartbeats and the stuck check use the database's clock, not the worker's.

    With the worker's clock an hour ahead (or behind), a job with a fresh
    heartbeat is still not stuck, and a stuck one is still found.
    """
    real_now = worker_module.utc_now
    monkeypatch.setattr(
        worker_module, "utc_now", lambda: real_now() + timedelta(hours=hours)
    )
    user = make_user(session)
    healthy = make_job(session, ["a"], owner=user)
    assert claim_next_job(session, "w1").id == healthy.id
    worker = make_worker(engine, ScriptedAI(), worker_id="w1")
    worker.beat(healthy.id)
    session.refresh(healthy)
    assert abs(real_now() - healthy.last_active_at) < timedelta(seconds=5)
    assert take_over_stuck_job(session, "w2", stale_after_seconds=STALE) is None

    stuck = make_running_job(session, ["b"], owner=user, seconds_ago=STALE + 5)
    taken = take_over_stuck_job(session, "w2", stale_after_seconds=STALE)
    assert taken.id == stuck.id


def test_stale_after_must_be_longer_than_the_heartbeat(monkeypatch):
    """Kuyruk refuses to start if healthy jobs could look stuck (D68)."""
    monkeypatch.setenv("HEARTBEAT_SECONDS", "30")
    monkeypatch.setenv("STALE_AFTER_SECONDS", "30")
    with pytest.raises(ValueError, match="STALE_AFTER_SECONDS"):
        Settings(_env_file=None)


def test_recovery_settings_have_spec_defaults():
    """Stale after 30 s, at most 3 crashes, demo mode off (spec 3, 8)."""
    fresh = Settings(_env_file=None)
    assert fresh.heartbeat_seconds == 5.0
    assert fresh.stale_after_seconds == 30.0
    assert fresh.max_crash_count == 3
    assert fresh.demo_mode is False


# --- Which jobs may be taken over ------------------------------------------


@pytest.mark.parametrize(
    "state", [JobState.QUEUED, JobState.DONE, JobState.FAILED, JobState.CANCELLED]
)
def test_only_running_jobs_are_taken_over(session, state):
    """Queued, cancelled or finished jobs are never taken over, however old."""
    job = make_running_job(session, texts(2), seconds_ago=3600, state=state)
    assert take_over_stuck_job(session, "w2", stale_after_seconds=STALE) is None
    session.refresh(job)
    assert job.state == state
    assert job.claimed_by == "dead-worker"
    assert job.crash_count == 0


async def test_stuck_jobs_come_before_queued_ones(engine, session):
    """An older queued job waits; the stuck job is taken first (D69)."""
    user = make_user(session)
    queued = make_job(session, ["queued text"], owner=user)
    stuck = make_running_job(session, ["stuck text"], owner=user)
    ai = ScriptedAI()
    worker = make_worker(engine, ai, stale_after_seconds=STALE)
    await worker.run_one_job()
    assert ai.received == ["stuck text"]
    assert reload(engine, stuck)[0].state == JobState.DONE
    assert reload(engine, queued)[0].state == JobState.QUEUED
    await worker.run_one_job()
    assert reload(engine, queued)[0].state == JobState.DONE


def test_two_workers_never_both_take_the_same_stuck_job(session, monkeypatch):
    """Compare-and-swap (D69): the second takeover finds the job changed.

    Worker w1 reads the stuck job; before its change reaches the database,
    worker w2 takes the job over. w1's change then matches nothing, and w1
    finds no other stuck job.
    """
    job = make_running_job(session, texts(2))
    real_execute = session.execute
    raced = []

    def w2_gets_there_first(statement, *args, **kwargs):
        """Just before w1's takeover change, let w2 take the job over."""
        if not raced and statement.is_dml:
            raced.append(True)
            with Session(session.get_bind()) as other:
                assert take_over_stuck_job(other, "w2", stale_after_seconds=STALE)
        return real_execute(statement, *args, **kwargs)

    monkeypatch.setattr(session, "execute", w2_gets_there_first)
    assert take_over_stuck_job(session, "w1", stale_after_seconds=STALE) is None
    session.refresh(job)
    assert job.claimed_by == "w2"
    assert job.crash_count == 1


def test_a_cancel_during_the_takeover_wins(session, monkeypatch):
    """If the user cancels between the look and the change, it stays cancelled."""
    job = make_running_job(session, texts(2))
    real_execute = session.execute
    cancelled = []

    def cancel_first(statement, *args, **kwargs):
        """Just before the takeover change, the user cancels."""
        if not cancelled and statement.is_dml:
            cancelled.append(True)
            with Session(session.get_bind()) as other:
                change_job_state(other, job.id, JobState.RUNNING, JobState.CANCELLED)
        return real_execute(statement, *args, **kwargs)

    monkeypatch.setattr(session, "execute", cancel_first)
    assert take_over_stuck_job(session, "w2", stale_after_seconds=STALE) is None
    session.refresh(job)
    assert job.state == JobState.CANCELLED
    assert job.crash_count == 0


# --- Continuing, not restarting ---------------------------------------------


async def test_takeover_continues_with_waiting_texts_only(engine, session):
    """Texts 1-5 ok and 6 failed stay as they are; only 7-10 are done."""
    finished = {n: "ok" for n in range(1, 6)} | {6: "failed"}
    job = make_running_job(session, texts(10), finished=finished)
    ai = ScriptedAI()
    worker = make_worker(engine, ai, worker_id="w2", stale_after_seconds=STALE)
    assert await worker.run_one_job() is True
    assert set(ai.received) == set(texts(10)[6:])
    assert len(ai.received) == 4
    job, items = reload(engine, job)
    assert job.state == JobState.DONE
    assert (job.succeeded, job.failed) == (9, 1)
    assert job.claimed_by == "w2"
    assert job.crash_count == 1
    assert job.resumed_from_position == 7
    assert [item.result for item in items[:5]] == ["dead worker's answer"] * 5
    assert items[5].state == ItemState.FAILED
    assert items[5].error_message == "dead worker's error"
    assert all(item.result == ANSWER for item in items[6:])


async def test_takeover_with_nothing_waiting_just_finishes_the_job(engine, session):
    """Crash after the last text but before "done": no text is redone (D73)."""
    job = make_running_job(session, texts(2), finished={1: "ok", 2: "ok"})
    ai = ScriptedAI()
    await make_worker(engine, ai, stale_after_seconds=STALE).run_one_job()
    job, _ = reload(engine, job)
    assert ai.received == []
    assert job.state == JobState.DONE
    assert job.resumed_from_position is None


# --- Crash count (D70) -------------------------------------------------------


@pytest.mark.parametrize("before", [0, 1, 2])
def test_up_to_the_max_a_stuck_job_is_taken_over(session, before):
    """Crash count 0, 1 or 2 before: taken over, count goes up by 1."""
    make_running_job(session, texts(2), crash_count=before)
    taken = take_over_stuck_job(
        session, "w2", stale_after_seconds=STALE, max_crash_count=3
    )
    assert taken.crash_count == before + 1
    assert taken.state == JobState.RUNNING


async def test_over_the_max_the_job_fails(engine, session):
    """Crash count 3 already: the 4th time stuck fails it, nothing is redone."""
    job = make_running_job(
        session, texts(4), crash_count=3, finished={1: "ok", 2: "failed"}
    )
    ai = ScriptedAI()
    worker = make_worker(engine, ai, stale_after_seconds=STALE, max_crash_count=3)
    assert await worker.run_one_job() is False
    job, items = reload(engine, job)
    assert ai.received == []
    assert job.state == JobState.FAILED
    assert job.error_message == CRASHED_TOO_OFTEN == "Crashed too many times"
    assert job.crash_count == 4
    assert job.finished_at is not None
    assert (job.succeeded, job.failed) == (1, 1)
    assert items[0].result == "dead worker's answer"
    assert [item.state for item in items[2:]] == [ItemState.WAITING] * 2


async def test_after_failing_a_crashed_job_the_worker_looks_further(engine, session):
    """A job failed for crashing doesn't stop the worker taking the next job."""
    user = make_user(session)
    make_running_job(session, ["a"], crash_count=3, owner=user)
    queued = make_job(session, ["b"], owner=user)
    worker = make_worker(engine, ScriptedAI(), stale_after_seconds=STALE)
    assert await worker.run_one_job() is True
    assert reload(engine, queued)[0].state == JobState.DONE


# --- Tries are counted when an attempt finishes (D71) -----------------------


async def test_a_crash_does_not_use_up_a_try(engine, session):
    """A text that was with the AI at the crash (0 tries) gets all 3 tries."""
    job = make_running_job(session, ["metin 1"])
    temporary = TemporaryAIError("busy")
    ai = ScriptedAI(script={"metin 1": [temporary, temporary, "geç cevap"]})
    await make_worker(engine, ai, stale_after_seconds=STALE).run_one_job()
    _, items = reload(engine, job)
    assert items[0].state == ItemState.OK
    assert items[0].result == "geç cevap"
    assert items[0].tries == 3


async def test_a_resumed_text_gets_only_its_remaining_tries(engine, session):
    """1 try used before the crash: 2 more, then it fails with 3 tries."""
    job = make_running_job(session, ["metin 1"], tries={1: 1})
    temporary = TemporaryAIError("busy")
    ai = ScriptedAI(script={"metin 1": [temporary, temporary, "never reached"]})
    await make_worker(engine, ai, stale_after_seconds=STALE).run_one_job()
    _, items = reload(engine, job)
    assert len(ai.received) == 2
    assert items[0].state == ItemState.FAILED
    assert items[0].tries == 3


async def test_each_finished_attempt_is_saved_at_once(engine, session):
    """After one temporary error, the database already shows 1 try."""
    seen = []

    async def note_tries(text):
        """Record the text's saved tries at the start of each AI call."""
        with Session(engine) as check:
            seen.append(check.get(JobItem, 1).tries)

    temporary = TemporaryAIError("busy")
    ai = ScriptedAI(script={"metin 1": [temporary, temporary]}, on_call=note_tries)
    make_job(session, ["metin 1"])
    await make_worker(engine, ai).run_one_job()
    assert seen == [0, 1, 2]
    with Session(engine) as check:
        assert check.get(JobItem, 1).tries == 3


# --- A worker that lost its job (D72) ---------------------------------------


def take_away(engine, job_id):
    """Pretend another worker took this job over meanwhile."""
    with Session(engine) as other:
        other.execute(
            update(Job)
            .where(Job.id == job_id)
            .values(claimed_by="other-worker", crash_count=1)
        )
        other.commit()


async def test_a_worker_that_lost_its_job_starts_no_more_texts(engine, session):
    """Taken over during text 2: text 2 is saved, 3-5 are left to the new owner."""
    job = make_job(session, texts(5))

    async def taken_over_on_second(text):
        """Another worker takes the job while text 2 is with the AI."""
        if text == "metin 2":
            take_away(engine, job.id)

    ai = ScriptedAI(on_call=taken_over_on_second)
    await make_worker(engine, ai, texts_at_once=1).run_one_job()
    job, items = reload(engine, job)
    assert ai.received == ["metin 1", "metin 2"]
    assert job.state == JobState.RUNNING
    assert job.claimed_by == "other-worker"
    assert [item.state for item in items[2:]] == [ItemState.WAITING] * 3


async def test_a_worker_that_lost_its_job_never_marks_it_done(engine, session):
    """Taken over during the last text: the old worker doesn't finish the job."""
    job = make_job(session, ["metin 1"])

    async def taken_over(text):
        """Another worker takes the job while the only text is with the AI."""
        take_away(engine, job.id)

    await make_worker(engine, ScriptedAI(on_call=taken_over)).run_one_job()
    job, items = reload(engine, job)
    assert job.state == JobState.RUNNING
    assert job.finished_at is None
    assert items[0].state == ItemState.OK


async def test_a_job_failed_by_another_worker_is_left_alone(engine, session):
    """Failed for crashing too often by another worker: the old one stops."""
    job = make_job(session, texts(3))

    async def failed_elsewhere(text):
        """Another worker fails the job while text 1 is with the AI."""
        with Session(engine) as other:
            change_job_state(other, job.id, JobState.RUNNING, JobState.FAILED)

    ai = ScriptedAI(on_call=failed_elsewhere)
    await make_worker(engine, ai, texts_at_once=1).run_one_job()
    job, _ = reload(engine, job)
    assert ai.received == ["metin 1"]
    assert job.state == JobState.FAILED


async def test_the_first_saved_answer_wins_and_counts_once(engine, session):
    """Both workers finish text 1: only the first answer is kept and counted."""
    job = make_job(session, ["metin 1"])

    async def other_worker_saves_first(text):
        """The other worker saves text 1 while this one's call is still out."""
        with Session(engine) as other:
            change_item_state(
                other, 1, ItemState.WAITING, ItemState.OK, result="first", tries=1
            )
            other.execute(update(Job).values(succeeded=Job.succeeded + 1))
            other.commit()

    ai = ScriptedAI(on_call=other_worker_saves_first)
    await make_worker(engine, ai).run_one_job()
    job, items = reload(engine, job)
    assert items[0].result == "first"
    assert job.succeeded == 1
    assert job.state == JobState.DONE


# --- The log line for E2 (D75) ----------------------------------------------


async def test_one_log_line_per_text_started(engine, session, caplog):
    """Each text started is logged once, with job, position and worker.

    A retry is not a new start, so a text retried twice still gives one line.
    """
    temporary = TemporaryAIError("busy")
    ai = ScriptedAI(script={"metin 2": [temporary, temporary]})
    job = make_job(session, texts(3))
    with caplog.at_level(logging.INFO, logger="kuyruk.worker"):
        await make_worker(engine, ai, worker_id="w9").run_one_job()
    started = sorted(
        record.getMessage()
        for record in caplog.records
        if record.getMessage().startswith("text started")
    )
    assert started == [
        f"text started job={job.id} position={n} worker=w9" for n in (1, 2, 3)
    ]


async def test_redone_texts_are_logged_again_after_a_takeover(engine, session, caplog):
    """After a crash, only the waiting texts get a new line, by the new worker."""
    job = make_running_job(session, texts(3), finished={1: "ok"})
    worker = make_worker(engine, ScriptedAI(), worker_id="w2")
    with caplog.at_level(logging.INFO, logger="kuyruk.worker"):
        await worker.run_one_job()
    started = sorted(
        record.getMessage()
        for record in caplog.records
        if record.getMessage().startswith("text started")
    )
    assert started == [
        f"text started job={job.id} position={n} worker=w2" for n in (2, 3)
    ]


# --- A polite stop hands the job back (D76) ---------------------------------


async def test_a_polite_stop_hands_the_job_back(engine, session):
    """Ctrl+C mid-job: the job stays running, without the worker's name."""
    worker = None

    async def stop_on_first(text):
        """Ask the worker to stop as soon as the first text reaches the AI."""
        worker.stop()

    worker = make_worker(engine, ScriptedAI(on_call=stop_on_first), texts_at_once=1)
    job = make_job(session, texts(3))
    await worker.run_one_job()
    job, items = reload(engine, job)
    assert job.state == JobState.RUNNING
    assert job.claimed_by is None
    assert job.crash_count == 0
    assert [item.state for item in items] == [ItemState.OK] + [ItemState.WAITING] * 2


async def test_a_handed_back_job_is_picked_up_at_once_and_is_not_a_crash(
    engine, session
):
    """No 30 s wait, crash count unchanged, even at the max; no recovered position."""
    job = make_running_job(
        session,
        texts(3),
        seconds_ago=0,
        claimed_by=None,
        crash_count=3,
        finished={1: "ok"},
    )
    ai = ScriptedAI()
    worker = make_worker(
        engine, ai, worker_id="w2", stale_after_seconds=STALE, max_crash_count=3
    )
    assert await worker.run_one_job() is True
    job, items = reload(engine, job)
    assert sorted(ai.received) == ["metin 2", "metin 3"]
    assert job.state == JobState.DONE
    assert job.claimed_by == "w2"
    assert job.crash_count == 3
    assert job.resumed_from_position is None
    assert items[0].result == "dead worker's answer"


async def test_a_handed_back_job_comes_before_queued_ones(engine, session):
    """Like a stuck job, it has waited longer than any queued job (D69)."""
    user = make_user(session)
    queued = make_job(session, ["queued text"], owner=user)
    make_running_job(
        session, ["handed back"], owner=user, seconds_ago=0, claimed_by=None
    )
    ai = ScriptedAI()
    await make_worker(engine, ai).run_one_job()
    assert ai.received == ["handed back"]
    assert reload(engine, queued)[0].state == JobState.QUEUED


def test_two_workers_never_both_pick_up_a_handed_back_job(session, monkeypatch):
    """Compare-and-swap here too: the second finds the job already taken."""
    job = make_running_job(session, texts(2), seconds_ago=0, claimed_by=None)
    real_execute = session.execute
    raced = []

    def w2_gets_there_first(statement, *args, **kwargs):
        """Just before w1's change, let w2 pick the job up."""
        if not raced and statement.is_dml:
            raced.append(True)
            with Session(session.get_bind()) as other:
                assert take_over_stuck_job(other, "w2", stale_after_seconds=STALE)
        return real_execute(statement, *args, **kwargs)

    monkeypatch.setattr(session, "execute", w2_gets_there_first)
    assert take_over_stuck_job(session, "w1", stale_after_seconds=STALE) is None
    session.refresh(job)
    assert job.claimed_by == "w2"
    assert job.crash_count == 0


def test_hand_back_keeps_a_cancel_or_takeover(engine, session):
    """Cancelled or taken over meanwhile: handing back changes nothing."""
    user = make_user(session)
    cancelled = make_running_job(
        session, ["a"], owner=user, claimed_by="w1", state=JobState.CANCELLED
    )
    taken = make_running_job(session, ["b"], claimed_by="w2", owner=user)
    worker = make_worker(engine, ScriptedAI(), worker_id="w1")
    worker.hand_back(cancelled.id)
    worker.hand_back(taken.id)
    assert reload(engine, cancelled)[0].claimed_by == "w1"
    assert reload(engine, cancelled)[0].state == JobState.CANCELLED
    assert reload(engine, taken)[0].claimed_by == "w2"
