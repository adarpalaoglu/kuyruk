"""Tests for the fake AI in `app/ai.py`, and for the AI_BACKEND switch.

The fake is given a pretend `sleep` that only records how long it was asked to
wait, so these tests check the waits without really waiting. `pytest.mark.anyio`
lets a test be an `async` function (anyio comes with FastAPI).
"""

from pathlib import Path

import pytest

from app import ai
from app.ai import (
    AISetupError,
    FakeAI,
    OllamaAI,
    PermanentAIError,
    TemporaryAIError,
    get_ai_backend,
)

pytestmark = pytest.mark.anyio

SAMPLE_TIMES = str(Path(__file__).parent / "data" / "sample_times.csv")


class RecordedWaits(list):
    """A pretend sleep: remembers each wait instead of waiting."""

    async def __call__(self, seconds: float) -> None:
        self.append(seconds)


def make_fake(**changes) -> FakeAI:
    """A fake AI with simple test defaults; `changes` overrides any of them."""
    options = {
        "wait_mode": "fixed",
        "wait_seconds": 0.5,
        "replay_file": SAMPLE_TIMES,
        "seed": 42,
        "answer": "Kısa bir özet.",
        "fail_every": 0,
        "fail_kind": "temporary",
        "sleep": RecordedWaits(),
    } | changes
    return FakeAI(**options)


async def test_fixed_mode_waits_then_returns_the_fixed_answer():
    waits = RecordedWaits()
    fake = make_fake(sleep=waits)

    assert await fake.summarise("Ürün güzel.") == "Kısa bir özet."
    assert await fake.summarise("Kargo geç geldi.") == "Kısa bir özet."
    assert waits == [0.5, 0.5]


async def test_real_waiting_uses_async_sleep():
    fake = make_fake(wait_seconds=0.01, sleep=ai.asyncio.sleep)

    assert await fake.summarise("metin") == "Kısa bir özet."


async def test_replay_draws_waits_from_the_file():
    waits = RecordedWaits()
    fake = make_fake(wait_mode="replay", sleep=waits)

    for _ in range(20):
        await fake.summarise("metin")

    assert set(waits) <= {1.5, 2.0, 2.5, 3.0, 4.0}
    assert len(set(waits)) > 1  # really drawn, not one value repeated


async def replay_waits(seed: int, calls: int = 20) -> list[float]:
    """The waits a fresh replay-mode fake with this seed asks for."""
    waits = RecordedWaits()
    fake = make_fake(wait_mode="replay", seed=seed, sleep=waits)
    for _ in range(calls):
        await fake.summarise("metin")
    return waits


async def test_replay_is_repeatable_with_the_same_seed():
    assert await replay_waits(seed=7) == await replay_waits(seed=7)


async def test_replay_differs_with_another_seed():
    assert await replay_waits(seed=7) != await replay_waits(seed=8)


async def test_never_fails_by_default():
    fake = make_fake()

    for _ in range(10):
        await fake.summarise("metin")


async def test_fails_every_nth_call():
    fake = make_fake(fail_every=3)
    outcomes = []
    for _ in range(7):
        try:
            await fake.summarise("metin")
            outcomes.append("ok")
        except TemporaryAIError:
            outcomes.append("failed")

    assert outcomes == ["ok", "ok", "failed", "ok", "ok", "failed", "ok"]


async def test_fail_every_one_means_every_call_fails():
    fake = make_fake(fail_every=1)

    for _ in range(5):
        with pytest.raises(TemporaryAIError):
            await fake.summarise("metin")


async def test_failures_can_be_permanent():
    fake = make_fake(fail_every=1, fail_kind="permanent")

    with pytest.raises(PermanentAIError, match="permanent"):
        await fake.summarise("metin")


async def test_it_waits_before_failing():
    waits = RecordedWaits()
    fake = make_fake(fail_every=1, sleep=waits)

    with pytest.raises(TemporaryAIError):
        await fake.summarise("metin")
    assert waits == [0.5]


@pytest.mark.parametrize(
    ("contents", "message"),
    [
        ("seconds\n", "at least one time"),
        ("seconds\n1.0\n-2.0\n", "none negative"),
        ("time\n1.0\n", "seconds` column"),
        ("seconds\nfast\n", "seconds` column"),
    ],
)
def test_bad_replay_file_is_refused(tmp_path, contents, message):
    path = tmp_path / "times.csv"
    path.write_text(contents)

    with pytest.raises(AISetupError, match=message):
        make_fake(wait_mode="replay", replay_file=str(path))


def test_missing_replay_file_is_refused(tmp_path):
    with pytest.raises(AISetupError, match="missing"):
        make_fake(wait_mode="replay", replay_file=str(tmp_path / "none.csv"))


def test_fixed_mode_does_not_need_the_replay_file(tmp_path):
    make_fake(wait_mode="fixed", replay_file=str(tmp_path / "none.csv"))


# --- The AI_BACKEND switch ---------------------------------------------------


def test_switch_gives_the_fake_ai(monkeypatch):
    monkeypatch.setattr(ai.settings, "ai_backend", "fake")
    monkeypatch.setattr(ai.settings, "fake_ai_fail_every", 4)

    backend = get_ai_backend()

    assert isinstance(backend, FakeAI)
    assert backend.fail_every == 4


def test_switch_gives_the_fixed_answer_outside_demo_mode(monkeypatch):
    monkeypatch.setattr(ai.settings, "ai_backend", "fake")
    monkeypatch.setattr(ai.settings, "demo_mode", False)
    assert get_ai_backend().demo_words == 0


def test_switch_gives_the_demo_answer_in_demo_mode(monkeypatch):
    monkeypatch.setattr(ai.settings, "ai_backend", "fake")
    monkeypatch.setattr(ai.settings, "demo_mode", True)
    monkeypatch.setattr(ai.settings, "fake_ai_demo_words", 3)
    assert get_ai_backend().demo_words == 3


async def test_demo_answer_starts_with_demo_and_the_texts_first_words():
    waits = RecordedWaits()
    fake = make_fake(demo_words=6, sleep=waits)
    answer = await fake.summarise(
        "Kargo çok hızlı geldi, paketleme özenliydi, ürün fotoğraftaki gibi."
    )
    assert answer == "Demo: Kargo çok hızlı geldi, paketleme özenliydi,…"
    assert await fake.summarise("Fena değil.") == "Demo: Fena değil."
    # The waits are exactly those of the plain fake AI.
    assert waits == [0.5, 0.5]


async def test_demo_answers_differ_per_text_but_wait_the_same_as_replay():
    plain = make_fake(wait_mode="replay", sleep=RecordedWaits())
    demo = make_fake(wait_mode="replay", demo_words=6, sleep=RecordedWaits())
    for text in ["bir", "iki üç", "dört beş altı"]:
        await plain.summarise(text)
        await demo.summarise(text)
    assert plain.sleep == demo.sleep


def test_demo_answer_marks_left_out_words():
    assert ai.demo_answer("a b c", 2) == "Demo: a b…"
    assert ai.demo_answer("  a   b  ", 2) == "Demo: a b"


def test_switch_gives_the_ollama_ai(monkeypatch):
    monkeypatch.setattr(ai.settings, "ai_backend", "ollama")
    monkeypatch.setattr(ai.settings, "ollama_model", "gemma4:e4b")

    backend = get_ai_backend()

    assert isinstance(backend, OllamaAI)
    assert backend.model == "gemma4:e4b"
