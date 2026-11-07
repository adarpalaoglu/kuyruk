"""Kuyruk's worker: the separate process that actually does the jobs (spec 7).

The API only saves a job and gives back a ticket. This program, started with
`python -m app.worker`, runs on its own next to the API (D29). It:

1. every WORKER_POLL_SECONDS (and when it starts), first looks for a *stuck*
   job, one whose worker died, and takes it over (crash recovery, spec 7.1,
   HAND-DESIGNED by Adar); if there is none, it *claims* the oldest `queued`
   job (marks it as its own, so no other worker takes it);
2. works through the job's texts in position order, up to TEXTS_AT_ONCE at the
   same time, one text per AI call (D27, D28);
3. retries temporary AI errors (waits 1 s, then 2 s), fails a text at once on
   permanent errors (D16, D47), and stops the job after 10 failures in a row
   (D18, D54);
4. sends a *heartbeat* while a job runs: it refreshes the job's
   `last_active_at` regularly, with the database's clock, so a stuck job can
   be told from a slow one (D68);
5. checks before every text whether the user cancelled, or whether another
   worker has taken the job over (D72);
6. writes one log line each time it starts a text ("text started job=...
   position=... worker=..."), so experiment E2 can count redone texts (D75).

Every state change goes through `app/states.py`, which only changes a state if
it is still what the worker expects, so a user's cancel always wins (D51).

The worker is *asynchronous* ("async"): while one text waits for the AI, the
program carries on with the other texts and the heartbeat instead of standing
still. `await` marks the places where it may wait.
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

from sqlalchemy import Engine, func, or_, update
from sqlmodel import Session, select

from app.ai import AIBackend, PermanentAIError, TemporaryAIError, get_ai_backend
from app.models import ItemState, Job, JobItem, JobState, Task, db_now, utc_now
from app.normalise import normalise, prepare_for_ai
from app.settings import settings
from app.states import change_item_state, change_job_state
from app.tokens import count_tokens

log = logging.getLogger("kuyruk.worker")

# The error message of a job stopped by the "failures in a row" rule (spec 4).
AI_UNAVAILABLE = "AI unavailable"
# The message a text gets when a bug in Kuyruk (not the AI) made it fail. The
# real cause goes only to the worker's log (D56).
INTERNAL_ERROR = "Internal error."
# The error message of a job that was found stuck more often than
# MAX_CRASH_COUNT allows (D70).
CRASHED_TOO_OFTEN = "Crashed too many times"

# Why the worker stopped starting new texts of a job, if it did. "lost" means
# the job is no longer this worker's to finish: another worker took it over,
# or failed it for crashing too often (D72).
StopReason = Literal["cancelled", "ai_unavailable", "shutdown", "lost"]


def make_worker_id() -> str:
    """A name for this worker process, saved in `claimed_by` on its jobs.

    Computer name + process number + a short random part, e.g.
    "macbook-4121-3fa2b1", so two workers never share a name even on one
    machine. It shows which worker had a job when it crashed, and lets a
    worker tell whether a job is still its own (D72).
    """
    return f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:6]}"


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
            # The database's clock, like every heartbeat (D68).
            last_active_at=db_now(),
        ):
            return session.get(Job, candidate)


def take_over_stuck_job(
    session: Session,
    worker_id: str,
    *,
    stale_after_seconds: float = settings.stale_after_seconds,
    max_crash_count: int = settings.max_crash_count,
) -> Job | None:
    """Take over a stuck or handed-back job, or return None if there's none.

    Crash recovery, spec 7.1 (HAND-DESIGNED by Adar):

    - *Stuck* means: the job is `running`, but its heartbeat (`last_active_at`)
      is older than `stale_after_seconds`, by the database's clock (D68). A
      slow job is never stuck: its worker keeps beating while it waits for the
      AI. Only a dead or frozen worker stops beating.
    - Taking it over adds 1 to `crash_count`. A job may be taken over at most
      `max_crash_count` times: when the count would go above that, the job is
      failed with "Crashed too many times" instead (D70), and the next stuck
      job is looked at.
    - Otherwise this worker writes its name in `claimed_by`, beats at once,
      clears any demo crash mark (so it doesn't crash too), and saves the
      first waiting text's position for the `recovered` event (D73). The
      worker then works only on the texts still waiting: finished texts are
      never redone.
    - A job *handed back* by a worker that was stopped politely (running,
      with no name in `claimed_by`, D76) is taken at once, without waiting
      for the stale time. That was not a crash, so the crash count stays as
      it is and no `recovered` event follows.

    The takeover is one *compare-and-swap* (D69): the change is made only if
    the job is still running with the same `last_active_at` and `claimed_by`
    we just read. If another worker took it a moment earlier, or the old
    worker beat again, nothing changes and we move on, so two workers can never
    both take the same job. On Postgres, `FOR UPDATE SKIP LOCKED` also makes
    two workers look at different stuck jobs, as when claiming.
    """
    while True:
        stuck = session.exec(
            select(Job.id, Job.last_active_at, Job.claimed_by, Job.crash_count)
            .where(
                Job.state == JobState.RUNNING,
                or_(
                    Job.last_active_at < db_now(-stale_after_seconds),
                    Job.claimed_by.is_(None),
                ),
            )
            .order_by(Job.last_active_at, Job.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        ).first()
        if stuck is None:
            # Ends the read so no lock or transaction is left open.
            session.rollback()
            return None
        job_id, seen_active, seen_by, seen_crashes = stuck
        # "Nothing changed since we looked": the compare part of
        # compare-and-swap.
        unchanged = (Job.last_active_at == seen_active, Job.claimed_by == seen_by)
        if seen_by is None:
            # Handed back after a polite stop (D76): not a crash.
            if session.execute(
                update(Job)
                .where(Job.id == job_id, Job.state == JobState.RUNNING, *unchanged)
                .values(claimed_by=worker_id, last_active_at=db_now())
            ).rowcount:
                session.commit()
                log.info("Job %s picked up after a clean stop.", job_id)
                return session.get(Job, job_id)
            session.commit()
            continue
        crashes = seen_crashes + 1
        if crashes > max_crash_count:
            if change_job_state(
                session,
                job_id,
                JobState.RUNNING,
                JobState.FAILED,
                only_if=unchanged,
                crash_count=crashes,
                finished_at=utc_now(),
                error_message=CRASHED_TOO_OFTEN,
            ):
                log.warning("Job %s failed: crashed %d times.", job_id, crashes)
            continue
        # The lowest position still waiting, worked out by the database in
        # the same step as the takeover.
        first_waiting = (
            select(func.min(JobItem.position))
            .where(JobItem.job_id == job_id, JobItem.state == ItemState.WAITING)
            .scalar_subquery()
        )
        taken = session.execute(
            update(Job)
            .where(Job.id == job_id, Job.state == JobState.RUNNING, *unchanged)
            .values(
                crash_count=crashes,
                claimed_by=worker_id,
                last_active_at=db_now(),
                crash_requested=False,
                resumed_from_position=first_waiting,
            )
        )
        session.commit()
        if taken.rowcount == 1:
            log.warning(
                "Job %s taken over from %s (crash %d).", job_id, seen_by, crashes
            )
            return session.get(Job, job_id)


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
        stale_after_seconds: float = settings.stale_after_seconds,
        max_crash_count: int = settings.max_crash_count,
        demo_mode: bool = settings.demo_mode,
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
        self.stale_after_seconds = stale_after_seconds
        self.max_crash_count = max_crash_count
        # Only in demo mode does this worker obey a demo crash mark (D74).
        self.demo_mode = demo_mode
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

        The first look happens at once, so stuck jobs are taken over as soon
        as a worker starts (spec 7.1). When there is no job, wait
        `poll_seconds` before looking again (or less, if a stop request
        arrives meanwhile).
        """
        log.info("Worker %s started.", self.worker_id)
        while not self.stopping.is_set():
            try:
                worked = await self.run_one_job()
            except Exception:
                # E.g. the database is briefly unreachable. Log it and try again
                # after a pause rather than let the whole worker die. A job
                # left half done stays running; crash recovery takes it over.
                log.exception("Worker loop error; trying again shortly.")
                worked = False
            if not worked:
                with suppress(TimeoutError):
                    await asyncio.wait_for(self.stopping.wait(), self.poll_seconds)
        log.info("Worker %s stopped.", self.worker_id)

    async def run_one_job(self) -> bool:
        """Take one job and process it. Return False if there was none.

        Stuck jobs come first (D69): one has already waited for at least the
        stale time, and someone may be watching it. Only if there is none is
        a queued job claimed.
        """
        with Session(self.engine) as session:
            job = take_over_stuck_job(
                session,
                self.worker_id,
                stale_after_seconds=self.stale_after_seconds,
                max_crash_count=self.max_crash_count,
            )
            if job is None:
                job = claim_next_job(session, self.worker_id)
                if job is None:
                    return False
                log.info("Job %s claimed (%s).", job.id, job.task)
            job_id, task = job.id, job.task
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
        # Only texts still waiting: after a takeover, texts already ok or
        # failed are never redone (spec 7.1).
        with Session(self.engine) as session:
            items = session.exec(
                select(
                    JobItem.id, JobItem.position, JobItem.original_text, JobItem.tries
                )
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
                item = waiting.popleft()
                text_task = self.process_text(job_id, task, *item)
                in_flight.add(asyncio.create_task(text_task))
            # Nothing more to start: let the texts still running finish.
            while in_flight and not self.stopping.is_set():
                await asyncio.wait(
                    in_flight | {stop_requested}, return_when=asyncio.FIRST_COMPLETED
                )
                in_flight = self.remove_finished(in_flight)
            if in_flight:
                # A stop was requested: give the remaining texts a short grace
                # time, then give up on them. They stay waiting and are redone
                # by crash recovery ("at least once", D19).
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
        """Say whether to stop starting new texts of this job, and why.

        Besides a stop request, too many failures and a cancel, the job may no
        longer be ours (D72): if this worker was frozen past the stale time,
        another worker may have taken it over (or failed it for crashing too
        often). Then this worker must stop at once, so the two don't both
        work through the same texts.

        In demo mode it also checks for a demo crash mark here, as well as at
        each heartbeat (D74), so the crash comes quickly even in a job that
        would finish before the next heartbeat.
        """
        if self.stopping.is_set():
            return "shutdown"
        if self.failures_in_a_row >= self.stop_after_failures:
            return "ai_unavailable"
        with Session(self.engine) as session:
            state, claimed_by, crash_requested = session.exec(
                select(Job.state, Job.claimed_by, Job.crash_requested).where(
                    Job.id == job_id
                )
            ).one()
        if self.demo_mode and crash_requested and claimed_by == self.worker_id:
            log.warning("Demo crash requested for job %s: exiting now.", job_id)
            crash_now()
        if state == JobState.CANCELLED:
            return "cancelled"
        if state != JobState.RUNNING or claimed_by != self.worker_id:
            return "lost"
        return None

    async def process_text(
        self,
        job_id: uuid.UUID,
        task: Task,
        item_id: int,
        position: int,
        text: str,
        tries: int = 0,
    ) -> None:
        """Do one text, with retries for temporary errors, and save the outcome.

        `tries` is how many attempts of this text had already finished before,
        e.g. before a crash. Every finished attempt (an answer or an error)
        adds 1 to the text's tries in the database at once (D71). An attempt
        cut off by a crash never finished, so it doesn't count: a resumed text
        gets exactly the tries it has left.
        """
        # One line per text started, for E2 to count redone texts (D75).
        log.info(
            "text started job=%s position=%d worker=%s",
            job_id,
            position,
            self.worker_id,
        )
        while True:
            try:
                result = await self.run_task(task, text)
            except TemporaryAIError as error:
                tries += 1
                if tries < self.max_tries:
                    self.count_try(item_id)
                    # Waits double each time: 1 s, 2 s, ... (D16).
                    await self.sleep(self.retry_first_wait_seconds * 2 ** (tries - 1))
                    continue
                self.save_text(job_id, item_id, error=str(error))
            except PermanentAIError as error:
                self.save_text(job_id, item_id, error=str(error))
            except Exception:
                # A bug in a task, not an AI error: fail this text, keep the
                # worker going, and keep the details out of the user's view.
                log.exception("Text %s of job %s hit a bug.", item_id, job_id)
                self.save_text(job_id, item_id, error=INTERNAL_ERROR)
            else:
                self.save_text(job_id, item_id, result=result)
            return

    def count_try(self, item_id: int) -> None:
        """Add one finished (failed, to be retried) attempt to a text's tries.

        Only while the text is still waiting: if another worker has already
        saved this text, it is left alone. "+ 1" is done by the database.
        """
        with Session(self.engine) as session:
            session.execute(
                update(JobItem)
                .where(JobItem.id == item_id, JobItem.state == ItemState.WAITING)
                .values(tries=JobItem.tries + 1)
            )
            session.commit()

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
        *,
        result: str | None = None,
        error: str | None = None,
    ) -> None:
        """Save one text's outcome and add it to the job's counts.

        A text with an error is failed; otherwise it is ok with its result.
        The attempt that produced it adds 1 to the text's tries (D71).
        This is saved even if the job was cancelled meanwhile: a text that was
        already with the AI keeps its result (spec 4). It is saved only if the
        text is still waiting, so if two workers finish the same text (after
        a takeover, D72), only the first answer is kept and counted.
        """
        succeeded = error is None
        with Session(self.engine) as session:
            changed = change_item_state(
                session,
                item_id,
                ItemState.WAITING,
                ItemState.OK if succeeded else ItemState.FAILED,
                result=result,
                tries=JobItem.tries + 1,
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

        This runs beside the texts, not between them, so a text that waits a
        long time for the AI never stops it. That is how a slow job is told
        from a stuck one (D68): only a dead or frozen worker stops beating.

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

        "Now" is the database's clock, not this worker's (D68). Only a job
        this worker has claimed is touched. In demo mode the beat also checks
        for a demo crash mark on the job (D74); if there is one, the worker
        exits on the spot.
        """
        with Session(self.engine) as session:
            session.execute(
                update(Job)
                .where(Job.id == job_id, Job.claimed_by == self.worker_id)
                .values(last_active_at=db_now())
            )
            session.commit()
            if not self.demo_mode:
                return
            crash_requested = session.exec(
                select(Job.crash_requested).where(
                    Job.id == job_id, Job.claimed_by == self.worker_id
                )
            ).first()
        if crash_requested:
            log.warning("Demo crash requested for job %s: exiting now.", job_id)
            crash_now()

    def finish_job(self, job_id: uuid.UUID, reason: StopReason | None) -> None:
        """Record how the job ended.

        - every text tried: done (even if some failed, D15);
        - too many failures in a row: failed, "AI unavailable";
        - cancelled: nothing to do, the API already recorded it;
        - stop request: handed back (D76): still running, but with this
          worker's name removed, so the next worker picks it up at once and
          it doesn't count as a crash;
        - lost: another worker has the job now, so it is theirs to finish.
        Done and failed are only written if the job is still running and still
        this worker's (D72), so a cancel or a takeover that happened meanwhile
        is kept.
        """
        if reason == "shutdown":
            self.hand_back(job_id)
            return
        if reason in ("cancelled", "lost"):
            log.info("Job %s left as is (%s).", job_id, reason)
            return
        final = JobState.FAILED if reason == "ai_unavailable" else JobState.DONE
        with Session(self.engine) as session:
            changed = change_job_state(
                session,
                job_id,
                JobState.RUNNING,
                final,
                only_if=(Job.claimed_by == self.worker_id,),
                finished_at=utc_now(),
                error_message=AI_UNAVAILABLE if final == JobState.FAILED else None,
            )
        if changed:
            log.info("Job %s %s.", job_id, final)
        else:
            log.info(
                "Job %s was cancelled or taken over before it was marked %s.",
                job_id,
                final,
            )

    def hand_back(self, job_id: uuid.UUID) -> None:
        """After a polite stop, give the job back for the next worker (D76).

        The job stays running; only this worker's name is removed. Done only
        if the job is still running and still ours, so a cancel or a takeover
        that happened meanwhile is kept.
        """
        with Session(self.engine) as session:
            handed_back = session.execute(
                update(Job)
                .where(
                    Job.id == job_id,
                    Job.state == JobState.RUNNING,
                    Job.claimed_by == self.worker_id,
                )
                .values(claimed_by=None)
            ).rowcount
            session.commit()
        if handed_back:
            log.info("Job %s handed back for the next worker.", job_id)
        else:
            log.info("Job %s left as is (cancelled or taken over).", job_id)


def crash_now() -> None:  # pragma: no cover - ends the process; see below
    """Exit this process at once, with no cleanup: a demo crash (D74).

    `os._exit` stops the program immediately, like pulling the plug: no
    `finally` blocks, no "stopped" log line, nothing saved, connections just
    dropped. That is what a real crash looks like to the database. (A normal
    `sys.exit` would run clean-up code first, which a power cut never does.)
    Tests replace this function; the Postgres kill test and the real run show
    the real thing.
    """
    os._exit(1)


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
