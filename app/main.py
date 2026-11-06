"""The entry point of Kuyruk's web API.

This file creates the FastAPI application (FastAPI is the library that turns
Python functions into web addresses, called *routes*), makes sure the database
tables exist on startup, and lists the routes. More routes are added in later
slices.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.db import create_db_and_tables
from app.settings import settings


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


@app.get("/health")
def health() -> dict[str, str]:
    """Answer "ok" so people and tools (Docker, CI) can check the API is up."""
    return {"status": "ok"}
