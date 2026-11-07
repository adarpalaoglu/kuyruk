"""Live progress for one job: `GET /jobs/{ticket}/events` (spec 6.1, D63-D67).

The reply is a *Server-Sent Events* stream (SSE): an ordinary web request
whose reply stays open while Kuyruk writes short messages down it as the job
moves on. Each message has a name and a JSON body, for example:

    event: progress
    data: {"done": 12, "total": 20, "succeeded": 12, "failed": 0}

The event format is HAND-DESIGNED by Adar (spec 6.1, D64):

- `state`     `{"state": "running"}`, on every state change;
- `progress`  `{"done": .., "total": .., "succeeded": .., "failed": ..}`,
  after texts finish (at most once per look; it may cover several texts);
- `recovered` `{"from_position": 212}`, when a job resumes after a crash
  (defined here, sent from Slice 8);
- `end`       `{"state": "done"}`, the final state; then the stream closes.

How a stream runs:

1. `open_job_events` runs *before* the stream starts: the login check (401)
   and "is this your job?" (404) give normal error replies. They are checked
   only here, so the stream keeps going even if the login pass expires (D65).
2. `watch_job` sends a snapshot (`state` + `progress`), then looks at the job
   in the database every `EVENTS_POLL_SECONDS` and sends only what changed
   (D63). After `EVENTS_KEEPALIVE_SECONDS` without a message it sends a
   comment line (`: keep-alive`), which clients ignore but which stops
   networks from closing a quiet connection (D66). When the job reaches done,
   failed or cancelled it sends `end`, and the stream closes.
3. FastAPI's built-in SSE support (`EventSourceResponse`) turns each event
   into the text above, and stops the stream at once when the client hangs up.

The stream is written so that many open streams can't slow Kuyruk down: it
is `async`, so while it waits between looks it holds no thread; each look is
a short read on a helper thread with its own short database session; and the
login check's session is closed before streaming starts.
"""

import uuid
from collections.abc import AsyncIterator
from typing import Annotated, NamedTuple

import anyio
from fastapi import APIRouter, Depends
from fastapi.sse import EventSourceResponse, ServerSentEvent
from sqlalchemy import Engine
from sqlmodel import Session, select

from app.auth import SessionDep
from app.db import get_engine
from app.jobs import CurrentUser, get_own_job
from app.models import Job, JobState
from app.settings import settings
from app.states import JOB_TRANSITIONS

router = APIRouter(tags=["jobs"])


class JobSnapshot(NamedTuple):
    """What a stream needs to know about its job at one moment."""

    state: JobState
    total: int
    succeeded: int
    failed: int


def state_event(state: JobState) -> ServerSentEvent:
    """The `state` event: `{"state": "running"}`."""
    return ServerSentEvent(event="state", data={"state": state.value})


def progress_event(snapshot: JobSnapshot) -> ServerSentEvent:
    """The `progress` event: how many texts are finished, out of how many.

    `done` is every finished text, whether it succeeded or failed.
    """
    return ServerSentEvent(
        event="progress",
        data={
            "done": snapshot.succeeded + snapshot.failed,
            "total": snapshot.total,
            "succeeded": snapshot.succeeded,
            "failed": snapshot.failed,
        },
    )


def recovered_event(from_position: int) -> ServerSentEvent:
    """The `recovered` event: the job resumed after a crash, at this text.

    Part of the format now; crash recovery (Slice 8) is what sends it.
    """
    return ServerSentEvent(event="recovered", data={"from_position": from_position})


def end_event(state: JobState) -> ServerSentEvent:
    """The `end` event: the job's final state. Nothing follows it."""
    return ServerSentEvent(event="end", data={"state": state.value})


# A *comment* line (`: keep-alive`): clients ignore it, but it shows networks
# and proxies that the connection is still in use (D66).
KEEP_ALIVE = ServerSentEvent(comment="keep-alive")


def is_final(state: JobState) -> bool:
    """True for done, failed and cancelled: states nothing can follow."""
    return state not in JOB_TRANSITIONS


