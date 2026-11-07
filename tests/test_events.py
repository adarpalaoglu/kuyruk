"""Tests for live progress, `GET /jobs/{ticket}/events` (`app/events.py`).

No worker runs in these tests. Instead, `play` makes the job move one step
between the stream's looks at the database: before each look after the first
one, the next step changes the job (start it, finish a text, cancel it...),
using the same state rule functions as the worker and the cancel route. So
every test plays out a job exactly, and fast: the stream looks every 0.01 s.

A stream reply is plain text: blocks separated by an empty line, each with an
`event:` line and a `data:` line of JSON, or a comment line starting with `:`.
`parse` turns it into a list such as [("state", {"state": "queued"}), ...].
"""

import json
import time
import uuid
from datetime import timedelta

import anyio
import jwt
import pytest
from sqlalchemy import select, update
from sqlmodel import Session, SQLModel, create_engine

from app import events
from app.db import get_engine, get_session
from app.events import recovered_event
from app.main import app
from app.models import ItemState, Job, JobItem, JobState, utc_now
from app.security import read_access_token
from app.settings import settings
from app.states import change_item_state, change_job_state
from app.worker import take_over_stuck_job

QUEUED, RUNNING = JobState.QUEUED, JobState.RUNNING
DONE, FAILED, CANCELLED = JobState.DONE, JobState.FAILED, JobState.CANCELLED


@pytest.fixture(autouse=True)
def fast_stream(monkeypatch):
    """Look every 0.01 s; keep-alive only in the test that asks for it."""
    monkeypatch.setattr(settings, "events_poll_seconds", 0.01)
    monkeypatch.setattr(settings, "events_keepalive_seconds", 60.0)


def send_job(client, headers, count=3):
    """Send a `normalise` job with `count` texts and return its ticket."""
    body = {"task": "normalise", "texts": [f"Metin {n}" for n in range(count)]}
    return client.post("/jobs", json=body, headers=headers).json()["id"]


# --- Steps: each changes the job the way the worker or a cancel would. ---


def start(session, job_id):
    """The worker takes the job: queued -> running."""
    assert change_job_state(session, job_id, QUEUED, RUNNING, started_at=utc_now())


def finish(state):
    """A step that ends a running job in `state` (done, failed or cancelled)."""

    def step(session, job_id):
        assert change_job_state(session, job_id, RUNNING, state, finished_at=utc_now())

    return step


def text_ok(session, job_id):
    """One more text succeeded."""
    session.execute(
        update(Job).where(Job.id == job_id).values(succeeded=Job.succeeded + 1)
    )
    session.commit()


def text_failed(session, job_id):
    """One more text failed."""
    session.execute(update(Job).where(Job.id == job_id).values(failed=Job.failed + 1))
    session.commit()


def nothing(session, job_id):
    """Nothing changes before this look."""


def set_up(engine, ticket, *steps):
    """Apply steps to the job now, before any stream is opened."""
    with Session(engine) as session:
        for step in steps:
            step(session, uuid.UUID(ticket))


def play(monkeypatch, engine, steps):
    """Before each look after the first, apply the next step; return the looks.

    The returned list grows by one for every look the stream takes, so a test
    can also check when the stream stopped looking.
    """
    real_read = events.read_job_snapshot
    remaining = list(steps)
    looks = []

    def read_after_next_step(read_engine, job_id):
        """Apply the next step (if this isn't the first look), then really read."""
        if looks and remaining:
            with Session(engine) as session:
                remaining.pop(0)(session, job_id)
        looks.append(job_id)
        return real_read(read_engine, job_id)

    monkeypatch.setattr(events, "read_job_snapshot", read_after_next_step)
    return looks


def parse(body):
    """Turn a stream's text into [(name, data), ...]; comments are ("comment", text)."""
    parsed = []
    for block in body.strip().split("\n\n"):
        name = data = None
        for line in block.split("\n"):
            if line.startswith(":"):
                parsed.append(("comment", line[1:].strip()))
            elif line.startswith("event: "):
                name = line.removeprefix("event: ")
            elif line.startswith("data: "):
                data = json.loads(line.removeprefix("data: "))
        if name is not None:
            parsed.append((name, data))
    return parsed


def stream(client, headers, ticket):
    """Open the stream, read it to its end, and return the reply."""
    return client.get(f"/jobs/{ticket}/events", headers=headers)


def progress(done_ok, failed, total):
    """The expected `progress` event."""
    return (
        "progress",
        {
            "done": done_ok + failed,
            "total": total,
            "succeeded": done_ok,
            "failed": failed,
        },
    )


