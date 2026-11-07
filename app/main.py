"""The entry point of Kuyruk's web API.

This file creates the FastAPI application (FastAPI is the library that turns
Python functions into web addresses, called *routes*), makes sure the database
tables exist on startup, and puts the pieces together:

- the routes: sign-up and log-in (`app/auth.py`), jobs (`app/jobs.py`),
  live progress (`app/events.py`) and the budget (`app/budget.py`);
- the shared error shape for every error reply (`app/errors.py`);
- the middleware that runs around every request (`app/middleware.py`):
  request ID, timing log, crash safety and the request size limit.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.auth import router as auth_router
from app.budget import router as budget_router
from app.db import create_db_and_tables
from app.errors import install_error_handlers
from app.events import router as events_router
from app.jobs import router as jobs_router
from app.middleware import BodySizeLimitMiddleware, RequestIdMiddleware
from app.settings import settings

# Print Kuyruk's own log lines (e.g. the timing line of every request) to the
# console. The web server sets up only its own loggers, so without this our
# INFO lines would be silently dropped. Done only once, even if this file is
# loaded again.
kuyruk_log = logging.getLogger("kuyruk")
if not kuyruk_log.handlers:
    console = logging.StreamHandler()
    console.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    kuyruk_log.addHandler(console)
    kuyruk_log.setLevel(logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Run start-up work before the API accepts requests.

    A *lifespan* is FastAPI's hook for "when the app starts / stops": code
    before `yield` runs at start-up, code after it at shutdown. Here we only
    create any missing database tables.
    """
    create_db_and_tables()
    yield


app = FastAPI(title=settings.app_name, lifespan=lifespan)
app.include_router(auth_router)
app.include_router(jobs_router)
app.include_router(events_router)
app.include_router(budget_router)
install_error_handlers(app)

# Middleware added later wraps the one added earlier, so the request-ID
# middleware is the outermost layer: it also labels and logs the size
# limit's 413 replies.
app.add_middleware(BodySizeLimitMiddleware)
app.add_middleware(RequestIdMiddleware)


@app.get("/health")
def health() -> dict[str, str]:
    """Answer "ok" so people and tools (Docker, CI) can check the API is up."""
    return {"status": "ok"}