def read_job_snapshot(engine: Engine, job_id: uuid.UUID) -> JobSnapshot | None:
    """Read the job's state and counts with a short session of its own.

    Only four columns are read, and the session (with its database
    connection) is closed straight after, so an open stream never holds a
    connection between looks. Returns None if the job is gone. (Kuyruk never
    deletes jobs, but a stream must not crash if one disappears.)
    """
    with Session(engine) as session:
        row = session.exec(
            select(Job.state, Job.total, Job.succeeded, Job.failed).where(
                Job.id == job_id
            )
        ).first()
    if row is None:
        return None
    state, total, succeeded, failed = row
    return JobSnapshot(JobState(state), total, succeeded, failed)


async def look(engine: Engine, job_id: uuid.UUID) -> JobSnapshot | None:
    """Run `read_job_snapshot` on a helper thread and wait for it.

    The database library works in the ordinary, blocking way: while a query
    runs, the code that sent it waits. Run directly here, that wait would
    freeze every other request on the server (they all share one *event
    loop*, the part that switches between requests). On a helper thread it
    only takes that thread, for a few milliseconds, and the event loop carries
    on with other requests meanwhile.
    """
    return await anyio.to_thread.run_sync(read_job_snapshot, engine, job_id)


async def watch_job(
    engine: Engine, job_id: uuid.UUID
) -> AsyncIterator[ServerSentEvent]:
    """Produce the job's events, from the snapshot to `end` (spec 6.1, D64).

    An *async generator*: it hands out one event at a time with `yield`, and
    waits with `await` in between without holding a thread. If the client
    hangs up, FastAPI stops it at whichever `await` it is waiting at.
    """
    last = await look(engine, job_id)
    if last is None:
        return
    # The snapshot: someone joining halfway sees where the job is at once.
    yield state_event(last.state)
    yield progress_event(last)
    quiet_since = anyio.current_time()
    while not is_final(last.state):
        await anyio.sleep(settings.events_poll_seconds)
        now = await look(engine, job_id)
        if now is None:
            return
        sent_something = False
        # Progress first, then state: the texts counted here finished before
        # the job changed state (e.g. the last text, then "done").
        if (now.succeeded, now.failed, now.total) != (
            last.succeeded,
            last.failed,
            last.total,
        ):
            yield progress_event(now)
            sent_something = True
        if now.state != last.state:
            yield state_event(now.state)
            sent_something = True
        last = now
        if sent_something:
            quiet_since = anyio.current_time()
        elif anyio.current_time() - quiet_since >= settings.events_keepalive_seconds:
            yield KEEP_ALIVE
            quiet_since = anyio.current_time()
    yield end_event(last.state)


def open_job_events(ticket: str, session: SessionDep, user: CurrentUser) -> uuid.UUID:
    """Check login and ownership once, before the stream starts; return the id.

    A *dependency*: FastAPI runs it before the route, so a missing or expired
    pass (401) and someone else's or a missing ticket (404) get normal error
    replies, not a broken stream. After this, the pass is never checked again
    (D65).

    The session is closed here, so the login check's database connection goes
    back to the pool now instead of being held until the stream ends. Postgres
    allows only a few connections; with one held per open stream, a handful of
    viewers would make every other request wait.
    """
    job = get_own_job(session, user, ticket)
    session.close()
    return job.id


@router.get("/jobs/{ticket}/events", response_class=EventSourceResponse)
async def job_events(
    job_id: Annotated[uuid.UUID, Depends(open_job_events)],
    engine: Annotated[Engine, Depends(get_engine)],
) -> AsyncIterator[ServerSentEvent]:
    """Live progress of one of your jobs, as Server-Sent Events (spec 6.1).

    Read it with `curl -N` or a `fetch`-based stream reader that sends the
    Authorization header. The login pass never goes in the address (D67):
    addresses end up in logs and browser history.
    """
    async for event in watch_job(engine, job_id):
        yield event
