"""Tests for the daily AI budget (`app/budget.py`, `enforce_daily_budget`).

Requests go through the real app with the in-memory SQLite test database (the
`client` fixture in conftest.py). Each test sets its own small budget with
`monkeypatch` (a pytest helper that changes a value for one test only), so
the numbers stay small and easy to follow. The Postgres race test, with two
jobs sent at the same moment, is in test_budget_postgres.py.
"""

import math
import sqlite3
import unicodedata
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.exc import OperationalError
from sqlmodel import Session, SQLModel, create_engine, select, text

from app import budget, jobs
from app.budget import next_utc_midnight, tokens_for_job, tokens_used_today
from app.models import Job, JobItem, JobState, Task, User
from app.settings import settings
from app.tokens import count_tokens

# One review, used as a job's only text. Its token count is worked out by the
# tests themselves (`NEEDED`), so the tests don't depend on the exact number.
REVIEW = "Kargo çok hızlı geldi, ürün de güzel ama kutusu ezikti."
NEEDED = count_tokens(REVIEW)


def send_job(client, headers, texts=(REVIEW,), task="llm", key=None):
    """Send a job and return the reply. `key` adds an Idempotency-Key header."""
    if key is not None:
        headers = {**headers, "Idempotency-Key": key}
    return client.post(
        "/jobs", json={"task": task, "texts": list(texts)}, headers=headers
    )


def set_budget(monkeypatch, tokens: int) -> None:
    """Make the daily budget `tokens` for this test only."""
    monkeypatch.setattr(settings, "daily_token_budget", tokens)


def used_today(client, headers) -> int:
    """Tokens used today, as GET /me/budget reports them."""
    return client.get("/me/budget", headers=headers).json()["used"]


def count_jobs(session) -> int:
    """How many jobs are saved right now."""
    return len(session.exec(select(Job)).all())


def edit_job(engine, ticket, **fields) -> None:
    """Change some fields of a saved job directly in the database."""
    with Session(engine) as session:
        job = session.get(Job, uuid.UUID(ticket))
        for name, value in fields.items():
            setattr(job, name, value)
        session.add(job)
        session.commit()


# --- A job bigger than the whole daily limit (D62) --------------------------


def test_job_just_at_daily_limit_is_accepted(client, log_in_as, monkeypatch):
    """A job needing exactly the whole budget fits (it's not *more*)."""
    set_budget(monkeypatch, NEEDED)
    reply = send_job(client, log_in_as("ayse"))
    assert reply.status_code == 202
    assert reply.json()["tokens_needed"] == NEEDED


def test_job_just_over_daily_limit_is_422_without_retry_after(
    client, log_in_as, session, monkeypatch
):
    """One token over the whole budget can never fit: 422, not 429 (D62)."""
    set_budget(monkeypatch, NEEDED - 1)
    reply = send_job(client, log_in_as("ayse"))
    assert reply.status_code == 422
    assert reply.json() == {
        "error": {
            "code": "job_exceeds_daily_budget",
            "message": f"This job needs {NEEDED:,} tokens; the daily limit is "
            f"{NEEDED - 1:,}. Split it into smaller jobs.",
        }
    }
    # Waiting won't help, so no "try again after".
    assert "Retry-After" not in reply.headers
    assert count_jobs(session) == 0


def test_too_big_message_uses_thousands_commas(client, log_in_as, monkeypatch):
    """Big numbers are written 340,000 so they are easy to read."""
    set_budget(monkeypatch, 1_000)
    texts = [REVIEW] * 200
    needed = tokens_for_job(texts)
    reply = send_job(client, log_in_as("ayse"), texts=texts)
    assert reply.json()["error"]["message"] == (
        f"This job needs {needed:,} tokens; the daily limit is 1,000. "
        "Split it into smaller jobs."
    )
    assert "," in f"{needed:,}"


# --- Under, exactly at, and over what's left today --------------------------


def test_jobs_under_the_budget_are_accepted(client, log_in_as, monkeypatch):
    """Two jobs that together use less than the budget both pass."""
    set_budget(monkeypatch, 3 * NEEDED)
    headers = log_in_as("ayse")
    assert send_job(client, headers).status_code == 202
    assert send_job(client, headers).status_code == 202
    assert used_today(client, headers) == 2 * NEEDED


def test_job_using_exactly_what_is_left_is_accepted(client, log_in_as, monkeypatch):
    """Reaching the budget exactly is allowed; only going over is refused."""
    set_budget(monkeypatch, 2 * NEEDED)
    headers = log_in_as("ayse")
    send_job(client, headers)
    assert send_job(client, headers).status_code == 202
    assert used_today(client, headers) == 2 * NEEDED