# --- Event order ---


def test_job_that_runs_to_done_gives_every_event_in_order(
    client, log_in_as, engine, monkeypatch
):
    headers = log_in_as("ayse")
    ticket = send_job(client, headers, count=3)
    play(monkeypatch, engine, [start, text_ok, text_failed, text_ok, finish(DONE)])
    reply = stream(client, headers, ticket)
    assert reply.status_code == 200
    assert reply.headers["content-type"].startswith("text/event-stream")
    assert parse(reply.text) == [
        ("state", {"state": "queued"}),
        progress(0, 0, 3),
        ("state", {"state": "running"}),
        progress(1, 0, 3),
        progress(1, 1, 3),
        progress(2, 1, 3),
        ("state", {"state": "done"}),
        ("end", {"state": "done"}),
    ]


def test_progress_comes_before_state_when_both_change_in_one_look(
    client, log_in_as, engine, monkeypatch
):
    """The last text and "done" seen together: progress, then state, then end."""
    headers = log_in_as("ayse")
    ticket = send_job(client, headers, count=1)
    set_up(engine, ticket, start)

    def last_text_and_done(session, job_id):
        text_ok(session, job_id)
        finish(DONE)(session, job_id)

    play(monkeypatch, engine, [last_text_and_done])
    assert parse(stream(client, headers, ticket).text) == [
        ("state", {"state": "running"}),
        progress(0, 0, 1),
        progress(1, 0, 1),
        ("state", {"state": "done"}),
        ("end", {"state": "done"}),
    ]


def test_joining_halfway_starts_with_a_snapshot(client, log_in_as, engine, monkeypatch):
    headers = log_in_as("ayse")
    ticket = send_job(client, headers, count=5)
    set_up(engine, ticket, start, text_ok, text_failed)
    play(monkeypatch, engine, [text_ok, text_ok, text_ok, finish(DONE)])
    parsed = parse(stream(client, headers, ticket).text)
    assert parsed[:2] == [("state", {"state": "running"}), progress(1, 1, 5)]
    assert parsed[-1] == ("end", {"state": "done"})


def test_no_change_sends_nothing(client, log_in_as, engine, monkeypatch):
    """Looks that find nothing new send no events (and no keep-alive yet)."""
    headers = log_in_as("ayse")
    ticket = send_job(client, headers, count=1)
    play(monkeypatch, engine, [nothing, nothing, start, nothing, finish(CANCELLED)])
    assert parse(stream(client, headers, ticket).text) == [
        ("state", {"state": "queued"}),
        progress(0, 0, 1),
        ("state", {"state": "running"}),
        ("state", {"state": "cancelled"}),
        ("end", {"state": "cancelled"}),
    ]


@pytest.mark.parametrize("final", [DONE, FAILED, CANCELLED])
def test_finished_job_gets_snapshot_and_end_at_once(
    client, log_in_as, engine, monkeypatch, final
):
    headers = log_in_as("ayse")
    ticket = send_job(client, headers, count=2)
    set_up(engine, ticket, start, text_ok, finish(final))
    looks = play(monkeypatch, engine, [])
    assert parse(stream(client, headers, ticket).text) == [
        ("state", {"state": final.value}),
        progress(1, 0, 2),
        ("end", {"state": final.value}),
    ]
    assert len(looks) == 1  # one look, no waiting


def test_cancel_during_the_stream_ends_it(client, log_in_as, engine, monkeypatch):
    """Cancelled through the real cancel route, while the stream is open."""
    headers = log_in_as("ayse")
    ticket = send_job(client, headers, count=4)
    set_up(engine, ticket, start, text_ok)

    def user_cancels(session, job_id):
        # The same conditional change the cancel route makes.
        assert change_job_state(
            session, job_id, RUNNING, CANCELLED, finished_at=utc_now()
        )

    play(monkeypatch, engine, [nothing, user_cancels])
    parsed = parse(stream(client, headers, ticket).text)
    assert parsed[-2:] == [
        ("state", {"state": "cancelled"}),
        ("end", {"state": "cancelled"}),
    ]
    # The stream really is over: the cancel route now sees a finished job.
    assert client.post(f"/jobs/{ticket}/cancel", headers=headers).status_code == 409


def test_queued_job_cancelled_before_start(client, log_in_as, engine, monkeypatch):
    headers = log_in_as("ayse")
    ticket = send_job(client, headers, count=2)

    def cancel_queued(session, job_id):
        assert change_job_state(session, job_id, QUEUED, CANCELLED)

    play(monkeypatch, engine, [cancel_queued])
    assert parse(stream(client, headers, ticket).text)[-2:] == [
        ("state", {"state": "cancelled"}),
        ("end", {"state": "cancelled"}),
    ]


