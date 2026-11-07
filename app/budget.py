"""The daily AI budget (spec 3.1, HAND-DESIGNED): how many tokens a user may use.

Each user may send `llm` jobs needing up to DAILY_TOKEN_BUDGET tokens per UTC
day (D11). A *token* is the small chunk of text an AI reads; it is counted
with the same counter as the `count_tokens` task (app/tokens.py).

This file holds the pieces of the budget:

- `tokens_for_job`: how many tokens a job's texts need, counted on the texts
  exactly as the AI will receive them (emails and phones hidden, D57);
- `tokens_used_today`: worked out fresh from the jobs table every time, never
  kept as a running total that could drift (D13, D58);
- `lock_user`: makes "check the budget, then save the job" one step per user,
  so two jobs sent at the same moment can't both pass (D14, D61);
- `check_daily_budget`: the check itself, with its two refusals (D59, D62);
- `GET /me/budget`: shows the user their limit, use and what is left.

The dependency that puts these together on `POST /jobs` is
`enforce_daily_budget` in app/jobs.py, because it also needs the job's
double-click protection, which lives there.
"""

import math
from collections.abc import Iterable
from datetime import datetime, time, timedelta
from typing import Annotated, NamedTuple

from fastapi import APIRouter, Depends, status
from sqlalchemy import and_, func, not_, update
from sqlmodel import Session, select

from app.auth import SessionDep, get_current_user
from app.errors import ApiError
from app.models import BudgetPublic, Job, JobState, Task, User, utc_now
from app.normalise import prepare_for_ai
from app.settings import settings
from app.tokens import count_tokens

router = APIRouter(tags=["budget"])


class BudgetExceeded(ApiError):
    """The 429 reply for a job that doesn't fit in what is left of today.

    429 means "too many requests: try again later". `Retry-After` tells the
    client how many seconds until the budget resets at the next UTC midnight.
    It is a kind of `ApiError`, so the shared error handler (app/errors.py)
    sends it in the usual error shape.
    """

    def __init__(self, needed: int, left: int, now: datetime) -> None:
        """Build the refusal: how much the job needs, how much is left."""
        seconds = math.ceil((next_utc_midnight(now) - now).total_seconds())
        super().__init__(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "budget_exceeded",
            f"This job needs {needed:,} tokens; you have {left:,} left today.",
            # Rounded up to whole seconds, so retrying then is never too early.
            headers={"Retry-After": str(seconds)},
        )


class JobTooBigForBudget(ApiError):
    """The 422 reply for a job that needs more than a whole day's budget.

    Such a job can never be accepted, not even tomorrow, so it gets no
    Retry-After (that would suggest waiting helps). The user must split it
    (D62). 422 means "this request itself is not acceptable".
    """

    def __init__(self, needed: int) -> None:
        """Build the refusal: how much the job needs, what the limit is."""
        super().__init__(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "job_exceeds_daily_budget",
            f"This job needs {needed:,} tokens; the daily limit is "
            f"{settings.daily_token_budget:,}. Split it into smaller jobs.",
        )


class BudgetPass(NamedTuple):
    """What a job that passed the budget check is saved with.

    `checked_at` is the moment of the check. The job is saved with it as its
    `created_at`, so the job counts on the same day it was checked against: a
    job checked at 23:59:59.999 and saved a moment later, after midnight,
    must not land on the next day without having been checked there.
    """

    tokens_needed: int
    checked_at: datetime


def next_utc_midnight(now: datetime) -> datetime:
    """Return the next midnight in UTC after `now`: when the budget resets."""
    today = now.date()
    return datetime.combine(today + timedelta(days=1), time(), tzinfo=now.tzinfo)


def tokens_for_job(texts: Iterable[str]) -> int:
    """Add up the tokens of a job's texts, as the AI will receive them (D57).

    Each text first goes through `prepare_for_ai`, the same step the worker
    uses before each AI call, so a long email address counts as the few tokens
    of "[EMAIL]". The fixed instruction and the AI's answers are not counted
    (see limits).
    """
    return sum(count_tokens(prepare_for_ai(text)) for text in texts)


def tokens_used_today(session: Session, user: User, now: datetime) -> int:
    """Add up the tokens of this user's `llm` jobs created today (UTC), now.

    Left out: jobs that ended without any AI work, i.e. cancelled or failed
    while still queued (`started_at` is empty: the worker never took them,
    D58). A job cancelled while running counts in full. Computed from the
    jobs table every time, never stored (D13).
    """
    start = datetime.combine(now.date(), time(), tzinfo=now.tzinfo)
    never_started = and_(
        Job.state.in_([JobState.CANCELLED, JobState.FAILED]),
        Job.started_at.is_(None),
    )
    total = session.exec(
        # coalesce(..., 0): the sum of no rows is empty (NULL), so use 0.
        select(func.coalesce(func.sum(Job.tokens_needed), 0)).where(
            Job.owner_id == user.id,
            Job.task == Task.LLM,
            Job.created_at >= start,
            Job.created_at < next_utc_midnight(now),
            not_(never_started),
        )
    ).one()
    return int(total)


def lock_user(session: Session, user: User) -> None:
    """Lock this user's row until this request saves or gives up (D14, D61).

    A *lock* is a "please wait" sign: while one request holds it, another
    request asking for the same lock waits until the first one's changes are
    saved (commit) or undone (rollback). Only requests of the same user wait
    for each other.

    Postgres: `SELECT ... FOR UPDATE` locks just this user's row.
    SQLite has no row locks and ignores FOR UPDATE, so there we make a write
    that changes nothing (username = username). Any write makes SQLite take its
    one write lock for the whole database, which the next writer waits for.
    """
    if session.get_bind().dialect.name == "postgresql":
        session.exec(select(User.id).where(User.id == user.id).with_for_update()).one()
    else:
        session.execute(
            update(User).where(User.id == user.id).values(username=User.username)
        )


def check_too_big(needed: int) -> None:
    """Refuse (422) a job that needs more than the whole daily limit (D62).

    No lock or database is needed: the answer doesn't depend on other jobs.
    """
    if needed > settings.daily_token_budget:
        raise JobTooBigForBudget(needed)


def check_daily_budget(session: Session, user: User, needed: int) -> BudgetPass:
    """Refuse (429) a job that doesn't fit in what is left of today (D59).

    Must be called while holding `lock_user`, so no other job of this user can
    be saved between this check and our own save. Exactly using up the budget
    is allowed. On refusal, the transaction is rolled back first, which saves
    nothing and lets go of the lock at once. On success, returns the
    `BudgetPass` the job is saved with.
    """
    now = utc_now()
    used = tokens_used_today(session, user, now)
    if used + needed > settings.daily_token_budget:
        session.rollback()
        left = max(settings.daily_token_budget - used, 0)
        raise BudgetExceeded(needed, left, now)
    return BudgetPass(tokens_needed=needed, checked_at=now)


@router.get("/me/budget", response_model=BudgetPublic)
def read_budget(
    session: SessionDep, user: Annotated[User, Depends(get_current_user)]
) -> BudgetPublic:
    """Show your daily token budget: the limit, used today, left, and reset time.

    `left` never goes below 0, and `resets_at` is the next UTC midnight.
    """
    now = utc_now()
    used = tokens_used_today(session, user, now)
    limit = settings.daily_token_budget
    return BudgetPublic(
        limit=limit,
        used=used,
        left=max(limit - used, 0),
        resets_at=next_utc_midnight(now),
    )