def test_job_one_token_over_what_is_left_is_429(
    client, log_in_as, session, monkeypatch
):
    """One token more than what's left refuses the job and saves nothing."""
    set_budget(monkeypatch, 2 * NEEDED - 1)
    headers = log_in_as("ayse")
    send_job(client, headers)
    reply = send_job(client, headers)
    assert reply.status_code == 429
    assert reply.json() == {
        "error": {
            "code": "budget_exceeded",
            "message": f"This job needs {NEEDED:,} tokens; "
            f"you have {NEEDED - 1:,} left today.",
        }
    }
    assert reply.headers["X-Request-ID"]
    assert count_jobs(session) == 1
    assert used_today(client, headers) == NEEDED


def test_429_retry_after_counts_seconds_to_utc_midnight(
    client, log_in_as, engine, monkeypatch
):
    """Retry-After says how long until the budget resets, rounded up."""
    frozen = datetime(2026, 11, 7, 23, 59, 30, 250_000, tzinfo=UTC)
    # Freeze "now" for the budget only; 29.75 s are left until midnight.
    monkeypatch.setattr(budget, "utc_now", lambda: frozen)
    set_budget(monkeypatch, NEEDED)
    headers = log_in_as("ayse")
    first = send_job(client, headers).json()["id"]
    # Make the first job belong to the frozen day.
    edit_job(engine, first, created_at=frozen - timedelta(hours=1))
    reply = send_job(client, headers)
    assert reply.status_code == 429
    assert reply.headers["Retry-After"] == "30"
    assert reply.json()["error"]["message"].endswith("you have 0 left today.")


def test_message_uses_thousands_commas(client, log_in_as, monkeypatch):
    """The 429 message writes big numbers as 12,000 and 3,000."""
    texts = [REVIEW] * 300
    needed = tokens_for_job(texts)
    set_budget(monkeypatch, needed + 3_000)
    headers = log_in_as("ayse")
    send_job(client, headers, texts=texts)
    reply = send_job(client, headers, texts=texts)
    assert reply.json()["error"]["message"] == (
        f"This job needs {needed:,} tokens; you have 3,000 left today."
    )
    assert needed > 3_000


# --- What is counted ---------------------------------------------------------


@pytest.mark.parametrize("task", [Task.NORMALISE, Task.COUNT_TOKENS])
def test_normalise_and_count_tokens_jobs_are_free(client, log_in_as, monkeypatch, task):
    """Cleaning and counting don't use the AI: never counted, never refused."""
    set_budget(monkeypatch, 1)
    headers = log_in_as("ayse")
    reply = send_job(client, headers, texts=[REVIEW] * 50, task=task)
    assert reply.status_code == 202
    assert reply.json()["tokens_needed"] is None
    assert used_today(client, headers) == 0


def test_tokens_are_counted_on_the_text_with_email_and_phone_hidden(client, log_in_as):
    """The AI receives "[EMAIL]" and "[TELEFON]", so that is what is counted."""
    original = (
        "Bana çok-uzun-bir-adres.ornegi@musteri-hizmetleri.example.com.tr "
        "veya 0532 123 45 67 numarasından ulaşın."
    )
    hidden = "Bana [EMAIL] veya [TELEFON] numarasından ulaşın."
    reply = send_job(client, log_in_as("ayse"), texts=[original])
    assert reply.json()["tokens_needed"] == count_tokens(hidden)
    assert count_tokens(hidden) < count_tokens(original)


def test_tokens_are_counted_after_joining_two_part_letters(client, log_in_as):
    """A letter stored in two parts is counted as the AI gets it: joined (NFC)."""
    two_part = unicodedata.normalize("NFD", "Şişli'de güzel bir gün")
    reply = send_job(client, log_in_as("ayse"), texts=[two_part])
    assert reply.json()["tokens_needed"] == count_tokens(
        unicodedata.normalize("NFC", two_part)
    )


def test_tokens_of_all_texts_are_added_up(client, log_in_as):
    """A job's count is the sum over its texts."""
    texts = [REVIEW, "Hızlı kargo.", "Berbat 🙄"]
    reply = send_job(client, log_in_as("ayse"), texts=texts)
    assert reply.json()["tokens_needed"] == sum(count_tokens(t) for t in texts)


# --- Which jobs count today (D58) --------------------------------------------


def test_yesterdays_jobs_do_not_count(client, log_in_as, engine, monkeypatch):
    """A job from before today's UTC midnight no longer uses today's budget."""
    set_budget(monkeypatch, NEEDED)
    headers = log_in_as("ayse")
    ticket = send_job(client, headers).json()["id"]
    edit_job(engine, ticket, created_at=datetime.now(UTC) - timedelta(days=1))
    assert used_today(client, headers) == 0
    assert send_job(client, headers).status_code == 202


