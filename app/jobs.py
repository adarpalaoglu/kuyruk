"""The jobs API: send a job, read it, list your jobs, read its results.

- `POST /jobs` checks and saves a new job with all its texts, and replies 202
  (accepted, not yet done) with a ticket. The work itself is done later by the
  worker (a separate process, built in Slice 5).
- `GET /jobs/{ticket}` shows one job; `GET /jobs` lists your own jobs;
  `GET /jobs/{ticket}/results` shows one row per text.
- `POST /jobs/{ticket}/cancel` cancels a job that hasn't finished yet.

`POST /jobs` also checks the daily AI budget for `llm` jobs
(`enforce_daily_budget` below, with the budget rules in `app/budget.py`).

Every route needs a logged-in user (`get_current_user` from `app/auth.py`).
A *ticket* is the job's id, a UUID. Someone else's ticket is answered exactly
like a ticket that doesn't exist (404), so nobody can learn which tickets exist.
"""

import uuid
from typing import Annotated, NamedTuple

from fastapi import APIRouter, Depends, Header, Query, Response, status
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.auth import SessionDep, get_current_user
from app.budget import (
    BudgetPass,
    check_daily_budget,
    check_too_big,
    lock_user,
    tokens_for_job,
)
from app.errors import ApiError
from app.models import (
    Job,
    JobCreate,
    JobItem,
    JobItemPublic,
    JobPublic,
    JobState,
    Task,
    User,
    utc_now,
)
from app.settings import settings
from app.states import JOB_TRANSITIONS, change_job_state

router = APIRouter(tags=["jobs"])

# Shorthand for "this route needs a logged-in user".
CurrentUser = Annotated[User, Depends(get_current_user)]

# The optional Idempotency-Key header (double-click protection, D20). Only
# `enforce_daily_budget` asks for it (see `CheckedJob` for why).
IdempotencyKey = Annotated[
    str | None,
    Header(min_length=1, max_length=settings.idempotency_key_max_length),
]


class CheckedJob(NamedTuple):
    """Everything `POST /jobs` needs, read and checked once.

    `enforce_daily_budget` is the only place that asks FastAPI for the job body
    and the Idempotency-Key header, and it hands them on to the route in this
    package. If the route asked for them as well, FastAPI would check them
    twice and report every mistake twice. (FastAPI remembers a dependency's
    result only when it succeeds, so a shared "read the body" helper would
    still run twice when the body is broken.)

    `budget_pass` is None for jobs the budget does not count.
    """

    new_job: JobCreate
    idempotency_key: str | None
    budget_pass: BudgetPass | None


def job_not_found() -> ApiError:
    """The one 404 used for every "you can't see this job" case.

    The same reply for "someone else's", "doesn't exist" and "not even a
    valid ticket", so the reply never reveals that someone else's job exists.
    """
    return ApiError(status.HTTP_404_NOT_FOUND, "not_found", "Job not found.")


def get_own_job(session: Session, user: User, ticket: str) -> Job:
    """Return the job with this ticket if it belongs to `user`, otherwise 404.

    The ticket arrives as plain text (not as a UUID type) on purpose: if
    FastAPI checked it, a malformed ticket would get a 422, which would be a
    different reply from the 404 for other people's tickets.
    """
    try:
        job_id = uuid.UUID(ticket)
    except ValueError:
        raise job_not_found() from None
    # One query asks for "this ticket AND owned by you", so another user's job
    # and a missing job follow exactly the same path.
    job = session.exec(
        select(Job).where(Job.id == job_id, Job.owner_id == user.id)
    ).first()
    if job is None:
        raise job_not_found()
    return job


def find_job_by_key(session: Session, user: User, key: str) -> Job | None:
    """Return this user's job that was sent with Idempotency-Key `key`, if any."""
    return session.exec(
        select(Job).where(Job.owner_id == user.id, Job.idempotency_key == key)
    ).first()


