"""Tests for Slice 10: the real-AI measurements and the data they produced.

Two kinds of test:
- the measuring code in `bench/measure_ollama.py`, run against a pretend
  Ollama (never the real one): does it record the right numbers, skip the
  warm-ups, keep to "N at once", and use the agreed run order?
- the committed data files: are the 100 test texts within Kuyruk's rules, and
  can the fake AI replay the measured times that compose and the settings
  point at?
"""

import asyncio
import csv
import json
from pathlib import Path

import httpx
import pytest

from app.ai import FakeAI, read_replay_times
from app.settings import Settings, settings
from bench import measure_ollama
from bench.measure_ollama import (
    ROUNDS,
    RecordingTransport,
    Review,
    measure_one,
    run_at_once,
    run_everything,
    shuffled,
)

ROOT = Path(__file__).parent.parent
DATA = ROOT / "bench" / "data"


class PretendOllama:
    """Answers like Ollama's chat address, after a short real wait.

    Reports the text's length as its input tokens and 7 output tokens, and
    remembers which texts it saw and how many were being answered at once.
    """

    def __init__(self, wait: float = 0.01) -> None:
        """`wait` is how long each answer takes, in seconds."""
        self.wait = wait
        self.texts: list[str] = []
        self.busy = 0
        self.most_busy = 0

    async def handle(self, request: httpx.Request) -> httpx.Response:
        """One chat request: wait, then reply with a summary and token counts."""
        text = json.loads(request.content)["messages"][1]["content"]
        self.texts.append(text)
        self.busy += 1
        self.most_busy = max(self.most_busy, self.busy)
        await asyncio.sleep(self.wait)
        self.busy -= 1
        return httpx.Response(
            200,
            json={
                "message": {"content": "Özet."},
                "prompt_eval_count": len(text),
                "eval_count": 7,
            },
        )

    def transport(self) -> RecordingTransport:
        """A recording transport whose requests go to this pretend Ollama."""
        return RecordingTransport(httpx.MockTransport(self.handle))


REVIEWS = [Review(n, f"Yorum {n}: " + "çok güzel " * n) for n in range(1, 11)]

pytestmark = pytest.mark.anyio


async def test_measure_one_records_tokens_seconds_and_characters():
    """Tokens come from Ollama's reply; characters are the text as written."""
    ollama = PretendOllama(wait=0.05)
    result = await measure_one(REVIEWS[2], ollama.transport)
    assert result.id == 3
    assert result.characters == len(REVIEWS[2].text)
    assert result.input_tokens == len(REVIEWS[2].text)
    assert result.output_tokens == 7
    assert 0.05 <= result.seconds < 1


async def test_emails_and_phones_are_hidden_before_the_ai_sees_them():
    """The measured path is Kuyruk's: prepare_for_ai runs first (D50)."""
    ollama = PretendOllama()
    review = Review(1, "Yazın: ali@example.com veya 0555 000 12 34")
    result = await measure_one(review, ollama.transport)
    assert ollama.texts == ["Yazın: [EMAIL] veya [TELEFON]"]
    # The character count is of the text as the user wrote it.
    assert result.characters == len(review.text)


async def test_recording_transport_passes_a_non_json_reply_on_unchanged():
    """A reply that isn't JSON is still handed back; nothing is recorded."""

    async def broken(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, text="bad gateway")

    transport = RecordingTransport(httpx.MockTransport(broken))
    async with httpx.AsyncClient(transport=transport) as client:
        response = await client.get("http://ollama/")
    assert response.status_code == 502
    assert response.text == "bad gateway"
    assert transport.reply == {}


@pytest.mark.parametrize("texts_at_once", [1, 2, 4, 8])
async def test_run_at_once_never_has_more_than_n_with_the_ai(texts_at_once):
    """At most N texts are with the AI at any moment, and all N places are used."""
    ollama = PretendOllama()
    total, results = await run_at_once(REVIEWS, texts_at_once, ollama.transport)
    assert ollama.most_busy == texts_at_once
    assert sorted(r.id for r in results) == list(range(1, 11))
    assert total > 0


def test_shuffled_order_is_fixed_and_mixed():
    """The same shuffle every time (fixed seed), and not the original order."""
    assert [r.id for r in shuffled(REVIEWS)] == [r.id for r in shuffled(REVIEWS)]
    assert [r.id for r in shuffled(REVIEWS)] != [r.id for r in REVIEWS]


def test_rounds_interleave_every_setting_three_times():
    """Each round tries 1, 2, 4 and 8 once, in a different order (D91)."""
    assert len(ROUNDS) == 3
    assert all(sorted(order) == [1, 2, 4, 8] for order in ROUNDS)
    assert len({tuple(order) for order in ROUNDS}) == 3


def read_rows(path: Path) -> list[dict]:
    """A CSV file as a list of {column: value} rows."""
    with path.open(encoding="utf-8", newline="") as file:
        return list(csv.DictReader(file))


