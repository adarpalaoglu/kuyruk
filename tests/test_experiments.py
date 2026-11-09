"""Tests for the E1 experiment addresses (`app/experiments.py`, spec 9.1, D95).

- Off by default: every address answers 404 in Kuyruk's error shape, with
  any method, and is hidden from /docs; Kuyruk refuses to start with them on
  in demo mode.
- On: the three sleep modes wait and say how long; the wait limits give 422;
  the three AI modes, against a pretend Ollama (never the real one), send
  exactly Kuyruk's request and return the summary, or 502 if Ollama fails.
- The mechanism itself, small and fast: while mode A waits, a ping can't
  get through; while mode C waits, it comes back at once.
"""

import asyncio
import json
import time

import httpx
import pytest

from app import experiments
from app.ai import OllamaAI
from app.main import app
from app.settings import Settings, settings

SLEEPS = [
    "/experiments/e1/sleep-a",
    "/experiments/e1/sleep-b",
    "/experiments/e1/sleep-c",
]
AIS = ["/experiments/e1/ai-a", "/experiments/e1/ai-b", "/experiments/e1/ai-c"]


@pytest.fixture
def experiments_on(monkeypatch):
    """Switch the experiment addresses on for one test.

    app/main.py adds them only when EXPERIMENTS_ENABLED is true at start-up,
    which it isn't in tests; so this adds them to the app for one test, as
    start-up would, and takes them away again afterwards.
    """
    monkeypatch.setattr(settings, "experiments_enabled", True)
    routes = list(app.router.routes)
    app.include_router(experiments.router)
    yield
    app.router.routes[:] = routes


def assert_error(reply, status_code, code):
    """The reply is an error in Kuyruk's one error shape, with a request ID."""
    assert reply.status_code == status_code
    assert reply.json()["error"]["code"] == code
    assert "x-request-id" in reply.headers


# --- Off by default -------------------------------------------------------


def test_experiments_are_off_by_default(monkeypatch):
    """A fresh setup has the experiment addresses switched off."""
    monkeypatch.delenv("EXPERIMENTS_ENABLED", raising=False)
    assert Settings(_env_file=None).experiments_enabled is False


def test_experiments_refused_in_demo_mode(monkeypatch):
    """Kuyruk won't start with the experiments on in demo mode (D95)."""
    monkeypatch.setenv("EXPERIMENTS_ENABLED", "true")
    monkeypatch.setenv("DEMO_MODE", "true")
    with pytest.raises(ValueError, match="EXPERIMENTS_ENABLED and DEMO_MODE"):
        Settings(_env_file=None)


@pytest.mark.parametrize("path", [*SLEEPS, "/experiments/e1/ping"])
def test_every_get_address_is_404_when_off(client, path):
    """Switched off, each address looks like one that doesn't exist."""
    assert_error(client.get(path, params={"seconds": 0}), 404, "not_found")


@pytest.mark.parametrize("path", AIS)
def test_every_ai_address_is_404_when_off(client, path):
    """Switched off, the AI addresses are 404 too, before the body is read."""
    assert_error(client.post(path, json={"text": "x"}), 404, "not_found")


@pytest.mark.parametrize("method", ["GET", "POST", "PUT", "DELETE"])
@pytest.mark.parametrize("path", [SLEEPS[0], AIS[0]])
def test_any_method_is_404_when_off(client, method, path):
    """No method gives it away (e.g. 405 "method not allowed"): always 404."""
    assert_error(client.request(method, path), 404, "not_found")


def test_experiment_addresses_exist_only_when_switched_on_at_start(monkeypatch):
    """app/main.py adds the addresses only when the setting is on (D95).

    Loads app/main.py again with the setting on, asks the new app for the
    ping, then loads it again with the setting off.
    """
    import importlib

    from fastapi.testclient import TestClient

    from app import main

    def ping_status() -> int:
        """Load app/main.py afresh and ask that app for the ping."""
        fresh = importlib.reload(main).app
        return TestClient(fresh).get("/experiments/e1/ping").status_code

    try:
        monkeypatch.setattr(settings, "experiments_enabled", True)
        assert ping_status() == 200
    finally:
        monkeypatch.setattr(settings, "experiments_enabled", False)
        assert ping_status() == 404


