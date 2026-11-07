"""Tests for the job and text state rules in `app/states.py` (spec section 4).

Every possible pair of states is tried: the allowed moves work, a move whose
starting state is out of date changes nothing, and every other move is
refused as a bug.
"""

from itertools import product

import pytest
from sqlmodel import select

from app.models import ItemState, Job, JobItem, JobState, Task, User
from app.states import (
    ITEM_TRANSITIONS,
    JOB_TRANSITIONS,
    ForbiddenTransition,
    change_item_state,
    change_job_state,
)

# Adar's arrows, written out by hand so a change to the table in the code is
# caught here.
ALLOWED_JOB_MOVES = {
    (JobState.QUEUED, JobState.RUNNING),
    (JobState.QUEUED, JobState.CANCELLED),
    (JobState.RUNNING, JobState.DONE),
    (JobState.RUNNING, JobState.FAILED),
    (JobState.RUNNING, JobState.CANCELLED),
}
ALLOWED_ITEM_MOVES = {
    (ItemState.WAITING, ItemState.OK),
    (ItemState.WAITING, ItemState.FAILED),
}
ALL_JOB_PAIRS = list(product(JobState, JobState))
ALL_ITEM_PAIRS = list(product(ItemState, ItemState))


def make_job(session, state: JobState) -> Job:
    """Save a user and one job of theirs in `state`, with one text."""
    user = User(username="ayse", hashed_password="not-a-real-hash")
    session.add(user)
    session.commit()
    job = Job(owner_id=user.id, task=Task.NORMALISE, total=1, state=state)
    session.add(job)
    session.add(JobItem(job_id=job.id, position=1, original_text="merhaba"))
    session.commit()
    return job


def test_tables_match_adars_rules():
    """The tables in the code hold exactly Adar's arrows, no more, no fewer."""
    job_moves = {(a, b) for a, targets in JOB_TRANSITIONS.items() for b in targets}
    item_moves = {(a, b) for a, targets in ITEM_TRANSITIONS.items() for b in targets}
    assert job_moves == ALLOWED_JOB_MOVES
    assert item_moves == ALLOWED_ITEM_MOVES


@pytest.mark.parametrize(("start", "end"), sorted(ALLOWED_JOB_MOVES))
def test_allowed_job_move_works(session, start, end):
    """An allowed move from the job's real state changes it and returns True."""
    job = make_job(session, start)
    assert change_job_state(session, job.id, start, end) is True
    session.refresh(job)
    assert job.state == end


@pytest.mark.parametrize(("start", "end"), sorted(ALLOWED_JOB_MOVES))
def test_allowed_job_move_from_wrong_state_changes_nothing(session, start, end):
    """If the job is no longer in `start`, nothing changes and it returns False.

    This is what keeps a cancel from being overwritten: the worker's
    "running -> done" does nothing once the job is cancelled.
    """
    other = next(state for state in JobState if state not in (start, end))
    job = make_job(session, other)
    assert change_job_state(session, job.id, start, end, error_message="x") is False
    session.refresh(job)
    assert job.state == other
    assert job.error_message is None


@pytest.mark.parametrize(
    ("start", "end"), [pair for pair in ALL_JOB_PAIRS if pair not in ALLOWED_JOB_MOVES]
)
def test_forbidden_job_move_is_refused(session, start, end):
    """Every move not in the table raises an error and changes nothing."""
    job = make_job(session, start)
    with pytest.raises(ForbiddenTransition, match="not allowed"):
        change_job_state(session, job.id, start, end)
    session.refresh(job)
    assert job.state == start


def test_job_move_sets_other_fields_in_the_same_step(session):
    """Extra fields (e.g. claimed_by) are saved together with the new state."""
    job = make_job(session, JobState.QUEUED)
    change_job_state(
        session, job.id, JobState.QUEUED, JobState.RUNNING, claimed_by="w1"
    )
    session.refresh(job)
    assert (job.state, job.claimed_by) == (JobState.RUNNING, "w1")


@pytest.mark.parametrize(("start", "end"), sorted(ALLOWED_ITEM_MOVES))
def test_allowed_item_move_works(session, start, end):
    """A waiting text can become ok or failed, with its result saved too."""
    job = make_job(session, JobState.RUNNING)
    item = job_item(session, job)
    assert change_item_state(session, item.id, start, end, result="r", tries=1)
    session.refresh(item)
    assert (item.state, item.result, item.tries) == (end, "r", 1)


def test_item_move_from_wrong_state_changes_nothing(session):
    """A text that is already ok is not changed by a second "waiting -> failed"."""
    job = make_job(session, JobState.RUNNING)
    item = job_item(session, job)
    change_item_state(session, item.id, ItemState.WAITING, ItemState.OK, result="r")
    assert not change_item_state(
        session, item.id, ItemState.WAITING, ItemState.FAILED, error_message="x"
    )
    session.refresh(item)
    assert (item.state, item.result, item.error_message) == (ItemState.OK, "r", None)


@pytest.mark.parametrize(
    ("start", "end"),
    [pair for pair in ALL_ITEM_PAIRS if pair not in ALLOWED_ITEM_MOVES],
)
def test_forbidden_item_move_is_refused(session, start, end):
    """Every text move not in the table (e.g. ok -> waiting) raises an error."""
    job = make_job(session, JobState.RUNNING)
    item = job_item(session, job)
    with pytest.raises(ForbiddenTransition):
        change_item_state(session, item.id, start, end)


def job_item(session, job: Job) -> JobItem:
    """The one text of `job`."""
    return session.exec(select(JobItem).where(JobItem.job_id == job.id)).one()
