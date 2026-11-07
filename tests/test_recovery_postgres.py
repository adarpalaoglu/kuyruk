"""Crash recovery and two workers on real Postgres (spec 7, 7.1, D32).

These tests start real worker processes (`python -m app.worker`, exactly as
in production) against a real Postgres database, because only a real
database server lets two programs truly work at the same moment, with real
row locks. They check:

- two workers on many jobs never take the same job, and never start the same
  text twice when nothing crashes;
- two workers trying to take over the same stuck job at the same instant:
  exactly one wins;
- a worker killed with `kill -9` in the middle of a job (no clean-up at all,
  like a power cut) is replaced by a second worker, which takes the job over
  after the stale time and finishes it with no text lost;
- a worker stopped politely (SIGTERM, what Docker sends) hands its job back,
  and a second worker carries on at once, without counting a crash (D76).

The workers use the fake AI and short times (heartbeat 0.2 s, stale after
1.5 s), so the tests take seconds, not minutes. Which texts each worker
started is read from their "text started" log lines (D75).

Like `tests/test_budget_postgres.py`, they run only when POSTGRES_TEST_URL
points at a Postgres database that may be wiped; otherwise they are skipped.
"""

import os
import re
import signal
import subprocess
import sys
import threading
import time
from collections import Counter
from collections.abc import Callable
from pathlib import Path

import pytest
from sqlalchemy import Engine, update
from sqlmodel import Session, select

from app.models import ItemState, Job, JobItem, JobState, Task, utc_now
from app.worker import take_over_stuck_job
from tests.test_budget_postgres import POSTGRES_TEST_URL, pg_engine  # noqa: F401
from tests.test_worker import make_job, make_user, texts

pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(not POSTGRES_TEST_URL, reason="POSTGRES_TEST_URL not set"),
]

# The project folder, so `python -m app.worker` finds Kuyruk's code.
PROJECT = Path(__file__).resolve().parent.parent
HEARTBEAT = 0.2
STALE = 1.5
# One "text started job=... position=... worker=..." log line (D75).
STARTED = re.compile(r"text started job=(\S+) position=(\d+) worker=(\S+)")


class WorkerProcess:
    """One real worker process, with its log written to a file."""

    def __init__(self, log_path: Path, ai_wait: float) -> None:
        """Start `python -m app.worker` on the Postgres test database.

        Every setting the worker reads is given here, so a local `.env`
        (e.g. one that chooses the real AI) can't change the test.
        """
        self.log_path = log_path
        env = os.environ | {
            "DATABASE_URL": POSTGRES_TEST_URL,
            "AI_BACKEND": "fake",
            "FAKE_AI_WAIT_MODE": "fixed",
            "FAKE_AI_WAIT_SECONDS": str(ai_wait),
            "FAKE_AI_FAIL_EVERY": "0",
            "WORKER_POLL_SECONDS": "0.1",
            "TEXTS_AT_ONCE": "4",
            "HEARTBEAT_SECONDS": str(HEARTBEAT),
            "STALE_AFTER_SECONDS": str(STALE),
            "MAX_CRASH_COUNT": "3",
            "DEMO_MODE": "false",
        }
        self.log_file = log_path.open("w")
        self.process = subprocess.Popen(
            [sys.executable, "-m", "app.worker"],
            cwd=PROJECT,
            env=env,
            stdout=self.log_file,
            stderr=subprocess.STDOUT,
        )

    def log(self) -> str:
        """Everything the worker has logged so far."""
        return self.log_path.read_text()

    def worker_id(self) -> str:
        """This worker's name, from its "Worker ... started." line."""
        wait_for(lambda: "started." in self.log(), what="the worker to start")
        return re.search(r"Worker (\S+) started\.", self.log()).group(1)

    def started_texts(self) -> list[tuple[str, int]]:
        """(job id, position) of every text this worker started."""
        return [
            (job_id, int(position))
            for job_id, position, _ in STARTED.findall(self.log())
        ]

    def stop_politely(self) -> None:
        """SIGTERM: ask the worker to stop, as Docker does, and wait for it."""
        self.process.send_signal(signal.SIGTERM)
        self.process.wait(timeout=20)

    def kill_hard(self) -> None:
        """`kill -9`: the operating system ends the process at once.

        SIGKILL can't be caught: no clean-up, no last log line, no database
        write. The same as a power cut, as far as Kuyruk can tell.
        """
        self.process.send_signal(signal.SIGKILL)
        self.process.wait(timeout=10)

    def close(self) -> None:
        """Stop the worker (if still running) and close its log file."""
        if self.process.poll() is None:
            self.process.kill()
            self.process.wait(timeout=10)
        self.log_file.close()


