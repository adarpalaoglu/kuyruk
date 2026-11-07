"""Tests for the worker in `app/worker.py` (spec section 7), on SQLite.

The real AI is never called. `ScriptedAI` is a pretend AI written for these
tests: for each text it can be told to answer, fail temporarily or fail
permanently, and it records how many texts it was working on at the same time.
Retry waits use a pretend `sleep` that records the wait instead of waiting, so
the tests run in a fraction of a second. `pytest.mark.anyio` lets a test be an
`async` function.
"""

import asyncio
import logging
import os
import signal
import time

import pytest
from sqlmodel import Session, select

from app import worker as worker_module
from app.ai import FakeAI, PermanentAIError, TemporaryAIError
from app.models import ItemState, Job, JobItem, JobState, Task, User
from app.normalise import normalise
from app.tokens import count_tokens
from app.worker import (
    AI_UNAVAILABLE,
    INTERNAL_ERROR,
    Worker,
    claim_next_job,
    prepare_for_ai,
)

pytestmark = pytest.mark.anyio

ANSWER = "Kısa bir özet."


class RecordedWaits(list):
    """A pretend sleep: remembers each wait instead of waiting."""

    async def __call__(self, seconds: float) -> None:
        self.append(seconds)


class ScriptedAI:
    """A pretend AI that does what each text's script says, and keeps records.

    `script` maps a text to a list of outcomes, one per try: an answer, or an
    error to raise. Texts without a script are answered with ANSWER. `wait` is
    how long each call really takes (a tiny real pause, so several texts are
    in progress at once). `on_call` is an optional function run at the start
    of each call, used to cancel or stop in the middle of a job.
    """

    def __init__(self, script=None, wait=0.005, on_call=None):
        self.script = {text: list(steps) for text, steps in (script or {}).items()}
        self.wait = wait
        self.on_call = on_call
        self.received: list[str] = []
        self.active = 0
        self.peak = 0

    async def summarise(self, text: str) -> str:
        """Follow the script for `text`, recording how many calls run at once."""
        self.received.append(text)
        if self.on_call is not None:
            await self.on_call(text)
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            await asyncio.sleep(self.wait)
        finally:
            self.active -= 1
        steps = self.script.get(text)
        outcome = steps.pop(0) if steps else ANSWER
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def texts(count: int) -> list[str]:
    """`count` numbered Turkish texts: "metin 1", "metin 2", ..."""
    return [f"metin {n}" for n in range(1, count + 1)]


def make_user(session, username="ayse") -> User:
    """Save a user and return it."""
    user = User(username=username, hashed_password="not-a-real-hash")
    session.add(user)
    session.commit()
    return user


def make_job(session, job_texts, task=Task.LLM, owner=None) -> Job:
    """Save a queued job with these texts (and a user, if none is given)."""
    owner = owner or make_user(session)
    job = Job(owner_id=owner.id, task=task, total=len(job_texts))
    session.add(job)
    session.add_all(
        JobItem(job_id=job.id, position=n, original_text=text)
        for n, text in enumerate(job_texts, start=1)
    )
    session.commit()
    return job


def make_worker(engine, ai, **changes) -> Worker:
    """A worker with fast test numbers; `changes` overrides any of them."""
    options = {
        "worker_id": "test-worker",
        "sleep": RecordedWaits(),
        "poll_seconds": 0.01,
        "texts_at_once": 4,
        "max_tries": 3,
        "retry_first_wait_seconds": 1.0,
        "stop_after_failures": 10,
        "heartbeat_seconds": 60,
        "shutdown_grace_seconds": 1.0,
    }
    options.update(changes)
    return Worker(engine, ai, **options)


def reload(engine, job: Job) -> tuple[Job, list[JobItem]]:
    """Read the job and its texts (in position order) fresh from the database."""
    with Session(engine) as session:
        fresh = session.get(Job, job.id)
        items = session.exec(
            select(JobItem).where(JobItem.job_id == job.id).order_by(JobItem.position)
        ).all()
        session.expunge_all()
    return fresh, list(items)


# --- Claiming ---------------------------------------------------------------


def test_claim_takes_oldest_queued_job_and_records_worker(session):
    """The oldest queued job becomes running, with our name and start time."""
    user = make_user(session)
    first = make_job(session, ["a"], owner=user)
    make_job(session, ["b"], owner=user)
    claimed = claim_next_job(session, "w1")
    assert claimed.id == first.id
    assert claimed.state == JobState.RUNNING
    assert claimed.claimed_by == "w1"
    assert claimed.started_at is not None
    assert claimed.last_active_at == claimed.started_at


