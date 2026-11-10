"""The AI behind the `llm` task: one small interface, two backends (spec 2.3-2.4).

Every AI here does one thing: given a text, return a one-sentence Turkish
summary, or raise an error marked *temporary* (try again later, it may work) or
*permanent* (it will never work, don't retry). The worker (Slice 5) decides what
to do with that mark (D16, D47).

The two backends are:
- `OllamaAI`: the real model, running in Ollama on this Mac.
- `FakeAI`: a stand-in for tests and experiments that waits, then answers with
  a fixed sentence, and can fail on purpose.

The setting AI_BACKEND chooses one, and only `get_ai_backend` here reads it, so
the rest of Kuyruk never knows which backend it is talking to.

"async" means a function can pause while waiting (for the AI, or a timer)
without stopping the rest of the program; `await` marks those pauses.
"""

import asyncio
import csv
import random
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Literal, Protocol

import httpx

from app.settings import settings

# The one fixed instruction sent with every text (D04). Not a setting: nobody
# may change it, so all runs stay comparable. The second sentence stops the
# model from adding alternative versions, which it did in testing.
INSTRUCTION = "Summarise this in one Turkish sentence. Reply with only that sentence."

# How much of an error reply from Ollama is kept in a message, in characters,
# so a huge reply can't fill the database.
MAX_ERROR_DETAIL = 200


class AIError(Exception):
    """Something went wrong while asking the AI. Always one of the two below."""


class TemporaryAIError(AIError):
    """The AI couldn't answer right now (too slow, switched off, overloaded).

    Trying the same text again a little later may work, so it is retried.
    """


class PermanentAIError(AIError):
    """The request can never succeed as it is (e.g. wrong model name).

    Retrying would only waste time, so the text fails at once.
    """


class AISetupError(RuntimeError):
    """The AI settings themselves are wrong (e.g. the replay file is missing).

    This is a setup mistake to fix, not a failure of one text.
    """


class AIBackend(Protocol):
    """What every AI backend offers: the one method the worker calls.

    A Protocol is a description of the methods an object must have; any class
    with a matching `summarise` counts as an AIBackend.
    """

    async def summarise(self, text: str) -> str:
        """Return a one-sentence Turkish summary of `text`, or raise an AIError."""
        ...


def make_ollama_ai(transport: httpx.AsyncBaseTransport | None = None) -> "OllamaAI":
    """Kuyruk's Ollama client, set up from the settings.

    The one place that turns settings into an `OllamaAI`, used by the worker
    (through `get_ai_backend`), the E1 experiment and the measuring scripts,
    so they all talk to Ollama the same way. `transport`: see `OllamaAI`.
    """
    return OllamaAI(
        url=settings.ollama_url,
        model=settings.ollama_model,
        temperature=settings.ollama_temperature,
        seed=settings.ollama_seed,
        timeout_seconds=settings.ollama_timeout_seconds,
        transport=transport,
    )


def get_ai_backend() -> AIBackend:
    """Create the AI chosen by the AI_BACKEND setting, using its settings.

    The only place in Kuyruk that knows which backend is in use.
    """
    if settings.ai_backend == "ollama":
        return make_ollama_ai()
    return FakeAI(
        wait_mode=settings.fake_ai_wait_mode,
        wait_seconds=settings.fake_ai_wait_seconds,
        replay_file=settings.fake_ai_replay_file,
        seed=settings.fake_ai_seed,
        answer=settings.fake_ai_answer,
        fail_every=settings.fake_ai_fail_every,
        fail_kind=settings.fake_ai_fail_kind,
        # In demo mode each answer echoes its own text (D125); never in the
        # experiments, which can't run in demo mode (D95).
        demo_words=settings.fake_ai_demo_words if settings.demo_mode else 0,
    )