class MemoryClears:
    """A pretend memory-clearer: notes how many texts Ollama had seen by then."""

    def __init__(self, ollama: PretendOllama) -> None:
        """Watch this pretend Ollama."""
        self.ollama = ollama
        self.at: list[int] = []

    async def __call__(self) -> None:
        """Record the moment of the clear (as a count of texts seen so far)."""
        self.at.append(len(self.ollama.texts))


async def test_run_everything_writes_all_files_without_the_warm_ups(tmp_path):
    """Warm-ups are sent but not recorded; every table has the right rows."""
    ollama = PretendOllama(wait=0.001)
    clears = MemoryClears(ollama)
    await run_everything(
        REVIEWS, ollama.transport, clears, data=tmp_path, heat_up_seconds=0
    )

    # 13 runs (1 alone + 12 at once), each with 3 warm-ups and 10 texts.
    assert len(ollama.texts) == 13 * (3 + 10)
    # Every run starts with an empty memory, then the warm-ups.
    assert clears.at == [run * 13 for run in range(13)]
    for run in range(13):
        start = run * 13
        assert ollama.texts[start : start + 3] == measure_ollama.WARM_UP_TEXTS

    singles = read_rows(tmp_path / "ollama_times.csv")
    assert [int(r["id"]) for r in singles] == [r.id for r in shuffled(REVIEWS)]
    assert [int(r["order"]) for r in singles] == list(range(1, 11))
    assert set(singles[0]) == {
        "order",
        "id",
        "characters",
        "input_tokens",
        "output_tokens",
        "seconds",
    }

    runs = read_rows(tmp_path / "texts_at_once.csv")
    played = [int(r["texts_at_once"]) for r in runs]
    assert played == [n for order in ROUNDS for n in order]

    items = read_rows(tmp_path / "texts_at_once_items.csv")
    assert len(items) == 12 * 10

    # The replay file holds only the per-text times at 4 at once, 3 rounds.
    replay = read_rows(tmp_path / "ollama_times_at_4.csv")
    assert len(replay) == 3 * 10
    assert read_replay_times(str(tmp_path / "ollama_times_at_4.csv"))


def test_parallel_setting_is_read_from_the_latest_ollama_start(tmp_path):
    """Ollama logs its settings at every start; the last one counts."""
    log = tmp_path / "server.log"
    log.write_text("OLLAMA_NUM_PARALLEL:1 ...\nlater\nOLLAMA_NUM_PARALLEL:8 ...\n")
    assert measure_ollama.ollama_parallel_setting(log) == 8
    assert measure_ollama.ollama_parallel_setting(tmp_path / "missing.log") is None
    log.write_text("nothing here")
    assert measure_ollama.ollama_parallel_setting(log) is None


# ---- The committed data files ------------------------------------------------


def test_the_100_test_texts_follow_kuyruks_rules():
    """100 texts, unique ids, none empty or over the limit, 34/33/33 by length."""
    rows = read_rows(DATA / "reviews_tr.csv")
    assert len(rows) == 100
    assert len({row["id"] for row in rows}) == 100
    lengths = [len(row["text"]) for row in rows]
    assert min(lengths) > 0
    assert max(lengths) <= settings.max_text_chars
    short = sum(length <= 200 for length in lengths)
    medium = sum(200 < length <= 800 for length in lengths)
    assert (short, medium, 100 - short - medium) == (34, 33, 33)


@pytest.mark.parametrize(
    ("name", "count"),
    [
        ("ollama_times.csv", 100),
        ("ollama_times_cool.csv", 100),
        ("ollama_times_at_4.csv", 300),
    ],
)
def test_measured_times_are_valid_replay_data(name, count):
    """Kuyruk's own reader accepts both files; every time is above zero."""
    times = read_replay_times(str(DATA / name))
    assert len(times) == count
    assert all(seconds > 0 for seconds in times)


def test_settings_and_compose_replay_the_times_at_4_at_once(monkeypatch):
    """The default replay file is the 4-at-once times (D93), in both places."""
    # Settings() with no environment value gives the default.
    monkeypatch.delenv("FAKE_AI_REPLAY_FILE", raising=False)
    default = Settings().fake_ai_replay_file
    assert default == "bench/data/ollama_times_at_4.csv"
    assert f"FAKE_AI_REPLAY_FILE:-{default}}}" in (ROOT / "compose.yaml").read_text()
    # The fake AI starts with it (it reads the file at once).
    fake = FakeAI(
        wait_mode="replay",
        wait_seconds=0,
        replay_file=str(ROOT / default),
        seed=1,
        answer="Özet.",
        fail_every=0,
        fail_kind="temporary",
    )
    assert fake.next_wait() > 0


