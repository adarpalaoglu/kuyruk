"""`GET /status`: a public "is everything working?" report (D117, D118).

The web interface's System status screen reads it. Where `/health` only says
"the API answers" (for Docker and E1), `/status` looks at every part:

- the API itself (if this reply arrives, it works);
- the database: one tiny query (`SELECT 1`), plus how many jobs are running
  and waiting;
- the worker: how old its "I'm alive" file is (D79). The worker is a separate
  program in its own container; compose gives the API and the worker one
  shared folder, so the API can read the file's date. No database writes are
  added for this (D118);
- which AI the `llm` task uses, and whether demo mode is on;
- the limits the interface shows (texts per job, password length...), so no
  limit is ever typed into the interface by hand.

It needs no login, so it shows only counts and settings: never a user name or
a ticket.
"""

import time
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import Engine, func, text
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, select
from starlette.concurrency import run_in_threadpool

from app.db import get_engine
from app.models import (
    AiStatus,
    Job,
    JobState,
    LimitsPublic,
    StatusPublic,
    WorkerStatus,
)
from app.settings import settings

router = APIRouter(tags=["status"])


def worker_status(path: str, max_age_seconds: float) -> WorkerStatus:
    """Read the worker's "I'm alive" file and say how fresh it is (D79, D118).

    The worker re-dates the file about once a second when idle and at every
    heartbeat during a job. Its age is "now" minus the file's last-changed
    time (both from this computer's clock, which the containers share). No
    setting, or no file yet, means the API can't tell: "unknown".
    """
    if not path:
        return WorkerStatus(state="unknown", last_seen_seconds=None)
    try:
        changed = Path(path).stat().st_mtime
    except OSError:
        return WorkerStatus(state="unknown", last_seen_seconds=None)
    # A clock step could make the file look newer than now; never show a
    # negative age.
    age = max(time.time() - changed, 0.0)
    state = "ok" if age <= max_age_seconds else "down"
    return WorkerStatus(state=state, last_seen_seconds=round(age, 1))


def database_counts(engine: Engine) -> tuple[int, int] | None:
    """Ask the database "are you there?" and count running and waiting jobs.

    Returns (running, waiting), or None if the database doesn't answer. It
    opens its own short session, like the progress stream does, so a broken
    database gives "down" here instead of an error reply.
    """
    try:
        with Session(engine) as session:
            session.exec(select(text("1"))).one()
            counts = dict(
                session.exec(
                    select(Job.state, func.count())
                    .where(Job.state.in_([JobState.RUNNING, JobState.QUEUED]))
                    .group_by(Job.state)
                ).all()
            )
    except SQLAlchemyError:
        return None
    return counts.get(JobState.RUNNING, 0), counts.get(JobState.QUEUED, 0)


def current_limits() -> LimitsPublic:
    """Copy the limits the interface shows out of the settings."""
    return LimitsPublic(
        min_texts_per_job=settings.min_texts_per_job,
        max_texts_per_job=settings.max_texts_per_job,
        max_text_chars=settings.max_text_chars,
        username_min_length=settings.username_min_length,
        username_max_length=settings.username_max_length,
        password_min_length=settings.password_min_length,
        texts_at_once=settings.texts_at_once,
        max_tries_per_text=settings.max_tries_per_text,
        heartbeat_seconds=settings.heartbeat_seconds,
        stale_after_seconds=settings.stale_after_seconds,
        jobs_page_default=settings.jobs_page_default,
    )


@router.get("/status", response_model=StatusPublic)
async def system_status(engine: Annotated[Engine, Depends(get_engine)]) -> StatusPublic:
    """Report whether the API, database, worker and AI are working (public).

    `async def` like `/health` (D99), so the reply never waits for one of
    FastAPI's helper threads to come free just to start. The database part is
    blocking work, so it alone is handed to a helper thread
    (`run_in_threadpool`).
    """
    counts = await run_in_threadpool(database_counts, engine)
    return StatusPublic(
        api="ok",
        database="ok" if counts is not None else "down",
        worker=worker_status(
            settings.worker_alive_file, settings.worker_alive_max_age_seconds
        ),
        running_jobs=counts[0] if counts is not None else None,
        jobs_waiting=counts[1] if counts is not None else None,
        ai=AiStatus(
            backend=settings.ai_backend,
            wait_mode=settings.fake_ai_wait_mode
            if settings.ai_backend == "fake"
            else None,
            model=settings.ollama_model,
        ),
        demo_mode=settings.demo_mode,
        limits=current_limits(),
    )