def test_stream_stops_quietly_if_the_job_disappears(
    client, log_in_as, engine, monkeypatch
):
    """Kuyruk never deletes jobs, but a stream must not crash if one vanishes."""
    headers = log_in_as("ayse")
    ticket = send_job(client, headers, count=1)
    real_read = events.read_job_snapshot
    looks = []

    def gone_on_second_look(read_engine, job_id):
        looks.append(job_id)
        return real_read(read_engine, job_id) if len(looks) == 1 else None

    monkeypatch.setattr(events, "read_job_snapshot", gone_on_second_look)
    reply = stream(client, headers, ticket)
    assert reply.status_code == 200
    assert parse(reply.text) == [("state", {"state": "queued"}), progress(0, 0, 1)]


def test_no_events_if_the_job_is_gone_at_the_first_look(client, log_in_as, monkeypatch):
    headers = log_in_as("ayse")
    ticket = send_job(client, headers, count=1)
    monkeypatch.setattr(events, "read_job_snapshot", lambda engine, job_id: None)
    assert parse(stream(client, headers, ticket).text) == []


# --- Keep-alive ---


def test_keep_alive_comment_when_nothing_changes(
    client, log_in_as, engine, monkeypatch
):
    monkeypatch.setattr(settings, "events_keepalive_seconds", 0.05)
    headers = log_in_as("ayse")
    ticket = send_job(client, headers, count=1)
    set_up(engine, ticket, start)
    # About 0.2 s of quiet looks, then the job finishes.
    play(monkeypatch, engine, [nothing] * 20 + [text_ok, finish(DONE)])
    parsed = parse(stream(client, headers, ticket).text)
    names = [name for name, _ in parsed]
    assert ("comment", "keep-alive") in parsed
    # Keep-alives come only in the quiet part: after the snapshot, before the
    # job moves again, and never as a replacement for an event.
    first, last = names.index("comment"), len(names) - 1 - names[::-1].index("comment")
    assert names[:2] == ["state", "progress"]
    assert first >= 2
    assert names[last + 1 :] == ["progress", "state", "end"]


# --- Login and the 404 rule, checked once when the stream opens ---


def test_stream_keeps_going_after_the_pass_expires(
    client, log_in_as, engine, monkeypatch
):
    monkeypatch.setattr(settings, "access_token_minutes", 1 / 60)  # one second
    headers = log_in_as("ayse")
    ticket = send_job(client, headers, count=1)
    token = headers["Authorization"].removeprefix("Bearer ")

    def wait_until_the_pass_expires(session, job_id):
        # Steps run on the stream's helper thread, so this wait freezes
        # nothing else.
        time.sleep(1.1)
        with pytest.raises(jwt.ExpiredSignatureError):
            read_access_token(token)

    play(
        monkeypatch, engine, [start, wait_until_the_pass_expires, text_ok, finish(DONE)]
    )
    parsed = parse(stream(client, headers, ticket).text)
    assert parsed[-1] == ("end", {"state": "done"})
    assert progress(1, 0, 1) in parsed  # events after the pass expired arrived
    # Anything new with that pass is refused, including a new stream.
    assert stream(client, headers, ticket).status_code == 401


def assert_error_reply(reply, status_code, code):
    """A normal error reply (JSON, our shape, request ID), not a stream."""
    assert reply.status_code == status_code
    assert reply.headers["content-type"] == "application/json"
    assert reply.json()["error"]["code"] == code
    assert reply.headers["x-request-id"]


def test_no_login_is_401_before_any_stream(client, log_in_as):
    ticket = send_job(client, log_in_as("ayse"))
    assert_error_reply(client.get(f"/jobs/{ticket}/events"), 401, "not_authenticated")
    broken = {"Authorization": "Bearer not-a-real-pass"}
    assert_error_reply(stream(client, broken, ticket), 401, "not_authenticated")


def test_other_users_job_is_404_before_any_stream(client, log_in_as, monkeypatch):
    ticket = send_job(client, log_in_as("ayse"))
    looks = play(monkeypatch, None, [])
    assert_error_reply(stream(client, log_in_as("mehmet"), ticket), 404, "not_found")
    assert looks == []  # the stream never started


