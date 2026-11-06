"""Kuyruk's data model: the three database tables and their allowed values.

A *table* is like a spreadsheet: each *row* is one record and each *column* one
field. We describe the tables with SQLModel, a library where one Python class
is one table and each attribute is one column. The tables are:

- `users`: the people who can log in.
- `jobs`: one row per *job* (one submission of texts to process).
- `job_items`: one row per *text* inside a job, with its result.

The fields follow `docs/spec.md` section 5; the allowed states follow section 4.
Rules about which state may follow which are added in the worker slices, not
here.
"""

import uuid
from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import DateTime, Dialect, Enum, TypeDecorator, UniqueConstraint
from sqlmodel import Field, SQLModel


def utc_now() -> datetime:
    """Return the current time in UTC (world time, no local time zone).

    Used as the default for "created at" columns so every timestamp is in the
    same time zone, which the daily budget (counted per UTC day) relies on.
    """
    return datetime.now(UTC)


class UTCDateTime(TypeDecorator):
    """A date-and-time column that always gives times back labelled as UTC.

    Databases differ: Postgres keeps the time-zone label, SQLite drops it. A
    time without a label can't be compared with one that has it (Python raises
    an error), which would break later checks such as "is this job's heartbeat
    older than 30 seconds?". This column stores every time in UTC and puts the
    UTC label back when reading, on both databases.
    """

    # The underlying database type; timezone=True gives Postgres the
    # time-zone-aware column type ("timestamptz").
    impl = DateTime(timezone=True)
    # Tells SQLAlchemy this type is safe to remember (cache) between queries.
    cache_ok = True

    def process_bind_param(
        self, value: datetime | None, dialect: Dialect
    ) -> datetime | None:
        """Before saving: refuse unlabelled times and convert the rest to UTC."""
        if value is None:
            return None
        if value.tzinfo is None:
            # An unlabelled time is ambiguous (local? UTC?), so it's a bug.
            raise ValueError("Times must carry a time zone; use utc_now().")
        return value.astimezone(UTC)

    def process_result_value(
        self, value: datetime | None, dialect: Dialect
    ) -> datetime | None:
        """After reading: label the time as UTC (SQLite gives it back unlabelled)."""
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


class Task(StrEnum):
    """The kinds of work a job can ask for (spec section 2).

    A *StrEnum* is a fixed list of allowed words: any other word is refused,
    and the database stores the plain word, e.g. "llm".
    """

    NORMALISE = "normalise"
    COUNT_TOKENS = "count_tokens"
    LLM = "llm"


class JobState(StrEnum):
    """Where a whole job is in its life (spec section 4).

    queued -> running -> done | failed | cancelled.
    """

    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ItemState(StrEnum):
    """Where one text inside a job is (spec section 4): waiting -> ok | failed."""

    WAITING = "waiting"
    OK = "ok"
    FAILED = "failed"


def plain_word_column(choices: type[StrEnum]) -> Enum:
    """Describe a database column that holds one word from a fixed list.

    Without this, the database would store the Python names ("QUEUED") and
    Postgres would use its own special list type. Here the column holds the
    plain word ("queued"), and a CHECK constraint (a rule the database itself
    enforces) refuses any word that isn't on the list.
    """
    return Enum(
        choices,
        native_enum=False,
        create_constraint=True,
        values_callable=lambda members: [member.value for member in members],
        length=max(len(member.value) for member in choices),
    )


class User(SQLModel, table=True):
    """One person who can log in and send jobs.

    The password is never stored; only `hashed_password`, a one-way scrambled
    form of it that can check a password but not reveal it. This field must
    never appear in any reply (Slice 2 adds the safe public reply models).
    """

    # Set the table name by hand: in Postgres "user" is a reserved word, so a
    # table called "user" is confusing to query by hand.
    __tablename__ = "users"

    # The database fills in the number when the row is first saved, so it
    # starts empty (None) in Python.
    id: int | None = Field(default=None, primary_key=True)
    # unique=True makes the database itself refuse a second user with the same
    # name, even if two sign-ups arrive at the same moment.
    username: str = Field(unique=True, index=True)
    hashed_password: str
    created_at: datetime = Field(default_factory=utc_now, sa_type=UTCDateTime)


class Job(SQLModel, table=True):
    """One submission: a list of texts and the task to run on each of them.

    The counts (`total`, `succeeded`, `failed`) summarise its texts. The worker
    fields (`claimed_by`, `last_active_at`, `crash_count`) let a worker take a
    job, show it is still alive, and let another worker recover it after a
    crash (spec section 7).
    """

    __tablename__ = "jobs"
    # The same idempotency key may be used only once per owner. (An idempotency
    # key is a label the client sends so that sending the same job twice, e.g.
    # after a double-click, creates it only once.) Two different users may use
    # the same key. Jobs without a key are not affected: databases treat empty
    # (NULL) values as never equal to each other.
    __table_args__ = (
        UniqueConstraint("owner_id", "idempotency_key", name="uq_jobs_owner_key"),
    )

    # A UUID (a long random identifier such as 1b4e28ba-2fa1-...) rather than
    # 1, 2, 3, so nobody can guess other people's job ids. It works as the
    # job's "ticket number".
    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    # A foreign key: this column must hold the id of an existing user.
    owner_id: int = Field(foreign_key="users.id", index=True)
    task: Task = Field(sa_type=plain_word_column(Task))
    state: JobState = Field(
        default=JobState.QUEUED, sa_type=plain_word_column(JobState), index=True
    )
    total: int = 0
    succeeded: int = 0
    failed: int = 0
    tokens_needed: int = 0
    idempotency_key: str | None = None
    created_at: datetime = Field(default_factory=utc_now, sa_type=UTCDateTime)
    started_at: datetime | None = Field(default=None, sa_type=UTCDateTime)
    finished_at: datetime | None = Field(default=None, sa_type=UTCDateTime)
    # Updated on every heartbeat (a regular "I'm still working" signal) while
    # the job runs; if it gets too old, the job is stuck, not still working.
    last_active_at: datetime | None = Field(default=None, sa_type=UTCDateTime)
    crash_count: int = 0
    # Which worker process has taken this job; empty while nobody has.
    claimed_by: str | None = None
    error_message: str | None = None


class JobItem(SQLModel, table=True):
    """One text inside a job, together with its result.

    The result is kept in the same row as its text, so a text and what was
    produced from it can never get separated.
    """

    __tablename__ = "job_items"

    id: int | None = Field(default=None, primary_key=True)
    job_id: uuid.UUID = Field(foreign_key="jobs.id", index=True)
    # The text's place in the job, counted from 1, so results come back in the
    # order they were sent.
    position: int
    original_text: str
    result: str | None = None
    state: ItemState = Field(
        default=ItemState.WAITING, sa_type=plain_word_column(ItemState)
    )
    # How many times the AI has been asked about this text (up to 3).
    tries: int = 0
    error_message: str | None = None
