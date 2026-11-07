"""Kuyruk's worker: the separate process that actually does the jobs (spec 7).

The API only saves a job and gives back a ticket. This program, started with
`python -m app.worker`, runs on its own next to the API (D29). It:

1. looks for the oldest `queued` job every WORKER_POLL_SECONDS and *claims* it
   (marks it as its own, so no other worker takes it);
2. works through the job's texts in position order, up to TEXTS_AT_ONCE at the
   same time, one text per AI call (D27, D28);
3. retries temporary AI errors (waits 1 s, then 2 s), fails a text at once on
   permanent errors (D16, D47), and stops the job after 10 failures in a row
   (D18, D54);
4. sends a *heartbeat* while a job runs: it refreshes the job's
   `last_active_at` regularly, so a stuck job can be told from a slow one;
5. checks before every text whether the user cancelled.

Every state change goes through `app/states.py`, which only changes a state if
it is still what the worker expects, so a user's cancel always wins (D51).

The worker is *asynchronous* ("async"): while one text waits for the AI, the
program carries on with the other texts and the heartbeat instead of standing
still. `await` marks the places where it may wait. Crash recovery (taking over
a stuck job) comes in Slice 8.
"""

import asyncio
import logging
import os
import signal
import socket
import uuid
from collections import deque
from collections.abc import Awaitable, Callable
from contextlib import suppress
from typing import Literal

from sqlalchemy import Engine, update
from sqlmodel import Session, select

from app.ai import AIBackend, PermanentAIError, TemporaryAIError, get_ai_backend
from app.models import ItemState, Job, JobItem, JobState, Task, utc_now
from app.normalise import hide_emails_and_phones, normalise, unicode_nfc
from app.settings import settings
from app.states import change_item_state, change_job_state
from app.tokens import count_tokens

log = logging.getLogger("kuyruk.worker")

# The error message of a job stopped by the "failures in a row" rule (spec 4).
AI_UNAVAILABLE = "AI unavailable"
# The message a text gets when a bug in Kuyruk (not the AI) made it fail. The
# real cause goes only to the worker's log (D56).
INTERNAL_ERROR = "Internal error."

# Why the worker stopped starting new texts of a job, if it did.
StopReason = Literal["cancelled", "ai_unavailable", "shutdown"]


def make_worker_id() -> str:
    """A name for this worker process, saved in `claimed_by` on its jobs.

    Computer name + process number + a short random part, e.g.
    "macbook-4121-3fa2b1", so two workers never share a name even on one
    machine. It lets Slice 8 see which worker had a job when it crashed.
    """
    return f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:6]}"


def prepare_for_ai(text: str) -> str:
    """Hide emails and phone numbers before a text is sent to the AI (D50).

    Only these two steps of `normalise` (NFC first, which the email pattern
    needs, D49); case and spacing stay as the user wrote them, so the AI sees
    the text as written, minus personal data.
    """
    return hide_emails_and_phones(unicode_nfc(text))