def test_claim_skips_jobs_that_are_not_queued(session):
    """Running, finished and cancelled jobs are never claimed."""
    user = make_user(session)
    for state in (JobState.RUNNING, JobState.DONE, JobState.CANCELLED):
        job = make_job(session, ["a"], owner=user)
        job.state = state
        session.add(job)
    session.commit()
    assert claim_next_job(session, "w1") is None


def test_a_job_is_claimed_only_once(session):
    """A second claim finds nothing: the job is no longer queued."""
    make_job(session, ["a"])
    assert claim_next_job(session, "w1") is not None
    assert claim_next_job(session, "w2") is None


def test_claim_moves_on_if_the_job_was_taken_meanwhile(session, monkeypatch):
    """If a cancel wins the race for the first job, the next one is claimed."""
    user = make_user(session)
    first = make_job(session, ["a"], owner=user)
    second = make_job(session, ["b"], owner=user)
    real_change = worker_module.change_job_state
    calls = []

    def cancel_first_then_change(session, job_id, *args, **kwargs):
        """Cancel the first job just before the worker's claim reaches it."""
        if not calls:
            real_change(session, first.id, JobState.QUEUED, JobState.CANCELLED)
        calls.append(job_id)
        return real_change(session, job_id, *args, **kwargs)

    monkeypatch.setattr(worker_module, "change_job_state", cancel_first_then_change)
    claimed = claim_next_job(session, "w1")
    assert claimed.id == second.id
    assert calls == [first.id, second.id]


async def test_run_one_job_returns_false_when_nothing_is_queued(engine):
    """With no queued job, the worker reports it had nothing to do."""
    assert await make_worker(engine, ScriptedAI()).run_one_job() is False


# --- Running each task ------------------------------------------------------


async def test_normalise_job_runs_to_done(engine, session):
    """Every text gets Adar's cleanup rules; the job ends done with counts."""
    originals = ["  ISPARTA İÇİN  ", "Yaz: ali@site.com 👍🏽", "kır\nve kir"]
    job = make_job(session, originals, task=Task.NORMALISE)
    await make_worker(engine, ScriptedAI()).run_one_job()
    job, items = reload(engine, job)
    assert [item.result for item in items] == [normalise(t) for t in originals]
    assert [item.result for item in items][0] == "ısparta için"
    assert all(item.state == ItemState.OK and item.tries == 1 for item in items)
    assert (job.state, job.succeeded, job.failed) == (JobState.DONE, 3, 0)
    assert job.finished_at is not None and job.finished_at >= job.started_at


async def test_count_tokens_job_runs_to_done(engine, session):
    """Each result is the text's token count, written as a number."""
    originals = ["Merhaba dünya", "Çok güzel bir gün 🌞"]
    job = make_job(session, originals, task=Task.COUNT_TOKENS)
    await make_worker(engine, ScriptedAI()).run_one_job()
    job, items = reload(engine, job)
    assert [item.result for item in items] == [str(count_tokens(t)) for t in originals]
    assert job.state == JobState.DONE


async def test_llm_job_runs_to_done_with_the_fake_ai(engine, session):
    """The real fake AI from app/ai.py works with the worker end to end."""
    fake = FakeAI(
        wait_mode="fixed",
        wait_seconds=0,
        replay_file="",
        seed=1,
        answer=ANSWER,
        fail_every=0,
        fail_kind="temporary",
    )
    job = make_job(session, texts(5))
    await make_worker(engine, fake).run_one_job()
    job, items = reload(engine, job)
    assert [item.result for item in items] == [ANSWER] * 5
    assert (job.state, job.succeeded, job.failed, job.total) == (
        JobState.DONE,
        5,
        0,
        5,
    )


async def test_llm_texts_reach_the_ai_without_personal_data(engine, session):
    """Emails and phones are hidden before the AI sees a text (D50).

    Nothing else changes: capitals and spacing stay as written. The original
    text is still stored unchanged.
    """
    original = "ALİ  yazdı: ali@site.com,  0532 123 45 67"
    job = make_job(session, [original])
    ai = ScriptedAI()
    await make_worker(engine, ai).run_one_job()
    assert ai.received == ["ALİ  yazdı: [EMAIL],  [TELEFON]"]
    _, items = reload(engine, job)
    assert items[0].original_text == original


def test_prepare_for_ai_joins_two_part_letters_first():
    """An email with "ş" stored as s + mark is still hidden (NFC first, D49)."""
    assert prepare_for_ai("ali@şirket.com") == "[EMAIL]"


# --- Failures and retries ---------------------------------------------------