def test_the_placeholder_times_are_gone():
    """The ten guessed times (D82) were replaced by measurements."""
    assert not (DATA / "placeholder_times.csv").exists()
    assert "placeholder_times" not in (ROOT / "compose.yaml").read_text()


async def test_at_once_only_keeps_the_one_at_a_time_file(tmp_path):
    """Redoing only the "at once" runs leaves ollama_times.csv untouched."""
    kept = tmp_path / "ollama_times.csv"
    kept.write_text("order,id,characters,input_tokens,output_tokens,seconds\n")
    ollama = PretendOllama(wait=0.001)
    clears = MemoryClears(ollama)
    await run_everything(
        REVIEWS,
        ollama.transport,
        clears,
        data=tmp_path,
        one_at_a_time=False,
        heat_up_seconds=0,
    )
    assert len(clears.at) == 12
    assert len(ollama.texts) == 12 * (3 + 10)
    assert kept.read_text().count("\n") == 1
    assert len(read_rows(tmp_path / "texts_at_once.csv")) == 12


async def test_one_at_a_time_only_keeps_the_at_once_files(tmp_path):
    """Redoing only the one-at-a-time run leaves the "at once" files alone."""
    kept = tmp_path / "texts_at_once.csv"
    kept.write_text("round,texts_at_once,total_seconds,started_at\n")
    ollama = PretendOllama(wait=0.001)
    clears = MemoryClears(ollama)
    await run_everything(
        REVIEWS,
        ollama.transport,
        clears,
        data=tmp_path,
        at_once=False,
        heat_up_seconds=0,
    )
    assert clears.at == [0]
    assert len(read_rows(tmp_path / "ollama_times.csv")) == 10
    assert kept.read_text().count("\n") == 1


async def test_heat_up_keeps_the_ai_busy_first_and_records_nothing(tmp_path):
    """Texts are sent during the heat-up, before the memory is emptied."""
    ollama = PretendOllama(wait=0.01)
    clears = MemoryClears(ollama)
    await run_everything(
        REVIEWS,
        ollama.transport,
        clears,
        data=tmp_path,
        at_once=False,
        heat_up_seconds=0.1,
    )
    heated = clears.at[0]
    assert heated >= 3
    # After the heat-up: 3 warm-ups, then exactly the 10 measured texts.
    assert len(ollama.texts) == heated + 3 + 10
    assert len(read_rows(tmp_path / "ollama_times.csv")) == 10


def test_the_two_only_options_cannot_be_combined():
    """Both together would measure nothing; the script refuses at once."""
    with pytest.raises(SystemExit):
        measure_ollama.parse_arguments(["--at-once-only", "--one-at-a-time-only"])
    assert measure_ollama.parse_arguments(["--at-once-only"]).at_once_only


@pytest.mark.parametrize(
    "broken",
    [httpx.ConnectError("off"), ValueError("not JSON"), KeyError("models")],
)
def test_setup_check_explains_a_missing_or_strange_ollama(monkeypatch, broken):
    """No answer, or an answer that isn't Ollama's: a plain message, no crash."""

    def model_digest() -> str:
        raise broken

    monkeypatch.setattr(measure_ollama, "model_digest", model_digest)
    assert measure_ollama.check_setup() == [
        "Ollama doesn't answer. Open the Ollama app and try again."
    ]


def test_new_setup_notes_never_erase_earlier_ones(monkeypatch, tmp_path):
    """A full rerun adds a dated section; the earlier notes stay."""
    monkeypatch.setattr(measure_ollama, "command_output", lambda *command: "")
    monkeypatch.setattr(measure_ollama, "model_digest", lambda: "abc")
    monkeypatch.setattr(measure_ollama, "ollama_parallel_setting", lambda: 8)
    monkeypatch.setattr(
        measure_ollama.httpx,
        "get",
        lambda *args, **kwargs: httpx.Response(200, json={"version": "0.40.15"}),
    )
    notes = tmp_path / "setup.md"
    measure_ollama.write_setup_notes(notes)
    assert notes.read_text().startswith("# Setup notes")
    notes.write_text(notes.read_text() + "\n## Run history\nwritten by hand\n")
    measure_ollama.write_setup_notes(notes)
    text = notes.read_text()
    assert "written by hand" in text
    assert text.count("## New full run") == 1
    assert text.count("| Ollama version | 0.40.15 |") == 2


def test_setup_check_can_leave_out_the_charger(monkeypatch):
    """The one-command rerun checks the charger itself (no repeated line)."""
    monkeypatch.setattr(
        measure_ollama, "model_digest", lambda: measure_ollama.MODEL_DIGEST
    )
    monkeypatch.setattr(measure_ollama, "ollama_parallel_setting", lambda: 8)
    monkeypatch.setattr(measure_ollama, "plugged_in", lambda: False)
    assert measure_ollama.check_setup() == [measure_ollama.CHARGER_PROBLEM]
    assert measure_ollama.check_setup(charger=False) == []