def test_experiment_addresses_are_hidden_from_docs(client):
    """Nothing under /experiments appears in the API description (/docs)."""
    paths = client.get("/openapi.json").json()["paths"]
    assert not [path for path in paths if path.startswith("/experiments")]


# --- Sleep modes ------------------------------------------------------------


@pytest.mark.parametrize("path", SLEEPS)
def test_sleep_modes_wait_and_say_how_long(client, experiments_on, path):
    """Each mode waits about the time asked and names itself in the reply."""
    reply = client.get(path, params={"seconds": 0.05})
    assert reply.status_code == 200
    body = reply.json()
    assert body["mode"] == path[-1]
    assert 0.05 <= body["waited_seconds"] < 1


@pytest.mark.parametrize("seconds", [-1, 10.5])
@pytest.mark.parametrize("path", SLEEPS)
def test_wait_outside_the_limits_is_422(client, experiments_on, path, seconds):
    """Below 0 or above E1_MAX_WAIT_SECONDS (10) is refused before waiting."""
    reply = client.get(path, params={"seconds": seconds})
    assert_error(reply, 422, "invalid_wait")
    assert "between 0 and 10" in reply.json()["error"]["message"]


def test_missing_wait_is_422(client, experiments_on):
    """A sleep address without `seconds` is a normal validation error."""
    assert_error(client.get(SLEEPS[0]), 422, "validation_error")


def test_ping_answers_ok(client, experiments_on):
    """The async ping answers like /health."""
    reply = client.get("/experiments/e1/ping")
    assert reply.status_code == 200
    assert reply.json() == {"status": "ok"}


# --- AI modes, with a pretend Ollama -----------------------------------------


@pytest.fixture
def pretend_ollama(monkeypatch):
    """Point both Ollama clients at a pretend Ollama; returns what it received.

    The blocking client (modes A and B) and Kuyruk's async client (mode C)
    each get their own pretend transport answering the same way.
    """
    received: list[dict] = []

    def answer(request: httpx.Request) -> httpx.Response:
        """Record the request body and reply like Ollama's chat address."""
        received.append(json.loads(request.content))
        return httpx.Response(200, json={"message": {"content": " Özet. "}})

    async def answer_async(request: httpx.Request) -> httpx.Response:
        """The same answer, for the async client."""
        return answer(request)

    monkeypatch.setattr(experiments, "blocking_transport", httpx.MockTransport(answer))
    monkeypatch.setattr(
        experiments, "async_transport", httpx.MockTransport(answer_async)
    )
    return received


@pytest.mark.parametrize("path", AIS)
def test_ai_modes_send_kuyruks_request_and_return_the_summary(
    client, experiments_on, pretend_ollama, path
):
    """All three ways send exactly OllamaAI's request and return its summary."""
    reply = client.post(path, json={"text": "Kargo hızlı geldi."})
    assert reply.status_code == 200
    body = reply.json()
    assert body["mode"] == path[-1]
    assert body["summary"] == "Özet."
    assert body["seconds"] >= 0
    expected = OllamaAI(
        url=settings.ollama_url,
        model=settings.ollama_model,
        temperature=settings.ollama_temperature,
        seed=settings.ollama_seed,
        timeout_seconds=1,
    ).chat_request("Kargo hızlı geldi.")
    assert pretend_ollama == [expected]


@pytest.mark.parametrize("path", AIS)
def test_ai_failure_is_502(client, experiments_on, monkeypatch, path):
    """If Ollama answers with an error, every mode replies 502 `ai_failed`."""

    def broken(request: httpx.Request) -> httpx.Response:
        """Ollama with a problem of its own."""
        return httpx.Response(500, text="boom")

    async def broken_async(request: httpx.Request) -> httpx.Response:
        """The same problem, for the async client."""
        return broken(request)

    monkeypatch.setattr(experiments, "blocking_transport", httpx.MockTransport(broken))
    monkeypatch.setattr(
        experiments, "async_transport", httpx.MockTransport(broken_async)
    )
    assert_error(client.post(path, json={"text": "x"}), 502, "ai_failed")