async def test_partial_failure_is_still_done_with_counts(engine, session):
    """Two texts fail permanently; the job is done with 3 succeeded, 2 failed."""
    bad = PermanentAIError("Bad text.")
    ai = ScriptedAI({"metin 2": [bad], "metin 4": [bad]})
    job = make_job(session, texts(5))
    await make_worker(engine, ai).run_one_job()
    job, items = reload(engine, job)
    assert (job.state, job.succeeded, job.failed) == (JobState.DONE, 3, 2)
    assert items[1].state == ItemState.FAILED
    assert items[1].error_message == "Bad text."
    assert items[1].result is None


async def test_temporary_failures_then_success(engine, session):
    """Two temporary errors, then an answer: ok after 3 tries, waits 1 s and 2 s."""
    busy = TemporaryAIError("Busy.")
    ai = ScriptedAI({"metin 1": [busy, busy, "Sonunda."]})
    waits = RecordedWaits()
    job = make_job(session, texts(1))
    await make_worker(engine, ai, sleep=waits).run_one_job()
    job, items = reload(engine, job)
    assert (items[0].state, items[0].tries, items[0].result) == (
        ItemState.OK,
        3,
        "Sonunda.",
    )
    assert waits == [1.0, 2.0]
    assert (job.succeeded, job.failed) == (1, 0)


async def test_permanent_failure_is_not_retried(engine, session):
    """A permanent error fails the text after one try, with no wait."""
    ai = ScriptedAI({"metin 1": [PermanentAIError("Never works.")]})
    waits = RecordedWaits()
    job = make_job(session, texts(1))
    await make_worker(engine, ai, sleep=waits).run_one_job()
    _, items = reload(engine, job)
    assert (items[0].state, items[0].tries) == (ItemState.FAILED, 1)
    assert waits == []
    assert ai.received == ["metin 1"]


async def test_temporary_failure_three_times_fails_the_text(engine, session):
    """Three temporary errors in a row use up the tries: failed, tries 3."""
    busy = TemporaryAIError("Still busy.")
    ai = ScriptedAI({"metin 1": [busy, busy, busy]})
    job = make_job(session, texts(1))
    await make_worker(engine, ai).run_one_job()
    job, items = reload(engine, job)
    assert (items[0].state, items[0].tries) == (ItemState.FAILED, 3)
    assert items[0].error_message == "Still busy."
    assert job.state == JobState.DONE


async def test_ten_failures_in_a_row_fail_the_job(engine, session):
    """The AI is down: after 10 failed texts the job stops, "AI unavailable".

    With 4 at once, texts already started when the 10th failure arrives still
    finish; the rest are never tried and stay waiting.
    """
    down = PermanentAIError("Down.")
    ai = ScriptedAI({text: [down] for text in texts(30)})
    job = make_job(session, texts(30))
    await make_worker(engine, ai).run_one_job()
    job, items = reload(engine, job)
    assert job.state == JobState.FAILED
    assert job.error_message == AI_UNAVAILABLE
    assert job.finished_at is not None
    tried = [item for item in items if item.state != ItemState.WAITING]
    assert 10 <= len(tried) <= 10 + 3
    assert job.failed == len(tried)
    assert all(item.tries == 0 for item in items if item not in tried)


async def test_ten_temporary_failures_in_a_row_count_once_per_text(engine, session):
    """A text counts as one failure after all its tries, not one per try."""
    busy = TemporaryAIError("Busy.")
    ai = ScriptedAI({text: [busy] * 3 for text in texts(20)}, wait=0)
    job = make_job(session, texts(20))
    await make_worker(engine, ai, texts_at_once=1).run_one_job()
    job, items = reload(engine, job)
    assert job.state == JobState.FAILED
    assert [item.tries for item in items[:10]] == [3] * 10
    assert all(item.state == ItemState.WAITING for item in items[10:])


async def test_a_success_resets_the_failures_in_a_row(engine, session):
    """9 failures, 1 success, 9 failures: never 10 in a row, so the job is done."""
    down = PermanentAIError("Down.")
    script = {text: [down] for text in texts(19) if text != "metin 10"}
    job = make_job(session, texts(19))
    await make_worker(engine, ScriptedAI(script), texts_at_once=1).run_one_job()
    job, _ = reload(engine, job)
    assert (job.state, job.succeeded, job.failed) == (JobState.DONE, 1, 18)