def claim_next_job(session: Session, worker_id: str) -> Job | None:
    """Take the oldest queued job for this worker, or return None if there's none.

    *Claiming* means: set the job to running and write our name on it, in one
    conditional step ("only if it is still queued"). If a cancel or another
    worker got there first, that job is skipped and the next one is tried.

    On Postgres, `FOR UPDATE SKIP LOCKED` locks the chosen row until we have
    claimed it, and makes other workers skip a row that is locked rather than
    wait for it, so two workers never pick the same job. SQLite has no row
    locks and ignores that part; the "only if still queued" condition alone
    keeps it safe there.
    """
    while True:
        candidate = session.exec(
            select(Job.id)
            .where(Job.state == JobState.QUEUED)
            .order_by(Job.created_at, Job.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        ).first()
        if candidate is None:
            # Ends the read so no lock or transaction is left open.
            session.rollback()
            return None
        now = utc_now()
        if change_job_state(
            session,
            candidate,
            JobState.QUEUED,
            JobState.RUNNING,
            claimed_by=worker_id,
            started_at=now,
            last_active_at=now,
        ):
            return session.get(Job, candidate)


class Worker:
    """One worker: claims jobs one at a time and processes their texts.

    The numbers (texts at once, tries, waits...) come from settings; tests can
    pass their own. `sleep` is the waiting function used between retries;
    tests pass one that doesn't really wait.
    """

    def __init__(
        self,
        engine: Engine,
        ai: AIBackend,
        *,
        worker_id: str | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        poll_seconds: float = settings.worker_poll_seconds,
        texts_at_once: int = settings.texts_at_once,
        max_tries: int = settings.max_tries_per_text,
        retry_first_wait_seconds: float = settings.retry_first_wait_seconds,
        stop_after_failures: int = settings.stop_after_failures_in_a_row,
        heartbeat_seconds: float = settings.heartbeat_seconds,
        shutdown_grace_seconds: float = settings.shutdown_grace_seconds,
    ) -> None:
        """Remember the database, the AI and the numbers to work with."""
        self.engine = engine
        self.ai = ai
        self.worker_id = worker_id or make_worker_id()
        self.sleep = sleep
        self.poll_seconds = poll_seconds
        self.texts_at_once = texts_at_once
        self.max_tries = max_tries
        self.retry_first_wait_seconds = retry_first_wait_seconds
        self.stop_after_failures = stop_after_failures
        self.heartbeat_seconds = heartbeat_seconds
        self.shutdown_grace_seconds = shutdown_grace_seconds
        # Set when the worker is asked to stop (Ctrl+C or a stop signal).
        self.stopping = asyncio.Event()
        # Texts of the current job that failed one after another; reset to 0 by
        # every success (D54).
        self.failures_in_a_row = 0

    def stop(self) -> None:
        """Ask the worker to stop: no new texts start, then it exits (D55)."""
        if not self.stopping.is_set():
            log.info("Stop requested; finishing texts already with the AI.")
        self.stopping.set()

    async def run_forever(self) -> None:
        """Keep claiming and processing jobs until asked to stop.

        When there is no queued job, wait `poll_seconds` before looking again
        (or less, if a stop request arrives meanwhile).
        """
        log.info("Worker %s started.", self.worker_id)
        while not self.stopping.is_set():
            try:
                worked = await self.run_one_job()
            except Exception:
                # E.g. the database is briefly unreachable. Log it and try again
                # after a pause rather than let the whole worker die. A job
                # left half done stays running; Slice 8's recovery handles it.
                log.exception("Worker loop error; trying again shortly.")
                worked = False
            if not worked:
                with suppress(TimeoutError):
                    await asyncio.wait_for(self.stopping.wait(), self.poll_seconds)
        log.info("Worker %s stopped.", self.worker_id)

    async def run_one_job(self) -> bool:
        """Claim one queued job and process it. Return False if there was none."""
        with Session(self.engine) as session:
            job = claim_next_job(session, self.worker_id)
            if job is None:
                return False
            job_id, task = job.id, job.task
        log.info("Job %s claimed (%s).", job_id, task)
        await self.process_job(job_id, task)
        return True

    async def process_job(self, job_id: uuid.UUID, task: Task) -> None:
        """Process every waiting text of a claimed job, then record how it ended."""
        self.failures_in_a_row = 0
        # The heartbeat runs alongside the texts until the job is over.
        heartbeat = asyncio.create_task(self.heartbeat(job_id))
        try:
            reason = await self.process_texts(job_id, task)
        finally:
            heartbeat.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat
        self.finish_job(job_id, reason)

    async def process_texts(self, job_id: uuid.UUID, task: Task) -> StopReason | None:
        """Start the texts in position order, at most `texts_at_once` at a time.

        Before each new text, check whether to stop (cancel, too many failures
        in a row, or a stop request). Texts already started are always allowed
        to finish, except after a stop request, when they get at most
        `shutdown_grace_seconds`. Returns why it stopped early, or None if
        every text was tried.
        """
        with Session(self.engine) as session:
            items = session.exec(
                select(JobItem.id, JobItem.original_text)
                .where(JobItem.job_id == job_id, JobItem.state == ItemState.WAITING)
                .order_by(JobItem.position)
            ).all()
        waiting = deque(items)
        in_flight: set[asyncio.Task] = set()
        # Finishes as soon as a stop is requested, so waiting for texts can be
        # interrupted by it.
        stop_requested = asyncio.create_task(self.stopping.wait())
        reason: StopReason | None = None
        try:
            while waiting:
                reason = self.reason_to_stop(job_id)
                if reason is not None:
                    break
                if len(in_flight) >= self.texts_at_once:
                    # All places taken: wait until one text finishes (or a stop
                    # request), then check again.
                    await asyncio.wait(
                        in_flight | {stop_requested},
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    in_flight = self.remove_finished(in_flight)
                    continue
                item_id, text = waiting.popleft()
                in_flight.add(
                    asyncio.create_task(self.process_text(job_id, task, item_id, text))
                )
            # Nothing more to start: let the texts still running finish.
            while in_flight and not self.stopping.is_set():
                await asyncio.wait(
                    in_flight | {stop_requested}, return_when=asyncio.FIRST_COMPLETED
                )
                in_flight = self.remove_finished(in_flight)
            if in_flight:
                # A stop was requested: give the remaining texts a short grace
                # time, then give up on them. They stay waiting and are redone
                # by crash recovery (Slice 8, "at least once").
                _, unfinished = await asyncio.wait(
                    in_flight, timeout=self.shutdown_grace_seconds
                )
                for text_task in unfinished:
                    text_task.cancel()
                await asyncio.gather(*unfinished, return_exceptions=True)
                self.remove_finished(in_flight)
                # If every text still finished in time, the job ends normally.
                if unfinished and reason is None:
                    reason = "shutdown"
        finally:
            stop_requested.cancel()
            # Normally nothing is left running here. Only if an error ended
            # this early: stop the texts still with the AI, so they can't
            # change the next job's failures in a row. They stay waiting.
            leftover = [text_task for text_task in in_flight if not text_task.done()]
            for text_task in leftover:
                text_task.cancel()
            await asyncio.gather(*leftover, return_exceptions=True)
        return reason

    @staticmethod
    def remove_finished(in_flight: set[asyncio.Task]) -> set[asyncio.Task]:
        """Return the texts still running; log any that ended with an error.

        A text task only ends with an error if saving its result failed (e.g.
        the database was unreachable). The text then stays waiting, for crash
        recovery to redo.
        """
        for text_task in in_flight:
            if text_task.done() and not text_task.cancelled():
                error = text_task.exception()
                if error is not None:
                    log.error("Saving a text failed.", exc_info=error)
        return {text_task for text_task in in_flight if not text_task.done()}

    def reason_to_stop(self, job_id: uuid.UUID) -> StopReason | None:
        """Say whether to stop starting new texts of this job, and why."""
        if self.stopping.is_set():
            return "shutdown"
        if self.failures_in_a_row >= self.stop_after_failures:
            return "ai_unavailable"
        with Session(self.engine) as session:
            state = session.exec(select(Job.state).where(Job.id == job_id)).one()
        # The only way a job we claimed stops being running is a cancel.
        if state != JobState.RUNNING:
            return "cancelled"
        return None

    async def process_text(
        self, job_id: uuid.UUID, task: Task, item_id: int, text: str
    ) -> None:
        """Do one text, with retries for temporary errors, and save the outcome."""
        tries = 0
        while True:
            tries += 1
            try:
                result = await self.run_task(task, text)
            except TemporaryAIError as error:
                if tries < self.max_tries:
                    # Waits double each time: 1 s, 2 s, ... (D16).
                    await self.sleep(self.retry_first_wait_seconds * 2 ** (tries - 1))
                    continue
                self.save_text(job_id, item_id, tries, error=str(error))
            except PermanentAIError as error:
                self.save_text(job_id, item_id, tries, error=str(error))
            except Exception:
                # A bug in a task, not an AI error: fail this text, keep the
                # worker going, and keep the details out of the user's view.
                log.exception("Text %s of job %s hit a bug.", item_id, job_id)
                self.save_text(job_id, item_id, tries, error=INTERNAL_ERROR)
            else:
                self.save_text(job_id, item_id, tries, result=result)
            return

    async def run_task(self, task: Task, text: str) -> str:
        """Run the job's task on one text and return the result as text."""
        if task == Task.NORMALISE:
            return normalise(text)
        if task == Task.COUNT_TOKENS:
            return str(count_tokens(text))
        return await self.ai.summarise(prepare_for_ai(text))

    def save_text(
        self,
        job_id: uuid.UUID,
        item_id: int,
        tries: int,
        *,
        result: str | None = None,
        error: str | None = None,
    ) -> None:
        """Save one text's outcome and add it to the job's counts.

        A text with an error is failed; otherwise it is ok with its result.
        This is saved even if the job was cancelled meanwhile: a text that was
        already with the AI keeps its result (spec 4).
        """
        succeeded = error is None
        with Session(self.engine) as session:
            changed = change_item_state(
                session,
                item_id,
                ItemState.WAITING,
                ItemState.OK if succeeded else ItemState.FAILED,
                result=result,
                tries=tries,
                error_message=error,
            )
            if changed:
                # "+ 1" is done by the database itself, so two texts finishing
                # at the same moment can't overwrite each other's count.
                column = Job.succeeded if succeeded else Job.failed
                session.execute(
                    update(Job).where(Job.id == job_id).values({column.key: column + 1})
                )
                session.commit()
        self.failures_in_a_row = 0 if succeeded else self.failures_in_a_row + 1

    async def heartbeat(self, job_id: uuid.UUID) -> None:
        """Refresh the job's `last_active_at` every `heartbeat_seconds`, forever.

        It is stopped (cancelled) when the job is over. A failed beat (e.g. the
        database briefly unreachable) is logged and the next one tried as
        usual; it must never end the heartbeat or stop the job being finished.
        """
        while True:
            await asyncio.sleep(self.heartbeat_seconds)
            try:
                self.beat(job_id)
            except Exception:
                log.exception("Heartbeat for job %s failed; trying again.", job_id)

    def beat(self, job_id: uuid.UUID) -> None:
        """One heartbeat: set the job's `last_active_at` to now.

        Only a job this worker has claimed is touched.
        """
        with Session(self.engine) as session:
            session.execute(
                update(Job)
                .where(Job.id == job_id, Job.claimed_by == self.worker_id)
                .values(last_active_at=utc_now())
            )
            session.commit()

    def finish_job(self, job_id: uuid.UUID, reason: StopReason | None) -> None:
        """Record how the job ended.

        - every text tried: done (even if some failed, D15);
        - too many failures in a row: failed, "AI unavailable";
        - cancelled: nothing to do, the API already recorded it;
        - stop request: left running for crash recovery (Slice 8).
        Done and failed are only written if the job is still running, so a
        cancel that arrived meanwhile is kept.
        """
        if reason in ("cancelled", "shutdown"):
            log.info("Job %s left as is (%s).", job_id, reason)
            return
        final = JobState.FAILED if reason == "ai_unavailable" else JobState.DONE
        with Session(self.engine) as session:
            changed = change_job_state(
                session,
                job_id,
                JobState.RUNNING,
                final,
                finished_at=utc_now(),
                error_message=AI_UNAVAILABLE if final == JobState.FAILED else None,
            )
        if changed:
            log.info("Job %s %s.", job_id, final)
        else:
            log.info("Job %s was cancelled before it was marked %s.", job_id, final)


async def serve(worker: Worker) -> None:
    """Run the worker until Ctrl+C (SIGINT) or a stop signal (SIGTERM).

    A *signal* is a message the operating system sends a program; Docker sends
    SIGTERM when it stops a container. Either one asks for a clean stop.
    """
    loop = asyncio.get_running_loop()
    for stop_signal in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(stop_signal, worker.stop)
    try:
        await worker.run_forever()
    finally:
        for stop_signal in (signal.SIGINT, signal.SIGTERM):
            loop.remove_signal_handler(stop_signal)


def main() -> None:
    """Start the worker: set up logging, make sure the tables exist, then run."""
    # Imported here so that importing this file (e.g. in tests) never connects
    # to the real database.
    from app.db import create_db_and_tables, engine

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    create_db_and_tables()
    asyncio.run(serve(Worker(engine, get_ai_backend())))


if __name__ == "__main__":  # pragma: no cover - runs only as `python -m app.worker`
    main()
