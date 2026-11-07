"""The connection between Kuyruk and its database.

Three things live here:

- the *engine*: the object that knows the database's address and keeps a small
  pool of open connections to it, so each request doesn't have to reconnect;
- `create_db_and_tables`, which makes any missing tables when Kuyruk starts;
- `get_session`, which gives each web request its own *session* (one
  conversation with the database: reads, writes, and saving them together);
- `get_engine`, for code that opens its own short sessions, such as a live
  progress stream that looks at the database again and again.
"""

from collections.abc import Iterator

from sqlalchemy import Engine, event
from sqlmodel import Session, SQLModel, create_engine

# Importing the models registers the tables with SQLModel; without this import
# `create_all` below would not know which tables to make.
from app import models  # noqa: F401
from app.settings import settings


def enforce_sqlite_foreign_keys(engine: Engine) -> None:
    """Make a SQLite engine refuse rows that point at something missing.

    A *foreign key* is a column that must hold the id of an existing row in
    another table, e.g. a job's `owner_id` must be a real user. Postgres always
    checks this; SQLite only does when told to, once per connection. Without
    this, a test on SQLite could pass for code that Postgres would refuse.
    """

    # SQLAlchemy calls this function every time it opens a new connection.
    @event.listens_for(engine, "connect")
    def switch_on_foreign_keys(dbapi_connection, connection_record) -> None:
        """Send SQLite the command that turns foreign-key checking on."""
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


def make_engine(database_url: str) -> Engine:
    """Create an engine for the database at `database_url`.

    It exists as a function so tests can make an engine for their own
    throwaway database instead of the real one.
    """
    connect_args = {}
    if database_url.startswith("sqlite"):
        # SQLite normally refuses to share a connection between threads. The
        # web server may handle one request across threads, which is safe here
        # because each request has its own session, so we switch that check off.
        connect_args = {"check_same_thread": False}
    new_engine = create_engine(database_url, connect_args=connect_args)
    if database_url.startswith("sqlite"):
        enforce_sqlite_foreign_keys(new_engine)
    return new_engine


# The one engine the running app uses, pointed at DATABASE_URL.
engine = make_engine(settings.database_url)


def create_db_and_tables() -> None:
    """Create every table that doesn't exist yet; existing tables are left as is.

    Kuyruk uses this instead of a migration tool (a tool that changes existing
    tables step by step) because the finished project is frozen and its tables
    won't change after release.
    """
    SQLModel.metadata.create_all(engine)


def get_session() -> Iterator[Session]:
    """Give one web request one database session, and always close it after.

    FastAPI calls this as a *dependency* (something a route asks for and
    FastAPI supplies). Because it uses `yield`, FastAPI runs the code before
    `yield` when the request starts and the clean-up after it when the request
    ends. The `with` block closes the session even if the request fails, so
    connections are never left open.
    """
    with Session(engine) as session:
        yield session


def get_engine() -> Engine:
    """Give a route the engine itself, so it can open its own short sessions.

    A live progress stream (`app/events.py`) stays open for minutes, so it
    can't keep one session for all that time: the session would hold one of
    the pool's few connections the whole while. Instead it opens a new session
    for each quick look and closes it at once. Being a dependency, tests can
    swap it for their own test database, just like `get_session`.
    """
    return engine