async def test_bug_in_a_task_fails_only_that_text(engine, session, monkeypatch):
    """An unexpected error (not the AI's) fails the text with a safe message."""

    def broken(text):
        """A stand-in for count_tokens that crashes on one text."""
        if text == "kötü":
            raise ValueError("secret internal detail")
        return 1

    monkeypatch.setattr(worker_module, "count_tokens", broken)
    job = make_job(session, ["iyi", "kötü"], task=Task.COUNT_TOKENS)
    await make_worker(engine, ScriptedAI()).run_one_job()
    job, items = reload(engine, job)
    assert items[1].error_message == INTERNAL_ERROR
    assert (job.state, job.succeeded, job.failed) == (JobState.DONE, 1, 1)


# --- Texts at once, order, heartbeat ----------------------------------------


async def test_never_more_than_four_texts_at_once(engine, session):
    """With 20 texts, the AI never has more than 4 at a time (and does reach 4)."""
    ai = ScriptedAI()
    make_job(session, texts(20))
    await make_worker(engine, ai).run_one_job()
    assert ai.peak == 4
    assert len(ai.received) == 20


async def test_texts_at_once_follows_the_setting(engine, session):
    """With texts_at_once=2, at most 2 are in progress."""
    ai = ScriptedAI()
    make_job(session, texts(10))
    await make_worker(engine, ai, texts_at_once=2).run_one_job()
    assert ai.peak == 2


async def test_texts_start_in_position_order(engine, session):
    """Texts are sent to the AI in the order they were in the job."""
    ai = ScriptedAI()
    make_job(session, texts(12))
    await make_worker(engine, ai).run_one_job()
    assert ai.received == texts(12)


async def test_heartbeat_moves_last_active_at(engine, session):
    """While a job runs, last_active_at keeps moving forward."""
    seen = []

    async def note_last_active(text):
        """Record the job's last_active_at at the start of each AI call."""
        with Session(engine) as check:
            seen.append(check.exec(select(Job.last_active_at)).one())

    ai = ScriptedAI(wait=0.05, on_call=note_last_active)
    job = make_job(session, texts(3))
    await make_worker(engine, ai, texts_at_once=1, heartbeat_seconds=0.01).run_one_job()
    job, _ = reload(engine, job)
    assert seen[0] < seen[-1] < job.last_active_at
    assert job.last_active_at > job.started_at


async def test_failed_heartbeat_does_not_stop_the_job_finishing(
    engine, session, monkeypatch, caplog
):
    """A heartbeat that fails once is logged; the job still ends done."""
    worker = make_worker(
        engine, ScriptedAI(wait=0.05), texts_at_once=1, heartbeat_seconds=0.01
    )
    real_beat = worker.beat
    beats = []

    def fail_first_beat(job_id):
        """Pretend the database refused the first heartbeat only."""
        beats.append(job_id)
        if len(beats) == 1:
            raise RuntimeError("database unreachable")
        real_beat(job_id)

    monkeypatch.setattr(worker, "beat", fail_first_beat)
    job = make_job(session, texts(2))
    with caplog.at_level(logging.ERROR, logger="kuyruk.worker"):
        await worker.run_one_job()
    job, _ = reload(engine, job)
    assert job.state == JobState.DONE
    assert len(beats) > 1
    assert job.last_active_at > job.started_at
    assert "Heartbeat for job" in caplog.text


async def test_error_mid_job_stops_texts_still_with_the_ai(
    engine, session, monkeypatch
):
    """If checking the job fails while a text is with the AI, that text stops too.

    Otherwise it would keep running in the background and change the next
    job's failures in a row. It stays waiting, for crash recovery to redo.
    """
    ai = ScriptedAI(wait=30)
    worker = make_worker(engine, ai)
    checks = []

    def fail_second_check(job_id):
        """Allow the first text to start, then pretend the database failed."""
        checks.append(job_id)
        if len(checks) == 2:
            raise RuntimeError("database unreachable")
        return None

    monkeypatch.setattr(worker, "reason_to_stop", fail_second_check)
    job = make_job(session, texts(3))
    with pytest.raises(RuntimeError, match="database unreachable"):
        await asyncio.wait_for(worker.run_one_job(), timeout=2)
    assert ai.active == 0
    # No text task of the worker is left running in the background.
    assert not [
        task
        for task in asyncio.all_tasks()
        if task.get_coro().__qualname__ == "Worker.process_text"
    ]
    _, items = reload(engine, job)
    assert all(item.state == ItemState.WAITING for item in items)


# --- Stopping the worker ----------------------------------------------------


async def test_stop_request_lets_running_texts_finish_then_leaves_job(engine, session):
    """Ctrl+C during text 1: text 1 finishes, nothing new starts, job stays running.

    The remaining texts stay waiting for Slice 8's recovery to continue.
    """
    worker = None

    async def stop_on_first(text):
        """Ask the worker to stop as soon as the first text reaches the AI."""
        worker.stop()

    worker = make_worker(engine, ScriptedAI(on_call=stop_on_first), texts_at_once=1)
    job = make_job(session, texts(5))
    await worker.run_one_job()
    job, items = reload(engine, job)
    assert job.state == JobState.RUNNING
    assert job.finished_at is None
    assert items[0].state == ItemState.OK
    assert all(item.state == ItemState.WAITING for item in items[1:])


