"""Demo mode only: `POST /demo/crash-worker`, a worker crash on purpose (D74).

In the demo (the video, the interface's "simulate a crash" button), people
should be able to watch crash recovery happen: a worker dies in the middle of
a job, and after the stale time another worker takes the job over and
finishes it (spec 7.1).

The API and the worker are separate programs (in Docker, separate
containers), so the API can't stop the worker directly. Both share the
database, so the API leaves a mark there instead: it sets `crash_requested`
on the caller's oldest running job, or on the job named by `?ticket=` (the
job page's button names its own job, D127). The worker running that job checks the
mark at every heartbeat and before each text and, when it sees it, exits on
the spot with no clean-up (`crash_now` in `app/worker.py`), like a power cut.

This is safe because it only works in demo mode (both the API and the worker
check DEMO_MODE), only for a logged-in user, and only on that user's own job.
Outside demo mode the address answers 404, exactly like an address that
doesn't exist, even before the login is checked.
"""

import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy import update
from sqlmodel import SQLModel, select

from app.auth import SessionDep
from app.errors import ApiError
from app.jobs import CurrentUser, get_own_job
from app.models import Job, JobState
from app.settings import settings

router = APIRouter(tags=["demo"])


def require_demo_mode() -> None:
    """Answer 404 unless demo mode is on, so the address seems not to exist.

    It runs before the login check (it is listed first on the route), so
    outside demo mode nobody learns that the address exists at all.
    """
    if not settings.demo_mode:
        raise ApiError(status.HTTP_404_NOT_FOUND, "not_found", "Not Found")


class CrashRequested(SQLModel):
    """The reply of POST /demo/crash-worker: which job's worker will crash."""

    ticket: uuid.UUID
    message: str


@router.post(
    "/demo/crash-worker",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_demo_mode)],
    # Shown on /docs only when demo mode is on.
    include_in_schema=settings.demo_mode,
)
def crash_worker(
    session: SessionDep, user: CurrentUser, ticket: str | None = None
) -> CrashRequested:
    """Tell the worker running one of your jobs to crash (demo mode only).

    Without `ticket` it is your oldest running job; with `?ticket=` it is that
    job (D127), so the button on a job's page crashes exactly the job on
    screen. Someone else's ticket, an unknown one or a malformed one gets the
    same 404 as everywhere else.

    The worker exits abruptly at its next heartbeat or before its next
    text, whichever comes first. After the stale time another worker takes
    the job over and carries on from the texts still waiting; the live
    progress stream shows a `recovered` event. Replies 202 (the crash
    happens a moment later, not during this request), or 409
    `no_running_job` if you have no running job (or the named job isn't
    running), since a crash would then show nothing.
    """
    if ticket is not None:
        # get_own_job gives the shared 404 for anything that isn't yours. A
        # job of yours that isn't running falls through to the 409 below.
        named = get_own_job(session, user, ticket)
        job_id = named.id if named.state == JobState.RUNNING else None
    else:
        job_id = session.exec(
            select(Job.id)
            .where(Job.owner_id == user.id, Job.state == JobState.RUNNING)
            .order_by(Job.started_at, Job.id)
            .limit(1)
        ).first()
    # The mark is set only if the job is still running, in case it finished
    # just after we looked.
    marked = job_id is not None and (
        session.execute(
            update(Job)
            .where(Job.id == job_id, Job.state == JobState.RUNNING)
            .values(crash_requested=True)
        ).rowcount
        == 1
    )
    session.commit()
    if not marked:
        raise ApiError(
            status.HTTP_409_CONFLICT,
            "no_running_job",
            "Start a job first: a crash only shows something while a job runs.",
        )
    return CrashRequested(
        ticket=job_id,
        message="The worker running this job will stop abruptly within a few "
        "seconds; another worker will take the job over after the stale time.",
    )