@pytest.mark.parametrize("ticket", [str(uuid.uuid4()), "not-a-ticket"])
def test_missing_or_malformed_ticket_is_404(client, log_in_as, ticket):
    assert_error_reply(stream(client, log_in_as("ayse"), ticket), 404, "not_found")


# --- Database connections ---


def test_stream_does_not_hold_the_login_checks_connection(
    log_in_as, client, monkeypatch, tmp_path
):
    """With a database that allows one connection, the stream still works.

    If the session used for the login check stayed open during the stream, it
    would keep that one connection, and the stream's own looks would wait for
    it until they time out. (On Postgres the pool holds about 15; one held per
    open stream would soon leave other requests waiting.)
    """
    one_connection = create_engine(
        f"sqlite:///{tmp_path / 'one.db'}",
        connect_args={"check_same_thread": False},
        pool_size=1,
        max_overflow=0,
        pool_timeout=0.5,
    )
    SQLModel.metadata.create_all(one_connection)

    def session_on_one_connection():
        with Session(one_connection) as session:
            yield session

    app.dependency_overrides[get_session] = session_on_one_connection
    app.dependency_overrides[get_engine] = lambda: one_connection
    headers = log_in_as("ayse")
    ticket = send_job(client, headers, count=1)
    play(monkeypatch, one_connection, [start, text_ok, finish(DONE)])
    parsed = parse(stream(client, headers, ticket).text)
    assert parsed[-1] == ("end", {"state": "done"})
    one_connection.dispose()


# --- Disconnect ---


@pytest.mark.anyio
async def test_disconnect_stops_the_stream_at_once(
    client, log_in_as, engine, monkeypatch
):
    """The client hangs up after the first event; the stream stops looking.

    The app is called directly (no test client), with a pretend client that
    says "I've gone" as soon as it receives something. The job stays queued,
    so the stream would go on forever if the hang-up weren't noticed. The
    request goes through Kuyruk's real middleware.
    """
    headers = log_in_as("ayse")
    ticket = send_job(client, headers, count=1)
    looks = play(monkeypatch, engine, [nothing] * 10_000)
    got_something = anyio.Event()
    request_read = False

    async def receive():
        """First the (empty) request, then, once an event has arrived, a hang-up."""
        nonlocal request_read
        if not request_read:
            request_read = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await got_something.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        """Notice the first piece of the stream."""
        if message["type"] == "http.response.body" and message.get("body"):
            got_something.set()

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": f"/jobs/{ticket}/events",
        "raw_path": f"/jobs/{ticket}/events".encode(),
        "root_path": "",
        "query_string": b"",
        "headers": [(b"authorization", headers["Authorization"].encode())],
        "client": ("testclient", 50000),
        "server": ("testserver", 80),
        "state": {},
    }
    with anyio.fail_after(2):
        await app(scope, receive, send)
    looks_when_stopped = len(looks)
    await anyio.sleep(0.1)  # ten more looks' worth of time
    assert len(looks) == looks_when_stopped


# --- The event format itself (spec 6.1) ---


def test_event_text_matches_spec_exactly(client, log_in_as, engine, monkeypatch):
    """The exact lines on the wire, keys in the spec's order."""
    headers = log_in_as("ayse")
    ticket = send_job(client, headers, count=2)
    set_up(engine, ticket, start, text_ok, text_failed, finish(DONE))
    assert stream(client, headers, ticket).text == (
        "event: state\n"
        'data: {"state": "done"}\n'
        "\n"
        "event: progress\n"
        'data: {"done": 2, "total": 2, "succeeded": 1, "failed": 1}\n'
        "\n"
        "event: end\n"
        'data: {"state": "done"}\n'
        "\n"
    )


def test_recovered_event_format():
    """Exactly spec 6.1: `{"from_position": 212}`."""
    event = recovered_event(212)
    assert event.event == "recovered"
    assert event.data == {"from_position": 212}


def test_stream_settings_have_spec_defaults():
    """Look twice a second (D63); keep-alive after 15 quiet seconds (D66)."""
    fresh = type(settings)()
    assert fresh.events_poll_seconds == 0.5
    assert fresh.events_keepalive_seconds == 15.0


def test_docs_show_the_stream_as_an_event_stream(client):
    route = client.get("/openapi.json").json()["paths"]["/jobs/{ticket}/events"]
    assert "text/event-stream" in route["get"]["responses"]["200"]["content"]


def test_reading_a_missing_job_gives_none(engine):
    assert events.read_job_snapshot(engine, uuid.uuid4()) is None


def test_get_engine_gives_the_apps_engine():
    """Outside tests, the stream's short sessions use the app's real engine."""
    from app import db

    assert db.get_engine() is db.engine


