"""Middleware: code that runs around every request, before and after the route.

Three pieces live here:

- `RequestIdMiddleware` gives every request a *request ID* (a short label sent
  back in the `X-Request-ID` header, so one request can be found in the logs),
  writes one timing line per request to the log, and turns any unexpected
  crash into a safe 500 reply.
- `BodySizeLimitMiddleware` refuses request bodies over `max_request_bytes`
  (413), without reading the whole body first.
- `KuyrukCORSMiddleware` is Starlette's CORS (which website's pages may call
  the API from a browser, D121), with its one refusal in Kuyruk's error
  shape.

They are added to the app in `app/main.py`.
"""

import logging
import re
import time
import uuid

from fastapi import Request, Response, status
from starlette.datastructures import Headers
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.middleware.cors import CORSMiddleware
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.errors import INTERNAL_ERROR_MESSAGE, ApiError, error_response
from app.settings import settings

# The name of the header that carries the request ID, in and out.
REQUEST_ID_HEADER = "X-Request-ID"

# A client's request ID is reused only if it contains nothing but letters,
# digits, ".", "_" and "-". This stops someone from sending, say, a line break
# followed by a fake log line, which would then be written into our logs.
SAFE_REQUEST_ID = re.compile(r"[A-Za-z0-9._-]+")

# The logger for request timing lines. (A logger is a named channel for log
# messages; `app/main.py` decides where its lines are printed.)
request_log = logging.getLogger("kuyruk.requests")


def choose_request_id(sent_by_client: str | None) -> str:
    """Reuse the client's request ID if it looks safe, otherwise make a new one.

    Reusing it lets a client (or another service in front of Kuyruk) follow
    one request through several logs using a label it already knows.
    """
    if (
        sent_by_client
        and len(sent_by_client) <= settings.request_id_max_length
        and SAFE_REQUEST_ID.fullmatch(sent_by_client)
    ):
        return sent_by_client
    # A UUID4 is random, so two requests practically never get the same ID.
    return uuid.uuid4().hex


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Add a request ID to every reply, log its timing, and catch crashes.

    This sits outside every route and error handler, so it also sees error
    replies. Unexpected crashes are caught here (not in `app/errors.py`)
    because FastAPI's own crash reply would be sent from a place this
    middleware doesn't reach, and would lose the request ID header.
    """

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        """Run the request, then label, time and log it."""
        request_id = choose_request_id(request.headers.get(REQUEST_ID_HEADER))
        # Keep it on the request, so routes could read it if they need to.
        request.state.request_id = request_id
        # perf_counter is a clock made for measuring durations precisely.
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            # An unexpected crash. Log the full details for us (with the
            # request ID so it can be found), but tell the client nothing
            # about what went wrong inside.
            request_log.exception("Unhandled error, request_id=%s", request_id)
            response = error_response(
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                "internal_error",
                INTERNAL_ERROR_MESSAGE,
            )
        milliseconds = (time.perf_counter() - started) * 1000
        response.headers[REQUEST_ID_HEADER] = request_id
        # Only method, path, status, time and ID: never headers or bodies,
        # which may hold passwords or login passes.
        request_log.info(
            "method=%s path=%s status=%d ms=%.1f request_id=%s",
            request.method,
            request.url.path,
            response.status_code,
            milliseconds,
            request_id,
        )
        return response


def request_too_large() -> ApiError:
    """The 413 error for a body over the limit, with a readable message."""
    return ApiError(
        status.HTTP_413_CONTENT_TOO_LARGE,
        "request_too_large",
        f"The request is larger than {settings.max_request_bytes:,} bytes.",
    )


class BodySizeLimitMiddleware:
    """Refuse request bodies larger than `max_request_bytes` with 413.

    It is written as plain *ASGI* (the low-level way a Python web server hands
    a request to the app, piece by piece) because it must see the body as it
    arrives, before anything reads it all into memory.

    Two cases:
    - The client says the size up front (`Content-Length` header) and it is
      too big: refuse at once, without reading the body at all.
    - The client doesn't say (it sends the body in chunks): count the bytes as
      each chunk arrives and stop as soon as the count passes the limit.
    """

    def __init__(self, app: ASGIApp) -> None:
        """Wrap `app`, the rest of Kuyruk that runs after this check."""
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Check one request's size, then pass it on to the app."""
        # Only web requests have bodies; start-up/shutdown messages pass by.
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        limit = settings.max_request_bytes

        declared = dict(scope["headers"]).get(b"content-length")
        if declared is not None and declared.isdigit() and int(declared) > limit:
            error = request_too_large()
            response = error_response(error.status_code, error.code, error.detail)
            await response(scope, receive, send)
            return

        received = 0

        async def counting_receive() -> Message:
            """Pass on the next piece of the body, refusing once it's too big."""
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    # FastAPI turns this error into the 413 reply (via the
                    # handler in app/errors.py); the rest is never read.
                    raise request_too_large()
            return message

        await self.app(scope, counting_receive, send)


class KuyrukCORSMiddleware(CORSMiddleware):
    """Starlette's CORS, with a refused "may I?" in Kuyruk's error shape (D121).

    Before a page from another website sends a request with a login header,
    the browser asks first (a *preflight*: an OPTIONS request). Starlette
    answers a refused preflight with a plain-text 400, "Disallowed CORS
    origin". CORS sits outside the request-ID middleware, so here that one
    reply is rebuilt in the shared error shape, with its own request ID,
    like every other error reply. Allowed preflights and everything else are
    left exactly as Starlette makes them.
    """

    def preflight_response(self, request_headers: Headers) -> Response:
        """Answer a preflight; a refusal gets the error shape and an ID."""
        response = super().preflight_response(request_headers)
        if response.status_code != status.HTTP_400_BAD_REQUEST:
            return response
        # Starlette's reason, e.g. "Disallowed CORS origin, headers".
        reason = bytes(response.body).decode() or "Disallowed CORS request"
        return error_response(
            status.HTTP_400_BAD_REQUEST,
            "cors_refused",
            f"{reason}.",
            headers={REQUEST_ID_HEADER: uuid.uuid4().hex},
        )