def ensure_same_content(session: Session, job: Job, new_job: JobCreate) -> None:
    """Refuse (409) if a reused Idempotency-Key comes with a different job.

    Same key + same task + same texts in the same order is a repeat (e.g. a
    double-click) and is fine. Same key + anything different is almost
    certainly a client bug, so we say so rather than guess which one is meant.
    """
    saved_texts = session.exec(
        select(JobItem.original_text)
        .where(JobItem.job_id == job.id)
        .order_by(JobItem.position)
    ).all()
    if job.task != new_job.task or list(saved_texts) != new_job.texts:
        raise ApiError(
            status.HTTP_409_CONFLICT,
            "idempotency_key_reused",
            "This Idempotency-Key was already used for a different job.",
        )


def save_new_job(
    session: Session,
    user: User,
    new_job: JobCreate,
    key: str | None,
    budget_pass: BudgetPass | None,
) -> Job:
    """Save the job and all its texts in one transaction, and return the job.

    An `llm` job comes with the `budget_pass` from `enforce_daily_budget`: its
    token count, and the moment of the budget check, used as its creation
    time so it counts on the day it was checked against. Other jobs get None.

    A *transaction* is a group of database changes that are saved all
    together or not at all: there is never a job with only some of its texts.

    If two requests with the same Idempotency-Key arrive at the same moment,
    both may get here. The database's "one key per user" rule refuses the
    second save; we then return the first one's job instead, so only one job
    is ever created.
    """
    job = Job(
        owner_id=user.id,
        task=new_job.task,
        total=len(new_job.texts),
        idempotency_key=key,
    )
    if budget_pass is not None:
        job.tokens_needed = budget_pass.tokens_needed
        job.created_at = budget_pass.checked_at
    session.add(job)
    # The job's id is made in Python (a random UUID), so the texts can point at
    # it before anything is saved.
    session.add_all(
        JobItem(job_id=job.id, position=position, original_text=text)
        for position, text in enumerate(new_job.texts, start=1)
    )
    try:
        session.commit()
    except IntegrityError:
        # Undo the failed save so the session can be used again.
        session.rollback()
        if key is None:
            # Not an idempotency clash, so it's a real bug: let it become a 500.
            raise
        winner = find_job_by_key(session, user, key)
        if winner is None:
            raise
        ensure_same_content(session, winner, new_job)
        return winner
    session.refresh(job)
    return job


def enforce_daily_budget(
    new_job: JobCreate,
    session: SessionDep,
    user: CurrentUser,
    idempotency_key: IdempotencyKey = None,
) -> CheckedJob:
    """Check an `llm` job against the daily budget; hand the job to the route.

    A FastAPI *dependency* of `POST /jobs`: it runs before the route, using the
    same session and user. It is the one place that reads the request body
    and the Idempotency-Key header (see `CheckedJob`). The budget pass inside
    the reply is None when there is nothing to count. Order (spec 3.1,
    D57-D62):

    1. Only `llm` jobs use the AI, so other jobs are free (no budget pass).
    2. A repeat of an already saved job (same Idempotency-Key) is not a new
       job: no count, no check; the route returns the saved ticket (D60).
    3. Count the tokens before locking, so the lock is held only briefly, and
       refuse a job bigger than a whole day's budget at once (422, D62).
    4. Lock the user (`lock_user`), so the check below and the route's save
       are one step: a second job of the same user waits here until this one
       is saved or refused (D14, D61).
    5. Look for the key again: a double-click's twin may have been saved while
       we waited for the lock, and it must get its ticket, not a 429.
    6. Refuse (429) if today's use plus this job is over the budget.

    The lock is let go when the route saves the job (commit) or when the
    request ends without saving (the session is then rolled back).
    """
    # The reply for every job the budget does not count (steps 1, 2 and 5).
    free = CheckedJob(new_job, idempotency_key, None)
    if new_job.task != Task.LLM:
        return free
    if idempotency_key and find_job_by_key(session, user, idempotency_key):
        return free
    needed = tokens_for_job(new_job.texts)
    check_too_big(needed)
    lock_user(session, user)
    if idempotency_key and find_job_by_key(session, user, idempotency_key):
        return free
    budget_pass = check_daily_budget(session, user, needed)
    return CheckedJob(new_job, idempotency_key, budget_pass)