@pytest.mark.parametrize("path", AIS)
@pytest.mark.parametrize(
    "reply",
    [{"x": 1}, {"message": {"content": None}}, {"message": {"content": "  "}}],
)
def test_odd_ollama_replies_fail_the_same_way_in_every_mode(
    client, experiments_on, monkeypatch, path, reply
):
    """No summary, a null one, or an empty one: 502 in A, B and C alike."""

    def odd(request: httpx.Request) -> httpx.Response:
        """Ollama answering 200 without a usable summary."""
        return httpx.Response(200, json=reply)

    async def odd_async(request: httpx.Request) -> httpx.Response:
        """The same, for the async client."""
        return odd(request)

    monkeypatch.setattr(experiments, "blocking_transport", httpx.MockTransport(odd))
    monkeypatch.setattr(experiments, "async_transport", httpx.MockTransport(odd_async))
    assert_error(client.post(path, json={"text": "x"}), 502, "ai_failed")


@pytest.mark.parametrize("path", AIS)
def test_ai_text_is_limited_like_a_job_text(client, experiments_on, path):
    """Empty, or over MAX_TEXT_CHARS (2,000): refused before reaching the AI."""
    for text in ["", "a" * (settings.max_text_chars + 1)]:
        reply = client.post(path, json={"text": text})
        assert_error(reply, 422, "validation_error")


def test_blocking_client_cant_reach_ollama_is_502(client, experiments_on, monkeypatch):
    """Ollama switched off (no connection) is a 502 too."""

    def unreachable(request: httpx.Request) -> httpx.Response:
        """Like Ollama not running."""
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(
        experiments, "blocking_transport", httpx.MockTransport(unreachable)
    )
    assert_error(client.post(AIS[1], json={"text": "x"}), 502, "ai_failed")


def test_blocking_client_reply_without_a_summary_is_502(
    client, experiments_on, monkeypatch
):
    """A 200 reply that isn't shaped like Ollama's is a failure too."""
    monkeypatch.setattr(
        experiments,
        "blocking_transport",
        httpx.MockTransport(lambda request: httpx.Response(200, json={"x": 1})),
    )
    assert_error(client.post(AIS[0], json={"text": "x"}), 502, "ai_failed")


# --- The mechanism -------------------------------------------------------------


async def ping_finish_during(path: str) -> float:
    """When a ping finishes, counted from the start, if it is sent 0.05 s
    after `path` starts a 0.3-second wait.

    Both requests go to the real app inside this test's own event loop, like
    two visitors of one server. The test client shares that loop, so a loop
    held by mode A also holds the ping back; the ping's finish time shows it.
    """
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://e1") as client:
        start = time.perf_counter()

        async def late_ping() -> float:
            """Send a ping 0.05 s after the start; return when it finished."""
            await asyncio.sleep(0.05)
            await client.get("/experiments/e1/ping")
            return time.perf_counter() - start

        slow, finished = await asyncio.gather(
            client.get(path, params={"seconds": 0.3}), late_ping()
        )
        assert slow.status_code == 200
    return finished


@pytest.mark.anyio
async def test_mode_a_holds_up_a_ping_and_mode_c_does_not(experiments_on):
    """A's blocking wait stops the ping until it ends; C's await doesn't."""
    assert await ping_finish_during(SLEEPS[0]) >= 0.29
    assert await ping_finish_during(SLEEPS[2]) < 0.2


@pytest.mark.anyio
async def test_health_stays_fast_while_all_helper_threads_are_busy(experiments_on):
    """D99: 45 mode-B requests fill all ~40 helper threads for 2 s, yet
    /health (async def, no helper thread needed) answers at once.

    The limit is 0.5 s, not tighter: on GitHub's machines /health once took
    0.13 s here (E4). A plain-def /health would have to wait for a free
    helper thread, about 1.9 s, so it would still fail clearly.
    """
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://e1") as client:
        slow = [
            asyncio.create_task(client.get(SLEEPS[1], params={"seconds": 2.0}))
            for _ in range(45)
        ]
        # Let the slow requests take every helper thread first.
        await asyncio.sleep(0.1)
        start = time.perf_counter()
        reply = await client.get("/health")
        took = time.perf_counter() - start
        assert all(r.status_code == 200 for r in await asyncio.gather(*slow))
    assert reply.json() == {"status": "ok"}
    assert took < 0.5