async def test_stop_waits_at_most_the_grace_time(engine, session):
    """A text stuck at the AI is given up after the grace time; it stays waiting."""
    worker = None

    async def stop_then_hang(text):
        """Ask for a stop, then never answer (much longer than the grace)."""
        worker.stop()
        await asyncio.sleep(30)

    worker = make_worker(
        engine, ScriptedAI(on_call=stop_then_hang), shutdown_grace_seconds=0.05
    )
    job = make_job(session, texts(3))
    started = time.monotonic()
    await worker.run_one_job()
    assert time.monotonic() - started < 2
    job, items = reload(engine, job)
    assert job.state == JobState.RUNNING
    assert all(item.state == ItemState.WAITING and item.tries == 0 for item in items)


async def test_stop_after_the_last_text_started_still_ends_done(engine, session):
    """A stop that arrives while the last texts finish in time: the job is done."""
    worker = None

    async def stop_on_last(text):
        """Ask for a stop when the last text reaches the AI."""
        if text == "metin 3":
            worker.stop()

    worker = make_worker(engine, ScriptedAI(on_call=stop_on_last))
    job = make_job(session, texts(3))
    await worker.run_one_job()
    job, _ = reload(engine, job)
    assert (job.state, job.succeeded) == (JobState.DONE, 3)


async def test_run_forever_processes_jobs_until_stopped(engine, session):
    """The loop picks up a queued job, finishes it, and exits on stop."""
    worker = make_worker(engine, ScriptedAI())
    job = make_job(session, texts(3))
    running = asyncio.create_task(worker.run_forever())
    for _ in range(200):
        await asyncio.sleep(0.01)
        if reload(engine, job)[0].state == JobState.DONE:
            break
    worker.stop()
    await asyncio.wait_for(running, timeout=1)
    assert reload(engine, job)[0].state == JobState.DONE


async def test_run_forever_survives_an_error(engine, monkeypatch, caplog):
    """A failure in one round (e.g. database down) is logged; the loop goes on."""
    worker = make_worker(engine, ScriptedAI())
    rounds = []

    async def fail_once():
        """Fail the first round, then stop the worker on the second."""
        rounds.append(1)
        if len(rounds) == 1:
            raise RuntimeError("database unreachable")
        worker.stop()
        return False

    monkeypatch.setattr(worker, "run_one_job", fail_once)
    with caplog.at_level(logging.ERROR, logger="kuyruk.worker"):
        await asyncio.wait_for(worker.run_forever(), timeout=1)
    assert len(rounds) == 2
    assert "Worker loop error" in caplog.text


async def test_failed_save_is_logged_and_text_stays_waiting(
    engine, session, monkeypatch, caplog
):
    """If saving a text's result fails, it is logged; the text stays waiting."""
    worker = make_worker(engine, ScriptedAI())

    def broken_save(*args, **kwargs):
        """Pretend the database refused the save."""
        raise RuntimeError("database unreachable")

    monkeypatch.setattr(worker, "save_text", broken_save)
    job = make_job(session, texts(2))
    with caplog.at_level(logging.ERROR, logger="kuyruk.worker"):
        await worker.run_one_job()
    job, items = reload(engine, job)
    assert "Saving a text failed" in caplog.text
    assert all(item.state == ItemState.WAITING for item in items)


async def test_serve_stops_cleanly_on_a_stop_signal(engine):
    """SIGTERM (what Docker sends) makes the worker stop and `serve` return."""
    worker = make_worker(engine, ScriptedAI())
    serving = asyncio.create_task(worker_module.serve(worker))
    await asyncio.sleep(0.02)
    os.kill(os.getpid(), signal.SIGTERM)
    await asyncio.wait_for(serving, timeout=1)
    assert worker.stopping.is_set()


def test_main_sets_up_and_runs_the_worker(monkeypatch, engine):
    """`main` creates the tables and runs a worker on the app's database."""
    import app.db

    served = []

    async def pretend_serve(worker):
        """Record the worker instead of running it."""
        served.append(worker)

    monkeypatch.setattr(app.db, "engine", engine)
    monkeypatch.setattr(app.db, "create_db_and_tables", lambda: None)
    monkeypatch.setattr(worker_module, "serve", pretend_serve)
    worker_module.main()
    assert served[0].engine is engine