@router.post("/jobs", response_model=JobPublic, status_code=status.HTTP_202_ACCEPTED)
def create_job(
    response: Response,
    session: SessionDep,
    user: CurrentUser,
    checked: Annotated[CheckedJob, Depends(enforce_daily_budget)],
) -> Job:
    """Accept a new job and reply 202 with its ticket and a Location header.

    202 (Accepted), not 201 (Created): the texts have not been processed yet;
    Kuyruk has only accepted the job and given a ticket (D22). `Location`
    says where to check on it.

    The optional `Idempotency-Key` header is double-click protection (D20):
    sending the same job again with the same key returns the same ticket
    instead of making a second job.

    For `llm` jobs, `enforce_daily_budget` has already checked the budget and
    counted the tokens; they are saved on the job as `tokens_needed`, with the
    moment of the check as its `created_at`. The job itself also comes from
    `enforce_daily_budget`, so FastAPI checks the body only once.
    """
    new_job, idempotency_key, budget_pass = checked
    job = None
    if idempotency_key is not None:
        job = find_job_by_key(session, user, idempotency_key)
    if job is None:
        job = save_new_job(session, user, new_job, idempotency_key, budget_pass)
    else:
        ensure_same_content(session, job, new_job)
    response.headers["Location"] = f"/jobs/{job.id}"
    return job


@router.get("/jobs", response_model=list[JobPublic])
def list_jobs(
    session: SessionDep,
    user: CurrentUser,
    # Named `status` in the address (?status=queued); called `state_filter`
    # here because `status` is already the name of FastAPI's status numbers.
    state_filter: Annotated[JobState | None, Query(alias="status")] = None,
    limit: Annotated[
        int, Query(ge=1, le=settings.jobs_page_max)
    ] = settings.jobs_page_default,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[Job]:
    """List your own jobs, newest first, optionally only those in one state.

    `limit` is how many to show (one page) and `offset` how many to skip, so
    `?limit=50&offset=50` is the second page.
    """
    query = select(Job).where(Job.owner_id == user.id)
    if state_filter is not None:
        query = query.where(Job.state == state_filter)
    # Two jobs can share a creation time; ordering by id as well keeps the
    # order the same from one page to the next.
    query = (
        query.order_by(Job.created_at.desc(), Job.id.desc()).offset(offset).limit(limit)
    )
    return list(session.exec(query).all())


@router.get("/jobs/{ticket}", response_model=JobPublic)
def read_job(ticket: str, session: SessionDep, user: CurrentUser) -> Job:
    """Show one of your jobs: its state and counts."""
    return get_own_job(session, user, ticket)


@router.get("/jobs/{ticket}/results", response_model=list[JobItemPublic])
def read_results(ticket: str, session: SessionDep, user: CurrentUser) -> list[JobItem]:
    """Show one row per text of your job, in the order they were sent."""
    job = get_own_job(session, user, ticket)
    return list(
        session.exec(
            select(JobItem).where(JobItem.job_id == job.id).order_by(JobItem.position)
        ).all()
    )


@router.post("/jobs/{ticket}/cancel", response_model=JobPublic)
def cancel_job(ticket: str, session: SessionDep, user: CurrentUser) -> Job:
    """Cancel one of your jobs that is still queued or running (D17, D52).

    A queued job is cancelled at once. A running job is marked cancelled now;
    the worker notices before it starts its next text. Texts already with the
    AI finish and keep their results; texts not started stay waiting. A job
    that has already finished (done, failed or cancelled) gives 409.
    """
    job = get_own_job(session, user, ticket)
    # Keep trying while the rules allow "current state -> cancelled". The
    # change only happens if the job is still in the state we just read; if
    # the worker moved it meanwhile (e.g. queued -> running), read it again
    # and try from the new state. States only move forward, so this ends.
    while JobState.CANCELLED in JOB_TRANSITIONS.get(job.state, set()):
        if change_job_state(
            session, job.id, job.state, JobState.CANCELLED, finished_at=utc_now()
        ):
            session.refresh(job)
            return job
        session.refresh(job)
    raise ApiError(
        status.HTTP_409_CONFLICT,
        "job_finished",
        f"This job has already finished ({job.state}).",
    )
