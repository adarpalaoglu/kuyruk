"""Tests for the shared error shape (`app/errors.py`) and the middleware
(`app/middleware.py`): request ID, timing log, crash safety and size limit.
"""

import asyncio
import json
import logging

import pytest
from fastapi import HTTPException

from app import jobs
from app.errors import ApiError, handle_http_error
from app.middleware import BodySizeLimitMiddleware
from app.settings import settings


def assert_error_shape(reply, status_code: int, code: str) -> None:
    """Check a reply is an error in exactly Kuyruk's shape, with a request ID."""
    assert reply.status_code == status_code
    body = reply.json()
    assert set(body) == {"error"}
    assert set(body["error"]) == {"code", "message"}
    assert body["error"]["code"] == code
    assert body["error"]["message"]
    assert reply.headers["X-Request-ID"]


@pytest.fixture
def crash_on_read(monkeypatch):
    """Make GET /jobs/{ticket} crash with an error holding "internal" details."""

    def crash(*args):
        raise RuntimeError("database password is hunter2, table jobs broke")

    monkeypatch.setattr(jobs, "get_own_job", crash)


# --- One error shape for every status ---------------------------------------


def test_401_shape(client):
    reply = client.get("/jobs")

    assert_error_shape(reply, 401, "not_authenticated")
    assert reply.headers["WWW-Authenticate"] == "Bearer"


def test_404_shape_for_an_address_that_does_not_exist(client):
    assert_error_shape(client.get("/no-such-page"), 404, "not_found")


def test_405_shape(client):
    assert_error_shape(client.delete("/jobs"), 405, "method_not_allowed")


def test_409_shape(client):
    account = {"username": "ayse", "password": "gizli1234"}
    client.post("/register", json=account)

    assert_error_shape(client.post("/register", json=account), 409, "username_taken")


def test_422_shape_names_the_field(client):
    reply = client.post("/register", json={"username": "ayse"})

    assert_error_shape(reply, 422, "validation_error")
    assert reply.json()["error"]["message"] == "password: Field required"


def test_422_for_a_missing_body_and_broken_json(client, log_in_as):
    headers = {**log_in_as("ayse"), "Content-Type": "application/json"}

    no_body = client.post("/jobs", headers=headers)
    broken = client.post("/jobs", content="{not json", headers=headers)

    assert_error_shape(no_body, 422, "validation_error")
    assert no_body.json()["error"]["message"] == "Request body: Field required"
    assert_error_shape(broken, 422, "validation_error")


def test_500_shape_hides_internal_details(client, log_in_as, crash_on_read):
    reply = client.get("/jobs/anything", headers=log_in_as("ayse"))

    assert_error_shape(reply, 500, "internal_error")
    assert reply.json()["error"]["message"] == "Something went wrong on our side."
    assert "hunter2" not in reply.text
    assert "RuntimeError" not in reply.text


def test_500_is_logged_with_its_request_id(client, log_in_as, crash_on_read, caplog):
    headers = {**log_in_as("ayse"), "X-Request-ID": "crash-123"}

    with caplog.at_level(logging.INFO, logger="kuyruk"):
        reply = client.get("/jobs/anything", headers=headers)

    assert reply.headers["X-Request-ID"] == "crash-123"
    crash_lines = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(crash_lines) == 1
    assert "request_id=crash-123" in crash_lines[0].getMessage()
    assert crash_lines[0].exc_info is not None  # full details kept for us


def test_unknown_status_falls_back_to_a_generic_code(client):
    # FastAPI itself never raises a 418, so call the handler the way FastAPI would.
    reply = asyncio.run(handle_http_error(None, HTTPException(418, "I'm a teapot")))

    assert json.loads(reply.body) == {
        "error": {"code": "error", "message": "I'm a teapot"}
    }


# --- Request ID ---------------------------------------------------------------


def test_every_reply_gets_a_new_request_id(client):
    first = client.get("/health")
    second = client.get("/health")

    assert len(first.headers["X-Request-ID"]) == 32
    assert first.headers["X-Request-ID"] != second.headers["X-Request-ID"]


def test_a_sensible_client_request_id_is_reused(client):
    reply = client.get("/health", headers={"X-Request-ID": "abc-123_x.y"})

    assert reply.headers["X-Request-ID"] == "abc-123_x.y"


def test_request_id_at_the_length_limit_is_reused(client):
    request_id = "r" * settings.request_id_max_length

    reply = client.get("/health", headers={"X-Request-ID": request_id})

    assert reply.headers["X-Request-ID"] == request_id


@pytest.mark.parametrize(
    "odd_id",
    [
        "r" * (settings.request_id_max_length + 1),  # too long
        "two words",  # space
        "fake%0Aline",  # percent sign (e.g. an encoded line break)
        "ışık".encode(),  # non-English letters, sent as raw UTF-8 bytes
        "",  # empty
    ],
)
def test_an_odd_client_request_id_is_replaced(client, odd_id):
    reply = client.get("/health", headers={"X-Request-ID": odd_id})

    assert reply.headers["X-Request-ID"] not in (odd_id, str(odd_id))
    assert len(reply.headers["X-Request-ID"]) == 32


def test_error_replies_also_carry_the_request_id(client):
    reply = client.get("/jobs", headers={"X-Request-ID": "err-1"})

    assert reply.status_code == 401
    assert reply.headers["X-Request-ID"] == "err-1"