@pytest.fixture
def start_worker(tmp_path, pg_engine) -> Callable[..., WorkerProcess]:  # noqa: F811
    """Start worker processes for one test; all are stopped at its end."""
    started: list[WorkerProcess] = []

    def start(name: str, ai_wait: float = 0.1) -> WorkerProcess:
        """Start one worker that logs to `<name>.log`."""
        worker = WorkerProcess(tmp_path / f"{name}.log", ai_wait)
        started.append(worker)
        return worker

    yield start
    for worker in started:
        worker.close()


def wait_for(condition: Callable[[], bool], what: str, timeout: float = 30) -> None:
    """Check `condition` every 0.05 s until it is true; fail after `timeout` s."""
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError(f"Timed out waiting for {what}.")
        time.sleep(0.05)


def job_row(engine: Engine, job_id) -> Job:
    """Read one job fresh from the database."""
    with Session(engine) as session:
        job = session.get(Job, job_id)
        session.expunge(job)
    return job


def test_two_workers_never_take_the_same_job_or_text(
    pg_engine,  # noqa: F811
    start_worker,
):
    """30 jobs of 5 texts, two workers: each job and each text done exactly once."""
    first, second = start_worker("first"), start_worker("second")
    first_id, second_id = first.worker_id(), second.worker_id()
    with Session(pg_engine) as session:
        owner = make_user(session)
        job_ids = [make_job(session, texts(5), owner=owner).id for _ in range(30)]

    def all_done() -> bool:
        """True once every job is done."""
        with Session(pg_engine) as session:
            states = session.exec(select(Job.state)).all()
        return all(state == JobState.DONE for state in states)

    wait_for(all_done, what="all 30 jobs to be done", timeout=60)

    started = first.started_texts() + second.started_texts()
    # No text was started twice, and none was skipped.
    assert len(started) == len(set(started)) == 30 * 5
    # No job was worked on by both workers.
    first_jobs = {job_id for job_id, _ in first.started_texts()}
    second_jobs = {job_id for job_id, _ in second.started_texts()}
    assert first_jobs.isdisjoint(second_jobs)
    # Both really took part, so the test proved something.
    assert first_jobs and second_jobs
    with Session(pg_engine) as session:
        jobs = session.exec(select(Job)).all()
        assert {job.claimed_by for job in jobs} == {first_id, second_id}
        assert all(job.crash_count == 0 and job.succeeded == 5 for job in jobs)
        assert sorted(str(job.id) for job in jobs) == sorted(map(str, job_ids))


def race_for_takeover(engine: Engine, barrier, worker_id: str, winners) -> None:
    """Wait at the starting line with the other thread, then try a takeover.

    A *barrier* is that starting line: each thread waits at it until both
    have arrived, then both are let go at the same instant.
    """
    with Session(engine) as session:
        barrier.wait()
        if take_over_stuck_job(session, worker_id, stale_after_seconds=STALE):
            winners.append(worker_id)


