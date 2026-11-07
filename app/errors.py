"""Kuyruk's one error shape, used for every error reply.

Every error, whatever went wrong, is sent back as:

    {"error": {"code": "not_found", "message": "Job not found."}}

`code` is a short fixed word a program can check; `message` is a sentence a
person can read. One shape means the interface (and anyone writing a client)
needs only one piece of code to show errors.

This file holds `ApiError` (the error our own code raises) and the *exception
handlers*: functions FastAPI calls when an error escapes a route, which turn
that error into a reply in this shape. Unexpected crashes (500) are handled in
`app/middleware.py`, because they must also carry the request ID.
"""

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

# The code used when an error doesn't name its own, chosen by its status
# number. These cover errors raised by FastAPI itself, e.g. 404 for an address
# that doesn't exist, or 401 when the login header is missing.
DEFAULT_CODES = {
    status.HTTP_400_BAD_REQUEST: "bad_request",
    status.HTTP_401_UNAUTHORIZED: "not_authenticated",
    status.HTTP_404_NOT_FOUND: "not_found",
    status.HTTP_405_METHOD_NOT_ALLOWED: "method_not_allowed",
    status.HTTP_409_CONFLICT: "conflict",
    status.HTTP_413_CONTENT_TOO_LARGE: "request_too_large",
    status.HTTP_422_UNPROCESSABLE_CONTENT: "validation_error",
}

# The only thing a 500 reply ever says. The real cause goes to the server log;
# showing it to the client could reveal how Kuyruk works inside.
INTERNAL_ERROR_MESSAGE = "Something went wrong on our side."


class ApiError(HTTPException):
    """An error our own code raises on purpose, with its own short `code`.

    It is an `HTTPException` (FastAPI's "stop and reply with this status"
    error), plus a code word, e.g. `ApiError(409, "username_taken", "...")`.
    """

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        headers: dict[str, str] | None = None,
    ) -> None:
        """Remember the status, code word and message for the error reply."""
        super().__init__(status_code=status_code, detail=message, headers=headers)
        self.code = code


def error_response(
    status_code: int,
    code: str,
    message: str,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    """Build a reply in Kuyruk's error shape."""
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message}},
        headers=headers,
    )


async def handle_http_error(
    request: Request, exc: StarletteHTTPException
) -> JSONResponse:
    """Turn any HTTP error (ours or FastAPI's own) into the error shape.

    Extra headers are kept, e.g. `WWW-Authenticate` on a 401, which tells the
    client how to log in.
    """
    code = getattr(exc, "code", None) or DEFAULT_CODES.get(exc.status_code, "error")
    return error_response(exc.status_code, code, str(exc.detail), exc.headers)


def describe_validation_error(error: dict) -> str:
    """Turn one of FastAPI's validation problems into a readable sentence.

    FastAPI describes where the problem is as a path such as
    ("body", "task") or ("query", "limit"). We name the field and add the
    message, e.g. "task: Input should be 'normalise', 'count_tokens' or 'llm'".
    The user's input is deliberately not repeated back.
    """
    # Leave out the first part ("body", "query", "header"...), which only says
    # which part of the request it was.
    field = ".".join(str(part) for part in error["loc"][1:])
    if not field:
        return f"Request {error['loc'][0]}: {error['msg']}"
    return f"{field}: {error['msg']}"


async def handle_validation_error(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Turn FastAPI's 422 "your input broke a rule" error into the error shape.

    Every problem FastAPI found becomes one sentence, joined with "; ".
    """
    message = "; ".join(describe_validation_error(error) for error in exc.errors())
    return error_response(
        status.HTTP_422_UNPROCESSABLE_CONTENT, "validation_error", message
    )


def install_error_handlers(app: FastAPI) -> None:
    """Tell the app to reply to errors in the shared shape."""
    # Registering for Starlette's HTTPException also covers FastAPI's own and
    # our ApiError, because both are kinds of it.
    app.add_exception_handler(StarletteHTTPException, handle_http_error)
    app.add_exception_handler(RequestValidationError, handle_validation_error)
