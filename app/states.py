"""The job and text state rules (spec section 4, HAND-DESIGNED by Adar).

A job may only move along these arrows:

    queued -> running      the worker takes it
    queued -> cancelled    the user cancels before it starts
    running -> done        every text has been tried
    running -> failed      it can't carry on ("AI unavailable")
    running -> cancelled   the user cancels while it runs

A text may only move waiting -> ok or waiting -> failed.

Every state change in Kuyruk goes through `change_job_state` or
`change_item_state` below. They refuse any move not in the tables (that would
be a bug), and they change the database *conditionally*: "set it to done, but
only if it is still running" (D51). The database checks and changes in one
step, so when the API and the worker try to change the same job at the same
moment, whoever writes first wins and the other is told it lost. That is why
a cancel from the API can never be overwritten by the worker's final "done".
"""

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy import ColumnElement, update
from sqlmodel import Session

from app.models import ItemState, Job, JobItem, JobState

# The allowed arrows: each state, and the states it may move to. A state that
# isn't listed (done, failed, cancelled; ok, failed) is final: nothing follows.
JOB_TRANSITIONS: dict[JobState, set[JobState]] = {
    JobState.QUEUED: {JobState.RUNNING, JobState.CANCELLED},
    JobState.RUNNING: {JobState.DONE, JobState.FAILED, JobState.CANCELLED},
}
ITEM_TRANSITIONS: dict[ItemState, set[ItemState]] = {
    ItemState.WAITING: {ItemState.OK, ItemState.FAILED},
}


class ForbiddenTransition(Exception):
    """Code tried a state change that the rules don't allow, e.g. done -> running.

    This always means a bug in Kuyruk, so it is an error, never a quiet no-op.
    """


def check_allowed(
    transitions: dict[Any, set[Any]], from_state: Any, to_state: Any
) -> None:
    """Raise ForbiddenTransition unless `from_state -> to_state` is in the table."""
    if to_state not in transitions.get(from_state, set()):
        raise ForbiddenTransition(
            f"State change {from_state} -> {to_state} is not allowed."
        )


def change_job_state(
    session: Session,
    job_id: uuid.UUID,
    from_state: JobState,
    to_state: JobState,
    *,
    only_if: Sequence[ColumnElement[bool]] = (),
    **fields: Any,
) -> bool:
    """Move one job from `from_state` to `to_state`, only if it is still there.

    `fields` are other columns to set in the same step, e.g. `finished_at`.
    `only_if` adds more conditions that must also still hold, e.g. "still
    claimed by this worker" (D72) or "nothing changed since we looked" (D69).
    Returns True if the job changed, or False if its state had already moved on
    or a condition no longer held (someone else got there first); in that case
    nothing is changed at all. The change is saved (committed) at once.
    """
    check_allowed(JOB_TRANSITIONS, from_state, to_state)
    # One UPDATE ... WHERE id = ? AND state = ?: the condition and the change
    # happen together inside the database, so nobody can slip in between.
    statement = (
        update(Job)
        .where(Job.id == job_id, Job.state == from_state, *only_if)
        .values(state=to_state, **fields)
    )
    changed = session.execute(statement).rowcount == 1
    session.commit()
    return changed


def change_item_state(
    session: Session,
    item_id: int,
    from_state: ItemState,
    to_state: ItemState,
    **fields: Any,
) -> bool:
    """Move one text from `from_state` to `to_state`, only if it is still there.

    Works exactly like `change_job_state`, for a text instead of a job: e.g.
    `fields` holds the result, the number of tries and any error message.
    """
    check_allowed(ITEM_TRANSITIONS, from_state, to_state)
    statement = (
        update(JobItem)
        .where(JobItem.id == item_id, JobItem.state == from_state)
        .values(state=to_state, **fields)
    )
    changed = session.execute(statement).rowcount == 1
    session.commit()
    return changed