def test_every_request_is_timed_in_the_log(client, caplog):
    with caplog.at_level(logging.INFO, logger="kuyruk"):
        client.get("/health", headers={"X-Request-ID": "time-1"})

    lines = [r.getMessage() for r in caplog.records if r.name == "kuyruk.requests"]
    assert len(lines) == 1
    line = lines[0]
    assert line.startswith("method=GET path=/health status=200 ms=")
    assert line.endswith("request_id=time-1")


def test_the_timing_log_holds_no_secrets(client, log_in_as, caplog):
    with caplog.at_level(logging.INFO, logger="kuyruk"):
        headers = log_in_as("ayse")
        client.get("/jobs", headers=headers)

    log_text = caplog.text
    assert "gizli1234" not in log_text
    assert headers["Authorization"].split()[1] not in log_text


# --- Request size limit -------------------------------------------------------


def job_body_of_exactly(size: int) -> bytes:
    """A valid job body that is exactly `size` bytes long."""
    empty = b'{"task": "normalise", "texts": ["x"]}'
    filler = b"x" * (size - len(empty))
    return empty.replace(b'"x"', b'"x' + filler + b'"')


@pytest.fixture
def small_limit(monkeypatch) -> int:
    """Lower the size limit to 200 bytes, so the tests don't send 25 MB."""
    monkeypatch.setattr(settings, "max_request_bytes", 200)
    return 200


def test_body_at_the_size_limit_is_accepted(client, log_in_as, small_limit):
    body = job_body_of_exactly(small_limit)
    assert len(body) == small_limit
    headers = {**log_in_as("ayse"), "Content-Type": "application/json"}

    reply = client.post("/jobs", content=body, headers=headers)

    assert reply.status_code == 202


def test_body_one_byte_over_the_limit_gives_413(client, log_in_as, small_limit):
    body = job_body_of_exactly(small_limit + 1)
    headers = {**log_in_as("ayse"), "Content-Type": "application/json"}

    reply = client.post("/jobs", content=body, headers=headers)

    assert_error_shape(reply, 413, "request_too_large")
    assert reply.json()["error"]["message"] == ("The request is larger than 200 bytes.")


@pytest.mark.parametrize(("extra", "status_code"), [(0, 202), (1, 413)])
def test_limit_works_without_a_content_length_header(
    client, log_in_as, small_limit, extra, status_code
):
    # Sending the body as a generator makes the client leave out
    # Content-Length and send "Transfer-Encoding: chunked" instead.
    body = job_body_of_exactly(small_limit + extra)
    headers = {**log_in_as("ayse"), "Content-Type": "application/json"}

    reply = client.post("/jobs", content=iter([body]), headers=headers)

    assert reply.request.headers.get("Content-Length") is None
    assert reply.status_code == status_code


@pytest.mark.parametrize("escaped", [False, True])
def test_default_limit_is_25_mb_and_fits_the_largest_valid_job(escaped):
    # The worst case: every character is an emoji. Written as escape codes
    # (json.dumps' default, escaped=True) each emoji takes 12 bytes.
    largest_job = json.dumps(
        {"task": "llm", "texts": ["😀" * 2000] * 1000}, ensure_ascii=escaped
    ).encode()

    assert settings.max_request_bytes == 25 * 1024 * 1024
    assert len(largest_job) <= settings.max_request_bytes


async def run_size_check(headers, chunks):
    """Send one request straight through `BodySizeLimitMiddleware`.

    Returns how many body pieces were read, what the inner app saw, and what
    was sent back. This shows the check stops early, without the whole body.
    """
    reads = []
    sent = []
    seen_by_app = []

    async def receive():
        reads.append(1)
        more = len(reads) < len(chunks)
        return {
            "type": "http.request",
            "body": chunks[len(reads) - 1],
            "more_body": more,
        }

    async def send(message):
        sent.append(message)

    async def inner_app(scope, receive, send):
        # Like FastAPI: read the body piece by piece until it's complete.
        while True:
            message = await receive()
            seen_by_app.append(message["body"])
            if not message["more_body"]:
                break

    scope = {"type": "http", "headers": headers}
    error = None
    try:
        await BodySizeLimitMiddleware(inner_app)(scope, receive, send)
    except ApiError as raised:
        error = raised
    return len(reads), seen_by_app, sent, error


def test_chunked_body_stops_being_read_once_over_the_limit(small_limit):
    ten_chunks = [b"x" * 60] * 10  # 600 bytes in total

    reads, seen, sent, error = asyncio.run(run_size_check([], ten_chunks))

    # 60, 120, 180 are fine; the 4th piece reaches 240 > 200 and stops it.
    assert reads == 4
    assert len(seen) == 3
    assert error is not None and error.status_code == 413


def test_declared_oversize_body_is_refused_without_reading_it(small_limit):
    reads, seen, sent, error = asyncio.run(
        run_size_check([(b"content-length", b"201")], [b"x" * 201])
    )

    assert reads == 0  # not a single byte of the body was read
    assert seen == []
    assert sent[0]["status"] == 413
    assert json.loads(sent[1]["body"])["error"]["code"] == "request_too_large"


def test_non_http_messages_pass_straight_through():
    calls = []

    async def inner_app(scope, receive, send):
        calls.append(scope["type"])

    asyncio.run(BodySizeLimitMiddleware(inner_app)({"type": "lifespan"}, None, None))

    assert calls == ["lifespan"]