class FakeAI:
    """A pretend AI: waits, then returns a fixed answer or fails on purpose.

    It exists so tests and experiments can control exactly how long the AI
    takes and when it fails, which the real AI can't do (D05).

    In demo mode (`demo_words` above 0) the answer instead starts with
    "Demo: " and repeats the text's first few words, so every result is
    different and nobody mistakes it for a real summary (D125). The waits
    are exactly the same either way.
    """

    def __init__(
        self,
        *,
        wait_mode: Literal["fixed", "replay"],
        wait_seconds: float,
        replay_file: str,
        seed: int,
        answer: str,
        fail_every: int,
        fail_kind: Literal["temporary", "permanent"],
        demo_words: int = 0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        """Store the behaviour; in replay mode, read the measured times now.

        `sleep` is the waiting function. It is asyncio.sleep, which pauses
        without blocking the program (this matters for experiment E1); tests
        pass a stand-in that records the waits instead of really waiting.
        """
        self.wait_mode = wait_mode
        self.wait_seconds = wait_seconds
        self.answer = answer
        self.demo_words = demo_words
        self.fail_every = fail_every
        self.fail_kind = fail_kind
        self.sleep = sleep
        # Counts every call, so "fail every Nth call" knows when it's the Nth.
        self.calls = 0
        # Its own random number generator started from a fixed seed: the same
        # seed always gives the same series of waits (D06).
        self.random = random.Random(seed)
        self.replay_times = (
            read_replay_times(replay_file) if wait_mode == "replay" else []
        )

    async def summarise(self, text: str) -> str:
        """Wait as configured, then return the fixed answer or fail on purpose."""
        # Count before waiting, so calls running at the same time are numbered
        # in the order they started.
        self.calls += 1
        call_number = self.calls
        await self.sleep(self.next_wait())
        if self.fail_every and call_number % self.fail_every == 0:
            error = (
                TemporaryAIError if self.fail_kind == "temporary" else PermanentAIError
            )
            raise error(f"Fake AI failed on purpose ({self.fail_kind}).")
        if self.demo_words:
            return demo_answer(text, self.demo_words)
        return self.answer

    def next_wait(self) -> float:
        """How many seconds the next call waits: fixed, or drawn from real times."""
        if self.wait_mode == "replay":
            return self.random.choice(self.replay_times)
        return self.wait_seconds


def demo_answer(text: str, words: int) -> str:
    """The demo AI's answer: "Demo: " and the text's first `words` words (D125).

    The text is the one the AI receives, so emails and phone numbers are
    already hidden (D50). "…" marks that words were left out.
    """
    parts = text.split()
    shown = " ".join(parts[:words])
    return f"Demo: {shown}{'…' if len(parts) > words else ''}"


def read_replay_times(path: str) -> list[float]:
    """Read the measured AI times (in seconds) from a CSV file.

    A CSV is a plain table file with values separated by commas. This one needs
    a column named `seconds`; Slice 10 wrote `bench/data/ollama_times_at_4.csv` in
    that format. Raises AISetupError if the file is missing, empty or wrong.
    """
    try:
        with Path(path).open(newline="", encoding="utf-8") as file:
            times = [float(row["seconds"]) for row in csv.DictReader(file)]
    except FileNotFoundError:
        raise AISetupError(f"Replay file {path} is missing.") from None
    except (KeyError, TypeError, ValueError):
        raise AISetupError(
            f"Replay file {path} needs a `seconds` column of numbers."
        ) from None
    if not times or min(times) < 0:
        raise AISetupError(
            f"Replay file {path} needs at least one time, none negative."
        )
    return times


class OllamaAI:
    """The real AI: sends each text to Ollama's chat address on this Mac.

    Ollama answers web requests (HTTP) at e.g. http://localhost:11434. Kuyruk
    sends one plain request per text (D27) and reads the summary from the reply.
    """

    def __init__(
        self,
        *,
        url: str,
        model: str,
        temperature: float,
        seed: int,
        timeout_seconds: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        """Store the connection details.

        `transport` is how requests are actually sent. Normally it's None
        (really send them); tests pass a pretend one that answers like Ollama.
        """
        self.url = url
        self.model = model
        self.temperature = temperature
        self.seed = seed
        self.timeout_seconds = timeout_seconds
        self.transport = transport

    def chat_request(self, text: str) -> dict:
        """The exact request Kuyruk sends to Ollama for one text.

        Kept in one place so the E1 experiment's blocking client
        (`app/experiments.py`) sends exactly the same request as Kuyruk.
        """
        return {
            "model": self.model,
            "messages": [
                {"role": "system", "content": INSTRUCTION},
                {"role": "user", "content": text},
            ],
            # Thinking off: otherwise the model first writes a long hidden
            # reasoning, which adds large, uneven delay (spec 2.4, D08).
            "think": False,
            # One complete reply, not a word-by-word stream.
            "stream": False,
            # Temperature 0 (no randomness in word choice) and a fixed seed,
            # so the same text almost always gives the same summary (D46).
            "options": {"temperature": self.temperature, "seed": self.seed},
        }

    async def summarise(self, text: str) -> str:
        """Ask Ollama for the summary, labelling every failure temporary or permanent.

        Temporary: too slow (timeout), can't connect (Ollama off), a reply that
        breaks off halfway, a 5xx reply (Ollama's own fault or busy) or 429
        ("too many requests"). Everything else is permanent (D47).
        """
        request = self.chat_request(text)
        try:
            async with httpx.AsyncClient(
                base_url=self.url,
                timeout=self.timeout_seconds,
                transport=self.transport,
            ) as client:
                response = await client.post("/api/chat", json=request)
        except httpx.TimeoutException as error:
            raise TemporaryAIError("The AI took too long to answer.") from error
        except httpx.ConnectError as error:
            raise TemporaryAIError(
                "Could not reach the AI. Is Ollama running?"
            ) from error
        # The connection broke while the reply was arriving (Ollama closed it
        # early, or the connection was reset): the next try may work.
        except (httpx.RemoteProtocolError, httpx.ReadError) as error:
            raise TemporaryAIError("The AI's reply broke off halfway.") from error
        except httpx.HTTPError as error:
            raise PermanentAIError(f"Talking to the AI failed: {error}") from error

        return self.answer_from(response)

    def answer_from(self, response: httpx.Response) -> str:
        """Read the summary from Ollama's reply, or raise a labelled error.

        Kept in one place so the E1 experiment's blocking client
        (`app/experiments.py`) judges Ollama's reply exactly like Kuyruk.
        """
        # 5xx: a problem on Ollama's side, or busy. 429: "too many requests".
        if response.status_code >= 500 or response.status_code == 429:
            raise TemporaryAIError(
                f"The AI had a problem or is busy (HTTP {response.status_code}): "
                f"{response.text[:MAX_ERROR_DETAIL]}"
            )
        if response.status_code != 200:
            raise PermanentAIError(
                f"The AI refused the request (HTTP {response.status_code}): "
                f"{response.text[:MAX_ERROR_DETAIL]}"
            )
        # A successful reply looks like {"message": {"content": "..."}, ...}.
        try:
            answer = response.json()["message"]["content"].strip()
        except (ValueError, KeyError, TypeError, AttributeError):
            raise PermanentAIError(
                "The AI's reply was not in the expected form."
            ) from None
        if not answer:
            raise PermanentAIError("The AI returned an empty answer.")
        return answer
