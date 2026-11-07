"""Tests for the Ollama client in `app/ai.py`, with Ollama replaced by a pretend one.

The real Ollama is never called here. httpx's `MockTransport` is a pretend web
server: every request the client sends goes to a small function in this file,
which can look at the request and answer like Ollama would (or fail like it).
"""

import json
import socket

import httpx
import pytest

from app.ai import INSTRUCTION, OllamaAI, PermanentAIError, TemporaryAIError

pytestmark = pytest.mark.anyio


def make_ollama(handler, **changes) -> OllamaAI:
    """An Ollama client whose requests are answered by `handler`."""
    options = {
        "url": "http://ollama.test:11434",
        "model": "gemma4:e4b",
        "temperature": 0.0,
        "seed": 42,
        "timeout_seconds": 5.0,
        "transport": httpx.MockTransport(handler),
    } | changes
    return OllamaAI(**options)


def reply(content: str) -> httpx.Response:
    """A successful Ollama chat reply containing `content`."""
    return httpx.Response(
        200,
        json={
            "model": "gemma4:e4b",
            "message": {"role": "assistant", "content": content},
        },
    )


async def test_summary_is_returned_without_extra_spaces():
    ollama = make_ollama(lambda request: reply("  Ürün çok beğenilmiş.\n"))

    assert await ollama.summarise("Ürün harika!") == "Ürün çok beğenilmiş."


async def test_request_has_the_model_instruction_text_and_fixed_options():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return reply("Özet.")

    await make_ollama(handler, seed=7, temperature=0.0).summarise("Kargo geç geldi.")

    request = seen[0]
    body = json.loads(request.content)
    assert request.method == "POST"
    assert str(request.url) == "http://ollama.test:11434/api/chat"
    assert body["model"] == "gemma4:e4b"
    assert body["messages"] == [
        {"role": "system", "content": INSTRUCTION},
        {"role": "user", "content": "Kargo geç geldi."},
    ]
    assert body["think"] is False
    assert body["stream"] is False
    assert body["options"] == {"temperature": 0.0, "seed": 7}


def test_instruction_is_the_fixed_sentence():
    assert INSTRUCTION == (
        "Summarise this in one Turkish sentence. Reply with only that sentence."
    )


# --- Temporary errors ---------------------------------------------------------


async def test_timeout_is_temporary():
    def handler(request):
        raise httpx.ReadTimeout("too slow", request=request)

    with pytest.raises(TemporaryAIError, match="too long"):
        await make_ollama(handler).summarise("metin")


async def test_connect_timeout_is_temporary():
    def handler(request):
        raise httpx.ConnectTimeout("no answer", request=request)

    with pytest.raises(TemporaryAIError):
        await make_ollama(handler).summarise("metin")


async def test_connection_refused_is_temporary():
    # A real request to a port on this computer where nothing listens, so the
    # computer itself refuses the connection, as when Ollama is switched off.
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        free_port = probe.getsockname()[1]
    ollama = make_ollama(None, url=f"http://127.0.0.1:{free_port}", transport=None)

    with pytest.raises(TemporaryAIError, match="Is Ollama running"):
        await ollama.summarise("metin")


@pytest.mark.parametrize("status", [429, 500, 502, 503])
async def test_server_errors_are_temporary(status):
    ollama = make_ollama(lambda request: httpx.Response(status, text="busy"))

    with pytest.raises(TemporaryAIError, match=f"HTTP {status}"):
        await ollama.summarise("metin")


@pytest.mark.parametrize(
    "broken_off",
    [
        httpx.RemoteProtocolError("peer closed connection mid-reply"),
        httpx.ReadError("connection reset"),
    ],
)
async def test_reply_that_breaks_off_halfway_is_temporary(broken_off):
    def handler(request):
        raise broken_off

    with pytest.raises(TemporaryAIError, match="broke off halfway"):
        await make_ollama(handler).summarise("metin")


# --- Permanent errors ---------------------------------------------------------


@pytest.mark.parametrize("status", [400, 404, 422])
async def test_other_error_replies_are_permanent(status):
    ollama = make_ollama(
        lambda request: httpx.Response(status, json={"error": "model not found"})
    )

    with pytest.raises(PermanentAIError, match=f"HTTP {status}.*model not found"):
        await ollama.summarise("metin")


async def test_long_error_reply_is_cut_short():
    ollama = make_ollama(lambda request: httpx.Response(400, text="x" * 10_000))

    with pytest.raises(PermanentAIError) as caught:
        await ollama.summarise("metin")
    assert len(str(caught.value)) < 300


@pytest.mark.parametrize(
    "bad_reply",
    [
        httpx.Response(200, text="not json"),
        httpx.Response(200, json={"no_message": True}),
        httpx.Response(200, json={"message": None}),
        httpx.Response(200, json=["a", "list"]),
    ],
)
async def test_reply_in_an_unexpected_form_is_permanent(bad_reply):
    ollama = make_ollama(lambda request: bad_reply)

    with pytest.raises(PermanentAIError, match="expected form"):
        await ollama.summarise("metin")


async def test_empty_answer_is_permanent():
    ollama = make_ollama(lambda request: reply("   "))

    with pytest.raises(PermanentAIError, match="empty"):
        await ollama.summarise("metin")


async def test_other_connection_problems_are_permanent():
    def handler(request):
        raise httpx.UnsupportedProtocol("odd address", request=request)

    with pytest.raises(PermanentAIError, match="failed"):
        await make_ollama(handler).summarise("metin")