# --- Recovery after a crash (D73) ---


def first_text_ok(session, job_id):
    """The worker finishes text 1 (the text row and the job's count)."""
    item_id = session.scalar(
        select(JobItem.id).where(JobItem.job_id == job_id, JobItem.position == 1)
    )
    change_item_state(session, item_id, ItemState.WAITING, ItemState.OK, result="x")
    text_ok(session, job_id)


def worker_dies(session, job_id):
    """The worker stops beating: its last heartbeat is a minute old."""
    session.execute(
        update(Job)
        .where(Job.id == job_id)
        .values(
            claimed_by="dead-worker", last_active_at=utc_now() - timedelta(seconds=60)
        )
    )
    session.commit()


def taken_over(session, job_id):
    """Another worker finds the job stuck and takes it over (the real code)."""
    assert take_over_stuck_job(session, "w2", stale_after_seconds=30).id == job_id


def taken_over_and_finished(session, job_id):
    """In one look: taken over, the last text done, and the job done."""
    taken_over(session, job_id)
    text_ok(session, job_id)
    finish(DONE)(session, job_id)


def crashed_too_often(session, job_id):
    """Another worker finds it stuck once too often and fails it."""
    assert take_over_stuck_job(session, "w2", max_crash_count=0) is None


def test_takeover_sends_recovered_with_the_first_waiting_position(
    client, log_in_as, engine, monkeypatch
):
    """Text 1 done, crash, takeover: `recovered` from position 2, then on to done."""
    headers = log_in_as("ayse")
    ticket = send_job(client, headers, count=3)
    play(
        monkeypatch,
        engine,
        [start, first_text_ok, worker_dies, taken_over, text_ok, text_ok, finish(DONE)],
    )
    assert parse(stream(client, headers, ticket).text) == [
        ("state", {"state": "queued"}),
        progress(0, 0, 3),
        ("state", {"state": "running"}),
        progress(1, 0, 3),
        ("recovered", {"from_position": 2}),
        progress(2, 0, 3),
        progress(3, 0, 3),
        ("state", {"state": "done"}),
        ("end", {"state": "done"}),
    ]


def test_recovered_comes_before_progress_and_state_in_one_look(
    client, log_in_as, engine, monkeypatch
):
    """Takeover, a text and "done" in one look: recovered, progress, state."""
    headers = log_in_as("ayse")
    ticket = send_job(client, headers, count=1)
    set_up(engine, ticket, start, worker_dies)
    play(monkeypatch, engine, [taken_over_and_finished])
    assert parse(stream(client, headers, ticket).text) == [
        ("state", {"state": "running"}),
        progress(0, 0, 1),
        ("recovered", {"from_position": 1}),
        progress(1, 0, 1),
        ("state", {"state": "done"}),
        ("end", {"state": "done"}),
    ]


def test_nothing_waiting_gives_a_null_position(client, log_in_as, engine, monkeypatch):
    """Crash after the last text, before "done": `from_position` is null."""
    headers = log_in_as("ayse")
    ticket = send_job(client, headers, count=1)
    set_up(engine, ticket, start, first_text_ok, worker_dies)
    play(monkeypatch, engine, [taken_over, finish(DONE)])
    assert parse(stream(client, headers, ticket).text)[2:] == [
        ("recovered", {"from_position": None}),
        ("state", {"state": "done"}),
        ("end", {"state": "done"}),
    ]


def test_no_recovered_when_the_job_fails_for_crashing(
    client, log_in_as, engine, monkeypatch
):
    """Failed instead of resumed: just the failed state and the end."""
    headers = log_in_as("ayse")
    ticket = send_job(client, headers, count=2)
    set_up(engine, ticket, start, worker_dies)
    play(monkeypatch, engine, [crashed_too_often])
    assert parse(stream(client, headers, ticket).text) == [
        ("state", {"state": "running"}),
        progress(0, 0, 2),
        ("state", {"state": "failed"}),
        ("end", {"state": "failed"}),
    ]


def test_joining_after_a_recovery_sends_no_recovered(
    client, log_in_as, engine, monkeypatch
):
    """The snapshot is only state + progress (D64), even after a takeover."""
    headers = log_in_as("ayse")
    ticket = send_job(client, headers, count=2)
    set_up(engine, ticket, start, worker_dies, taken_over)
    play(monkeypatch, engine, [finish(DONE)])
    names = [name for name, _ in parse(stream(client, headers, ticket).text)]
    assert "recovered" not in names