def test_job_just_before_midnight_counts_only_for_that_day(log_in_as, engine):
    """The day runs from 00:00:00 UTC up to (not including) the next 00:00."""
    log_in_as("ayse")
    midnight = datetime(2026, 11, 7, tzinfo=UTC)
    with Session(engine) as session:
        user = session.exec(select(User)).one()
        for created in (midnight - timedelta(microseconds=1), midnight):
            job = Job(owner_id=user.id, task=Task.LLM, tokens_needed=5)
            job.created_at = created
            session.add(job)
        session.commit()
        assert tokens_used_today(session, user, midnight) == 5
        assert tokens_used_today(session, user, midnight - timedelta(hours=1)) == 5


def test_job_checked_before_midnight_is_saved_on_that_day(
    client, log_in_as, session, monkeypatch
):
    """A job checked at 23:59:59.999 counts on that day, even if saved later.

    Before this fix, the job got its creation time when it was saved, a moment
    after the check. A save after midnight put it on the next day, which never
    checked it, so a user could go over the next day's budget by one job.
    """
    checked = datetime(2026, 11, 7, 23, 59, 59, 999_000, tzinfo=UTC)
    monkeypatch.setattr(budget, "utc_now", lambda: checked)
    reply = send_job(client, log_in_as("ayse"))
    job = session.get(Job, uuid.UUID(reply.json()["id"]))
    assert job.created_at == checked


def test_job_cancelled_while_queued_does_not_count(client, log_in_as, monkeypatch):
    """A job cancelled before the worker took it never used the AI."""
    set_budget(monkeypatch, NEEDED)
    headers = log_in_as("ayse")
    ticket = send_job(client, headers).json()["id"]
    client.post(f"/jobs/{ticket}/cancel", headers=headers)
    assert used_today(client, headers) == 0
    assert send_job(client, headers).status_code == 202


@pytest.mark.parametrize(
    ("state", "started", "counts"),
    [
        (JobState.FAILED, False, False),  # failed before starting
        (JobState.CANCELLED, False, False),  # cancelled while queued
        (JobState.QUEUED, False, True),  # still waiting: will use the AI
        (JobState.RUNNING, True, True),
        (JobState.CANCELLED, True, True),  # cancelled while running: no refund
        (JobState.FAILED, True, True),  # e.g. "AI unavailable"
        (JobState.DONE, True, True),
    ],
)
def test_which_jobs_count(client, log_in_as, engine, state, started, counts):
    """Only jobs that ended without any AI work are left out of today's use."""
    headers = log_in_as("ayse")
    ticket = send_job(client, headers).json()["id"]
    edit_job(
        engine,
        ticket,
        state=state,
        started_at=datetime.now(UTC) if started else None,
    )
    assert used_today(client, headers) == (NEEDED if counts else 0)


def test_other_users_jobs_do_not_count(client, log_in_as, monkeypatch):
    """Each user has their own budget."""
    set_budget(monkeypatch, NEEDED)
    send_job(client, log_in_as("ayse"))
    mehmet = log_in_as("mehmet")
    assert used_today(client, mehmet) == 0
    assert send_job(client, mehmet).status_code == 202


# --- Double-click protection comes first (D60) --------------------------------


def test_replay_with_same_key_is_not_counted_or_checked_again(
    client, log_in_as, session, monkeypatch
):
    """The budget is full after the first send; the repeat still gets its ticket."""
    set_budget(monkeypatch, NEEDED)
    headers = log_in_as("ayse")
    first = send_job(client, headers, key="tik-1")
    again = send_job(client, headers, key="tik-1")
    assert again.status_code == 202
    assert again.json()["id"] == first.json()["id"]
    assert used_today(client, headers) == NEEDED
    assert count_jobs(session) == 1


def test_reused_key_with_different_texts_is_409_not_429(client, log_in_as, monkeypatch):
    """A reused key with another job is a client mistake, whatever the budget."""
    set_budget(monkeypatch, NEEDED)
    headers = log_in_as("ayse")
    send_job(client, headers, key="tik-1")
    reply = send_job(client, headers, texts=["Başka bir metin."], key="tik-1")
    assert reply.status_code == 409
    assert reply.json()["error"]["code"] == "idempotency_key_reused"


