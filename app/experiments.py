"""Experiment E1 only: the same slow step written three ways (spec 9.1, D95).

E1 measures how much one wrongly written slow step freezes the rest of the
API. FastAPI runs every request on one *event loop*: a single line of work
that switches between requests whenever one of them is waiting. That only
works if the waiting is done in a way the loop can see. The three ways:

- **A** `async def` + a blocking wait (`time.sleep`, or a blocking web
  client): the loop itself sits and waits, so no other request can move.
- **B** plain `def`: FastAPI runs the route on one of its *helper threads*
  (about 40 workers on the side), so the loop stays free, until all helper
  threads are busy.
- **C** `async def` + `await` (`asyncio.sleep`, or Kuyruk's async Ollama
  client): the loop is told "wake me when the wait is over" and serves other
  requests meanwhile.

There are two slow steps: a plain timer (`sleep-a/b/c`, the main experiment)
and a real call to the AI (`ai-a/b/c`, the real-AI check). `ping` is a tiny
`async def` "ok": unlike `/health` (a plain `def`, which needs a helper
thread), it never uses a helper thread, so comparing the two shows whether a
delay comes from the stuck loop or from the helper threads running out.

All of this exists only with EXPERIMENTS_ENABLED=true (app/main.py adds these
addresses only then). Otherwise they are not part of Kuyruk at all: every
request to them answers 404, like any address that doesn't exist, and /docs
doesn't show them. Kuyruk refuses to start with it in demo mode (D95). The
load test that uses these addresses is `bench/e1_load.py`.
"""

import asyncio
import time

import httpx
from fastapi import APIRouter, status
from sqlmodel import Field, SQLModel

from app.ai import AIError, OllamaAI, make_ollama_ai
from app.errors import ApiError
from app.settings import settings

# How requests to Ollama are sent. None = really send them; tests put a
# pretend Ollama here (one for the blocking client, one for the async one).
blocking_transport: httpx.BaseTransport | None = None
async_transport: httpx.AsyncBaseTransport | None = None


# The addresses of this file. app/main.py adds them to Kuyruk only when
# EXPERIMENTS_ENABLED is true (D95); otherwise they are not part of Kuyruk at
# all, so every request to them gets the normal 404 and /docs never shows them.
router = APIRouter(prefix="/experiments/e1", tags=["experiments"])


class Waited(SQLModel):
    """The reply of a sleep address: which way, and how long it really waited."""

    mode: str
    waited_seconds: float


class E1Text(SQLModel):
    """The body of an AI address: one text, already cleaned for the AI.

    At most MAX_TEXT_CHARS characters, like a text in a job, so one request
    can't hold the AI (and, in mode A, the whole API) for long.
    """

    text: str = Field(min_length=1, max_length=settings.max_text_chars)


class Summarised(SQLModel):
    """The reply of an AI address: which way, how long, and the summary."""

    mode: str
    seconds: float
    summary: str


def check_wait(seconds: float) -> None:
    """Refuse a wait below 0 or above E1_MAX_WAIT_SECONDS with a 422.

    The upper limit stops anyone from freezing the API for long by accident.
    """
    if not 0 <= seconds <= settings.e1_max_wait_seconds:
        raise ApiError(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "invalid_wait",
            f"seconds must be between 0 and {settings.e1_max_wait_seconds:g}.",
        )


@router.get("/ping")
async def ping() -> dict[str, str]:
    """Answer "ok" straight from the event loop, never using a helper thread."""
    return {"status": "ok"}


@router.get("/sleep-a")
async def sleep_a(seconds: float) -> Waited:
    """Mode A (bad): `async def` with a blocking wait. Freezes the whole API."""
    check_wait(seconds)
    start = time.perf_counter()
    # Deliberately wrong: time.sleep holds the event loop for the whole wait.
    time.sleep(seconds)
    return Waited(mode="a", waited_seconds=time.perf_counter() - start)


@router.get("/sleep-b")
def sleep_b(seconds: float) -> Waited:
    """Mode B: plain `def`, so FastAPI runs it on a helper thread."""
    check_wait(seconds)
    start = time.perf_counter()
    # Blocks only this helper thread, not the event loop.
    time.sleep(seconds)
    return Waited(mode="b", waited_seconds=time.perf_counter() - start)


@router.get("/sleep-c")
async def sleep_c(seconds: float) -> Waited:
    """Mode C (good): `async def` with `await asyncio.sleep`. Nothing freezes."""
    check_wait(seconds)
    start = time.perf_counter()
    # The loop serves other requests until the wait is over.
    await asyncio.sleep(seconds)
    return Waited(mode="c", waited_seconds=time.perf_counter() - start)


def make_ollama() -> OllamaAI:
    """Kuyruk's own Ollama client, set up from the settings like the worker's."""
    return make_ollama_ai(async_transport)


def ai_failed(error: Exception) -> ApiError:
    """The 502 error reply for any failure while asking the AI.

    502 ("bad gateway") means: the problem is in the server behind us (here,
    Ollama), not in the request.
    """
    return ApiError(
        status.HTTP_502_BAD_GATEWAY,
        "ai_failed",
        f"The AI did not answer: {type(error).__name__}.",
    )


def blocking_summarise(text: str) -> str:
    """Ask Ollama for a summary with a *blocking* web client (for modes A and B).

    It sends exactly the request Kuyruk's own client sends
    (`OllamaAI.chat_request`) and reads the reply exactly as Kuyruk does
    (`OllamaAI.answer_from`), but with `httpx.Client`, which holds whatever
    runs it until Ollama answers. So all three modes fail the same way.
    """
    ollama = make_ollama()
    try:
        with httpx.Client(
            base_url=ollama.url,
            timeout=ollama.timeout_seconds,
            transport=blocking_transport,
        ) as client:
            response = client.post("/api/chat", json=ollama.chat_request(text))
        return ollama.answer_from(response)
    # No answer at all (a web error), or an answer Kuyruk would refuse.
    except (httpx.HTTPError, AIError) as error:
        raise ai_failed(error) from error


@router.post("/ai-a")
async def ai_a(body: E1Text) -> Summarised:
    """Real-AI mode A (bad): a blocking client inside `async def`."""
    start = time.perf_counter()
    # Deliberately wrong: the event loop waits for Ollama's whole answer.
    summary = blocking_summarise(body.text)
    return Summarised(mode="a", seconds=time.perf_counter() - start, summary=summary)


@router.post("/ai-b")
def ai_b(body: E1Text) -> Summarised:
    """Real-AI mode B: the same blocking client in plain `def` (helper thread)."""
    start = time.perf_counter()
    summary = blocking_summarise(body.text)
    return Summarised(mode="b", seconds=time.perf_counter() - start, summary=summary)


@router.post("/ai-c")
async def ai_c(body: E1Text) -> Summarised:
    """Real-AI mode C (good): Kuyruk's own async Ollama client, awaited."""
    start = time.perf_counter()
    try:
        summary = await make_ollama().summarise(body.text)
    except AIError as error:
        raise ai_failed(error) from error
    return Summarised(mode="c", seconds=time.perf_counter() - start, summary=summary)