def test_two_takeovers_at_the_same_instant_only_one_wins(pg_engine):  # noqa: F811
    """A stuck job raced for by two threads at once, ten times: one winner each."""
    with Session(pg_engine) as session:
        job_id = make_job(session, texts(2), owner=make_user(session)).id
    for attempt in range(10):
        with Session(pg_engine) as session:
            session.execute(
                update(Job)
                .where(Job.id == job_id)
                .values(
                    state=JobState.RUNNING,
                    claimed_by="dead-worker",
                    crash_count=0,
                    last_active_at=utc_now().replace(year=2000),
                )
            )
            session.commit()
        barrier = threading.Barrier(2)
        winners = []
        threads = [
            threading.Thread(
                target=race_for_takeover,
                args=(pg_engine, barrier, f"w{n}-{attempt}", winners),
            )
            for n in (1, 2)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert len(winners) == 1
        taken = job_row(pg_engine, job_id)
        assert taken.claimed_by == winners[0]
        assert taken.crash_count == 1


def test_killed_worker_is_replaced_and_no_text_is_lost(
    pg_engine,  # noqa: F811
    start_worker,
):
    """kill -9 mid-job; a second worker takes over after the stale time.

    20 texts, 0.3 s each, 4 at once. The first worker is killed once 8 texts
    are ok. The second finishes the job: 20 ok, none lost, crash count 1,
    and at most 4 texts done twice (those that were with the AI at the kill).
    """
    first = start_worker("first", ai_wait=0.3)
    first.worker_id()
    with Session(pg_engine) as session:
        job_id = make_job(session, texts(20), task=Task.LLM).id

    wait_for(
        lambda: job_row(pg_engine, job_id).succeeded >= 8,
        what="8 texts to be ok before the kill",
    )
    first.kill_hard()
    killed_at = time.monotonic()
    at_kill = job_row(pg_engine, job_id)
    assert at_kill.state == JobState.RUNNING
    assert at_kill.succeeded < 20

    second = start_worker("second", ai_wait=0.3)
    second_id = second.worker_id()
    wait_for(
        lambda: job_row(pg_engine, job_id).state == JobState.DONE,
        what="the second worker to finish the job",
    )
    # It could only take over once the heartbeat was older than the stale time.
    assert time.monotonic() - killed_at >= STALE - HEARTBEAT

    done = job_row(pg_engine, job_id)
    assert (done.succeeded, done.failed, done.total) == (20, 0, 20)
    assert done.crash_count == 1
    assert done.claimed_by == second_id
    with Session(pg_engine) as session:
        items = session.exec(select(JobItem).where(JobItem.job_id == job_id)).all()
    assert all(item.state == ItemState.OK and item.tries == 1 for item in items)

    # The second worker started only texts that were still waiting at the kill.
    first_started = set(first.started_texts())
    second_started = second.started_texts()
    assert len(second_started) == len(set(second_started)) == 20 - at_kill.succeeded
    redone = first_started & set(second_started)
    assert len(redone) <= 4
    # Every text was started at least once by one of the two: none lost.
    started_counts = Counter(first.started_texts() + second_started)
    assert {position for _, position in started_counts} == set(range(1, 21))
    assert done.resumed_from_position == min(position for _, position in second_started)


def test_politely_stopped_worker_hands_its_job_back(
    pg_engine,  # noqa: F811
    start_worker,
):
    """SIGTERM mid-job: job handed back; a second worker finishes it, no crash."""
    first = start_worker("first", ai_wait=0.3)
    first.worker_id()
    with Session(pg_engine) as session:
        job_id = make_job(session, texts(20), task=Task.LLM).id
    wait_for(
        lambda: job_row(pg_engine, job_id).succeeded >= 4,
        what="4 texts to be ok before the stop",
    )
    first.stop_politely()
    assert "handed back" in first.log()
    handed_back = job_row(pg_engine, job_id)
    assert handed_back.state == JobState.RUNNING
    assert handed_back.claimed_by is None

    second = start_worker("second", ai_wait=0.3)
    second_id = second.worker_id()
    picked_up = time.monotonic()
    wait_for(
        lambda: job_row(pg_engine, job_id).state == JobState.DONE,
        what="the second worker to finish the job",
    )
    done = job_row(pg_engine, job_id)
    assert (done.succeeded, done.failed) == (20, 0)
    assert done.crash_count == 0
    assert done.claimed_by == second_id
    assert "picked up after a clean stop" in second.log()
    # No waiting for the stale time: the rest (at most 16 texts, 4 at a time,
    # 0.3 s each) takes well under a few seconds.
    assert time.monotonic() - picked_up < 5
