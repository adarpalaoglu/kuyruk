"""Tests for the data model in `app/models.py`.

They check that the tables can store and return users, jobs and texts, that
defaults are right, and that the database itself refuses duplicates where the
spec says it must.
"""

import uuid
from datetime import UTC, datetime, timedelta, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, StatementError
from sqlmodel import Session, select

from app.models import ItemState, Job, JobItem, JobState, Task, User, utc_now


def make_user(session: Session, username: str = "ayse") -> User:
    """Save a user with a placeholder password hash and return it with its id."""
    user = User(username=username, hashed_password="not-a-real-hash")
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def test_user_job_and_texts_saved_and_read_back(session):
    user = make_user(session)
    job = Job(owner_id=user.id, task=Task.NORMALISE, total=3)
    session.add(job)
    session.commit()
    texts = ["Merhaba dünya", "Çok güzel bir ürün", "Kargo geç geldi"]
    for position, sample in enumerate(texts, start=1):
        session.add(JobItem(job_id=job.id, position=position, original_text=sample))
    session.commit()
    job_id = job.id
    # Forget everything held in memory so the reads below really come from the
    # database, not from the Python objects we just made.
    session.expunge_all()

    saved_user = session.exec(select(User)).one()
    saved_job = session.get(Job, job_id)
    saved_items = session.exec(
        select(JobItem).where(JobItem.job_id == job_id).order_by(JobItem.position)
    ).all()

    assert saved_user.username == "ayse"
    assert saved_user.created_at is not None
    assert isinstance(saved_job.id, uuid.UUID)
    assert saved_job.owner_id == saved_user.id
    assert saved_job.task == Task.NORMALISE
    assert saved_job.state == JobState.QUEUED
    assert (saved_job.total, saved_job.succeeded, saved_job.failed) == (3, 0, 0)
    assert saved_job.crash_count == 0
    assert saved_job.idempotency_key is None
    assert saved_job.claimed_by is None
    assert [item.original_text for item in saved_items] == texts
    assert [item.position for item in saved_items] == [1, 2, 3]
    assert all(item.state == ItemState.WAITING for item in saved_items)
    assert all(item.tries == 0 and item.result is None for item in saved_items)


def test_state_lists_match_the_spec():
    assert [s.value for s in JobState] == [
        "queued",
        "running",
        "done",
        "failed",
        "cancelled",
    ]
    assert [s.value for s in ItemState] == ["waiting", "ok", "failed"]
    assert [t.value for t in Task] == ["normalise", "count_tokens", "llm"]


def test_states_stored_as_plain_words(session):
    user = make_user(session)
    job = Job(owner_id=user.id, task=Task.COUNT_TOKENS)
    session.add(job)
    session.commit()
    session.add(JobItem(job_id=job.id, position=1, original_text="Merhaba"))
    session.commit()

    # Read the raw stored values with plain SQL, bypassing the Python model.
    job_row = session.execute(text("SELECT task, state FROM jobs")).one()
    item_state = session.execute(text("SELECT state FROM job_items")).scalar_one()

    assert tuple(job_row) == ("count_tokens", "queued")
    assert item_state == "waiting"


def test_database_refuses_a_state_not_on_the_list(session):
    user = make_user(session)
    job = Job(owner_id=user.id, task=Task.LLM)
    session.add(job)
    session.commit()

    with pytest.raises(IntegrityError):
        session.execute(text("UPDATE jobs SET state = 'paused'"))


def test_duplicate_username_rejected(session):
    make_user(session, "ayse")
    session.add(User(username="ayse", hashed_password="other-hash"))

    with pytest.raises(IntegrityError):
        session.commit()


def test_job_with_missing_owner_rejected(session):
    # No user 999 exists. Postgres refuses this; SQLite must refuse it too.
    session.add(Job(owner_id=999, task=Task.LLM))

    with pytest.raises(IntegrityError):
        session.commit()


def test_text_with_missing_job_rejected(session):
    session.add(JobItem(job_id=uuid.uuid4(), position=1, original_text="Merhaba"))

    with pytest.raises(IntegrityError):
        session.commit()


def test_same_idempotency_key_twice_for_one_user_rejected(session):
    user = make_user(session)
    session.add(Job(owner_id=user.id, task=Task.LLM, idempotency_key="abc"))
    session.commit()
    session.add(Job(owner_id=user.id, task=Task.LLM, idempotency_key="abc"))

    with pytest.raises(IntegrityError):
        session.commit()


def test_same_idempotency_key_allowed_for_two_users(session):
    ayse = make_user(session, "ayse")
    mehmet = make_user(session, "mehmet")
    session.add(Job(owner_id=ayse.id, task=Task.LLM, idempotency_key="abc"))
    session.add(Job(owner_id=mehmet.id, task=Task.LLM, idempotency_key="abc"))
    session.commit()

    assert len(session.exec(select(Job)).all()) == 2


def test_many_jobs_without_idempotency_key_allowed(session):
    user = make_user(session)
    session.add(Job(owner_id=user.id, task=Task.COUNT_TOKENS))
    session.add(Job(owner_id=user.id, task=Task.COUNT_TOKENS))
    session.commit()

    assert len(session.exec(select(Job)).all()) == 2


def test_times_come_back_labelled_as_utc(session):
    user = make_user(session)
    # 15:00 in Istanbul (UTC+3) is 12:00 UTC.
    istanbul = timezone(timedelta(hours=3))
    started = datetime(2026, 11, 6, 15, 0, tzinfo=istanbul)
    job = Job(owner_id=user.id, task=Task.LLM, started_at=started)
    session.add(job)
    session.commit()
    job_id = job.id
    session.expunge_all()

    saved = session.get(Job, job_id)

    assert saved.started_at == datetime(2026, 11, 6, 12, 0, tzinfo=UTC)
    assert saved.started_at.tzinfo == UTC
    assert saved.created_at.tzinfo == UTC
    assert saved.finished_at is None
    # The comparison later slices rely on must work without an error.
    assert saved.created_at <= utc_now()


def test_time_without_time_zone_refused(session):
    user = make_user(session)
    session.add(Job(owner_id=user.id, task=Task.LLM, started_at=datetime(2026, 1, 1)))

    with pytest.raises(StatementError):
        session.commit()