def test_twin_saved_while_waiting_for_the_lock_gets_its_ticket(
    client, log_in_as, engine, monkeypatch
):
    """A double-click's twin saved during our wait for the lock is a replay.

    Without the second key check after the lock, this request would count the
    twin's tokens as "used" and wrongly refuse its own repeat with a 429.
    """
    set_budget(monkeypatch, NEEDED)
    headers = log_in_as("ayse")
    original_lock = jobs.lock_user
    twin = {}

    def twin_saved_first(session, user):
        """Pretend the twin request saves its job while we wait for the lock."""
        with Session(engine) as other:
            job = Job(
                owner_id=user.id,
                task=Task.LLM,
                total=1,
                tokens_needed=NEEDED,
                idempotency_key="tik-1",
            )
            other.add(job)
            other.add(JobItem(job_id=job.id, position=1, original_text=REVIEW))
            other.commit()
            twin["id"] = str(job.id)
        original_lock(session, user)

    monkeypatch.setattr(jobs, "lock_user", twin_saved_first)
    reply = send_job(client, headers, key="tik-1")
    assert reply.status_code == 202
    assert reply.json()["id"] == twin["id"]
    assert used_today(client, headers) == NEEDED


# --- GET /me/budget ---------------------------------------------------------


def test_budget_page_for_a_new_user(client, log_in_as):
    """Nothing used yet: everything left, reset at the next UTC midnight."""
    reply = client.get("/me/budget", headers=log_in_as("ayse"))
    assert reply.status_code == 200
    body = reply.json()
    assert body["limit"] == settings.daily_token_budget
    assert body["used"] == 0
    assert body["left"] == settings.daily_token_budget
    resets_at = datetime.fromisoformat(body["resets_at"])
    assert resets_at == next_utc_midnight(datetime.now(UTC))
    assert set(body) == {"limit", "used", "left", "resets_at"}


def test_budget_page_after_jobs(client, log_in_as, monkeypatch):
    """Used and left change as llm jobs are sent."""
    set_budget(monkeypatch, 10 * NEEDED)
    headers = log_in_as("ayse")
    send_job(client, headers)
    send_job(client, headers, task="normalise")
    body = client.get("/me/budget", headers=headers).json()
    assert (body["limit"], body["used"], body["left"]) == (
        10 * NEEDED,
        NEEDED,
        9 * NEEDED,
    )


def test_budget_page_left_never_below_zero(client, log_in_as, monkeypatch):
    """If the limit is lowered after jobs were sent, left shows 0, not less."""
    headers = log_in_as("ayse")
    send_job(client, headers)
    set_budget(monkeypatch, 1)
    body = client.get("/me/budget", headers=headers).json()
    assert body["used"] == NEEDED
    assert body["left"] == 0


def test_budget_page_needs_login(client):
    """No login pass: 401 in the shared error shape."""
    reply = client.get("/me/budget")
    assert reply.status_code == 401
    assert reply.json()["error"]["code"] == "not_authenticated"


# --- Small pieces -------------------------------------------------------------


@pytest.mark.parametrize(
    ("now", "midnight"),
    [
        (datetime(2026, 11, 7, 0, 0, tzinfo=UTC), datetime(2026, 11, 8, tzinfo=UTC)),
        (
            datetime(2026, 11, 7, 23, 59, 59, tzinfo=UTC),
            datetime(2026, 11, 8, tzinfo=UTC),
        ),
        (datetime(2026, 12, 31, 12, tzinfo=UTC), datetime(2027, 1, 1, tzinfo=UTC)),
    ],
)
def test_next_utc_midnight(now, midnight):
    """The reset time is always the start of the next UTC day."""
    assert next_utc_midnight(now) == midnight


def test_retry_after_rounds_up():
    """0.25 s before midnight is "retry after 1 second", never 0."""
    now = datetime(2026, 11, 7, 23, 59, 59, 750_000, tzinfo=UTC)
    error = budget.BudgetExceeded(needed=5, left=0, now=now)
    assert error.headers == {"Retry-After": str(math.ceil(0.25))}


def test_lock_user_on_sqlite_makes_other_writers_wait(tmp_path):
    """On SQLite, `lock_user` takes the database's write lock until commit.

    Two separate connections to one database file: while the first holds the
    lock, the second can't write (it gives up after a 0.1 s wait); once the
    first is done, the second can.
    """
    url = f"sqlite:///{tmp_path / 'lock.db'}"
    first_engine = create_engine(url)
    second_engine = create_engine(url, connect_args={"timeout": 0.1})
    SQLModel.metadata.create_all(first_engine)
    with Session(first_engine) as session:
        session.add(User(username="ayse", hashed_password="x"))
        session.commit()
    with Session(first_engine) as first, Session(second_engine) as second:
        user = first.exec(select(User)).one()
        budget.lock_user(first, user)
        with pytest.raises(OperationalError) as caught:
            second.execute(text("UPDATE users SET username = username"))
        assert isinstance(caught.value.orig, sqlite3.OperationalError)
        assert "locked" in str(caught.value.orig)
        second.rollback()
        first.rollback()
        # The lock is gone: the second connection can write now.
        second.execute(text("UPDATE users SET username = username"))
        second.commit()
        # The lock changed nothing.
        assert first.exec(select(User.username)).one() == "ayse"
    first_engine.dispose()
    second_engine.dispose()
