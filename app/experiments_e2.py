"""Experiment E2 only: a job run inside the API with BackgroundTasks (spec 9.2).

E2 asks what happens to a job when the program doing it is killed halfway.
Kuyruk's real design runs every job in a separate *worker* program that keeps
everything in the database, and a stuck job is taken over by another worker
(crash recovery, spec 7.1). This file is the "broken" design E2 compares it
with, the one many tutorials show: the API does the job itself, using
FastAPI's *BackgroundTasks* (a list of functions FastAPI runs inside the API
process after it has sent the reply).

`POST /experiments/e2/jobs/{ticket}/run-in-api` marks one of your queued jobs
as running for the API and hands it to BackgroundTasks. The texts are done by
the worker's own code (`Worker.process_job` in `app/worker.py`), so they are
processed exactly as the worker would: 4 at once, the same retries, the same
"text started" log line (D75). The only differences are *where* the job runs
(inside the API, so it dies with the API) and that in E2's broken runs no
worker runs at all, so nothing ever looks for stuck jobs (D100).

All of this exists only with EXPERIMENTS_ENABLED=true (app/main.py adds the
address only then); otherwise it answers 404 like any address that doesn't
exist, and /docs doesn't show it. Kuyruk refuses to start with it in demo
mode (D95). The script that kills the API mid-job is `bench/e2_kill.py`.
"""

from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, status
from sqlalchemy import Engine

from app.ai import AIBackend, get_ai_backend
from app.auth import SessionDep
from app.db import get_engine
from app.errors import ApiError
from app.jobs import CurrentUser, get_own_job
from app.models import Job, JobPublic, JobState, db_now, utc_now
from app.states import change_job_state
from app.worker import Worker, make_worker_id

# The AI the API uses for these jobs. None = the one chosen by the settings
# (AI_BACKEND), like the worker; tests put a fake AI that doesn't wait here.
ai_override: AIBackend | None = None

# The address of this file. app/main.py adds it to Kuyruk only when
# EXPERIMENTS_ENABLED is true (D95).
router = APIRouter(prefix="/experiments/e2", tags=["experiments"])


@router.post(
    "/jobs/{ticket}/run-in-api",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=JobPublic,
)
def run_in_api(
    ticket: str,
    background_tasks: BackgroundTasks,
    session: SessionDep,
    user: CurrentUser,
    engine: Annotated[Engine, Depends(get_engine)],
) -> Job:
    """Start one of your queued jobs inside the API, with BackgroundTasks.

    Replies 202 with the job, now running; the texts are then done inside
    this API process, after the reply. Someone else's or an unknown ticket
    is 404 (like every job address); a job that is no longer queued (already
    taken by a worker, finished or cancelled) is 409 `job_not_queued`.
    """
    job = get_own_job(session, user, ticket)
    # "api-" in the name shows in the database and the logs that the API,
    # not a worker, had this job. No "I'm alive" file: that is the worker's.
    worker = Worker(
        engine,
        ai_override or get_ai_backend(),
        worker_id=f"api-{make_worker_id()}",
        alive_file="",
    )
    # Claimed the same way the worker claims a job: one conditional change,
    # only if the job is still queued (D51).
    claimed = change_job_state(
        session,
        job.id,
        JobState.QUEUED,
        JobState.RUNNING,
        claimed_by=worker.worker_id,
        started_at=utc_now(),
        # The database's clock, like every heartbeat (D68).
        last_active_at=db_now(),
    )
    if not claimed:
        session.refresh(job)
        raise ApiError(
            status.HTTP_409_CONFLICT,
            "job_not_queued",
            f"Only a queued job can be run in the API (this one is {job.state}).",
        )
    session.refresh(job)
    # FastAPI runs this after the reply is sent, inside this same process.
    # If the process dies, the job's progress dies with it (that is E2).
    background_tasks.add_task(worker.process_job, job.id, job.task)
    return job
