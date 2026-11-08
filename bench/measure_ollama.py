"""Measure the real AI: how long Gemma on Ollama takes per text (spec 9.0).

Slice 10. Kuyruk's fake AI replays real waiting times (spec 2.4, D06), so the
demo and experiments E1/E2 behave like the real AI without needing it. This
script makes those times. It sends the 100 synthetic reviews in
`bench/data/reviews_tr.csv` to Ollama through Kuyruk's own path: first
`prepare_for_ai` (hides emails and phone numbers), then Kuyruk's own Ollama
client (`OllamaAI`), exactly as the worker does.

What it does, in order:
1. checks the setup: Ollama answers, the model is the pinned one (D92), Ollama
   serves at least 8 requests at once (D88), and the Mac is plugged in; then
   writes the setup notes (`bench/data/setup.md`);
2. keeps the AI busy for 10 minutes first, so the Mac is warm, as in a long
   real job (nothing recorded); then, before every measured run, empties
   Ollama's memory of earlier texts and sends 3 warm-up requests that are not
   recorded (D89);
3. sends the 100 texts one at a time, in a shuffled order (D91), and writes
   `bench/data/ollama_times.csv`;
4. sends the same 100 texts 1, 2, 4 and 8 at once, 3 times each, in an
   interleaved order (D91), and writes `bench/data/texts_at_once.csv` (one row
   per run), `bench/data/texts_at_once_items.csv` (one row per text) and
   `bench/data/ollama_times_at_4.csv` (the per-text times at 4 at once, which
   the fake AI replays by default, D93).

Any AI error stops the whole run: a measurement with failures in it should be
redone, not half-counted.

Run from the project folder, plugged in, with `caffeinate` so the Mac doesn't
fall asleep in the middle (it takes about 50 minutes):

    caffeinate -dims uv run python -m bench.measure_ollama

`--at-once-only` redoes only step 4 and keeps `ollama_times.csv`;
`--one-at-a-time-only` redoes only step 3 and keeps the step 4 files.

Before that, `uv run python -m bench.measure_ollama --check-parallel` shows
whether Ollama really answers several requests at once: 8 short requests sent
together finish clearly faster than 8 sent one after another only if it does.
On this Mac it doesn't (D88).
"""

import argparse
import asyncio
import csv
import json
import platform
import random
import re
import subprocess
import sys
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import httpx

from app.ai import OllamaAI
from app.normalise import prepare_for_ai
from app.settings import settings

DATA = Path(__file__).parent / "data"
REVIEWS_FILE = DATA / "reviews_tr.csv"
SETUP_FILE = DATA / "setup.md"
ONE_AT_A_TIME_FILE = DATA / "ollama_times.csv"
RUNS_FILE = DATA / "texts_at_once.csv"
RUN_ITEMS_FILE = DATA / "texts_at_once_items.csv"
REPLAY_AT_4_FILE = DATA / "ollama_times_at_4.csv"

# The exact model measured (D92). A model "digest" is a fingerprint of its
# files: if the model under the name gemma4:e4b is ever replaced, the digest
# changes and this script refuses to measure, so all numbers come from one model.
MODEL_DIGEST = "eb2177adf7646d15faead94fdad6d3e170dc8015653418a3b7d5d52a6380be5b"

# Ollama's own log, where it writes its settings each time it starts. Its
# parallel setting (how many requests it answers at once) is only visible here.
OLLAMA_LOG = Path.home() / ".ollama" / "logs" / "server.log"
PARALLEL_NEEDED = 8  # the most texts at once measured below (D88)

# Fixed seed for shuffling the 100 texts, so a rerun uses the same order (D91).
SHUFFLE_SEED = 42

# Three throwaway texts sent first (D89). Not among the 100: Ollama remembers
# the last text it read and would answer a repeat of it faster.
WARM_UP_TEXTS = [
    "Ürün güzel, paketleme de özenliydi.",
    "Kargo biraz geç geldi ama satıcı ilgiliydi, sorun çözüldü.",
    "Bu termosu her gün kullanıyorum, çayı saatlerce sıcak tutuyor.",
]

# The interleaved order of the 12 "texts at once" runs (D91): each round tries
# every setting once, in a different order, so a slow drift (the fanless Mac
# warming up, background tasks) doesn't land on one setting only.
ROUNDS = [[1, 2, 4, 8], [8, 4, 2, 1], [2, 8, 1, 4]]

# How long the AI is kept busy before measuring, so every number comes from
# a fully warm Mac (found in Slice 10; see `heat_up`). 3 minutes was not
# enough: the Mac kept slowing for about 5-6 minutes of steady work.
HEAT_UP_SECONDS = 600

# Which setting's per-text times the fake AI replays by default: the worker's
# TEXTS_AT_ONCE default (D93).
REPLAY_AT = 4


@dataclass
class Review:
    """One of the 100 test texts: its id and the text as written."""

    id: int
    text: str


@dataclass
class Measurement:
    """What was measured for one text in one run.

    `seconds` is clock time around Kuyruk's own client call, i.e. what a worker
    waits for this text (D90). The token counts are Ollama's own: input tokens
    (`prompt_eval_count`, the instruction plus the text) and output tokens
    (`eval_count`, the summary it wrote).
    """

    id: int
    characters: int
    input_tokens: int
    output_tokens: int
    seconds: float


class RecordingTransport(httpx.AsyncBaseTransport):
    """Passes requests on to Ollama and keeps a copy of the reply.

    Kuyruk's `OllamaAI` returns only the summary, but the measurement also
    needs the token counts in Ollama's reply. `OllamaAI` accepts a "transport"
    (the part of the web library that actually sends a request), so this one
    "listens in": it sends the request as normal, reads the reply, remembers
    it, and hands an identical reply back. Kuyruk's code is unchanged (D90).
    """

    def __init__(self, inner: httpx.AsyncBaseTransport | None = None) -> None:
        """`inner` really sends the requests; tests pass a pretend Ollama."""
        self.inner = inner or httpx.AsyncHTTPTransport()
        self.reply: dict = {}

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        """Send the request, keep the reply's contents, return a fresh copy."""
        response = await self.inner.handle_async_request(request)
        body = await response.aread()
        await response.aclose()
        try:
            self.reply = json.loads(body)
        except ValueError:
            self.reply = {}
        # The body is already unpacked, so the copy must not claim to be packed
        # (content-encoding) or keep the old length.
        headers = [
            (name, value)
            for name, value in response.headers.multi_items()
            if name.lower() not in ("content-encoding", "content-length")
        ]
        return httpx.Response(response.status_code, headers=headers, content=body)

    async def aclose(self) -> None:
        """Close the real connection when Kuyruk's client is done with it."""
        await self.inner.aclose()


# Makes the transport for one call. Normally a real one; tests pass a maker
# whose transports answer like Ollama without Ollama running.
TransportMaker = Callable[[], RecordingTransport]


def make_client(transport: RecordingTransport) -> OllamaAI:
    """Kuyruk's own Ollama client, with the same settings the worker uses."""
    return OllamaAI(
        url=settings.ollama_url,
        model=settings.ollama_model,
        temperature=settings.ollama_temperature,
        seed=settings.ollama_seed,
        timeout_seconds=settings.ollama_timeout_seconds,
        transport=transport,
    )


def load_reviews(path: Path = REVIEWS_FILE) -> list[Review]:
    """Read the test texts (columns `id`, `text`)."""
    with path.open(encoding="utf-8", newline="") as file:
        return [Review(int(row["id"]), row["text"]) for row in csv.DictReader(file)]


def shuffled(reviews: list[Review]) -> list[Review]:
    """The texts in a fixed random order, so length isn't tied to time (D91)."""
    order = list(reviews)
    random.Random(SHUFFLE_SEED).shuffle(order)
    return order


async def measure_one(review: Review, make_transport: TransportMaker) -> Measurement:
    """Send one text through Kuyruk's path and time the AI call.

    A new client per call, as the worker's `OllamaAI` does anyway (it opens a
    new connection for every text). Hiding emails and phones happens before the
    clock starts: the worker does it too, and it takes microseconds.
    """
    transport = make_transport()
    client = make_client(transport)
    prepared = prepare_for_ai(review.text)
    start = time.perf_counter()
    await client.summarise(prepared)
    seconds = time.perf_counter() - start
    return Measurement(
        id=review.id,
        characters=len(review.text),
        input_tokens=int(transport.reply.get("prompt_eval_count", 0)),
        output_tokens=int(transport.reply.get("eval_count", 0)),
        seconds=round(seconds, 4),
    )


async def warm_up(make_transport: TransportMaker) -> None:
    """Send the warm-up texts one by one and throw the results away (D89).

    The first request makes Ollama load the model into memory (several
    seconds): a one-off cost, not a text's real time.
    """
    for number, text in enumerate(WARM_UP_TEXTS, start=1):
        result = await measure_one(Review(-number, text), make_transport)
        print(f"  warm-up {number}: {result.seconds:.2f} s (not recorded)")


async def run_one_at_a_time(
    reviews: list[Review], make_transport: TransportMaker
) -> list[Measurement]:
    """Send every text alone, one after another, in the shuffled order."""
    results = []
    for review in shuffled(reviews):
        result = await measure_one(review, make_transport)
        results.append(result)
        print(
            f"  {len(results):3}/{len(reviews)} id {review.id:3}: "
            f"{result.seconds:6.2f} s, {result.characters} characters"
        )
    return results


async def run_at_once(
    reviews: list[Review], texts_at_once: int, make_transport: TransportMaker
) -> tuple[float, list[Measurement]]:
    """Send all texts with at most `texts_at_once` with the AI at any moment.

    Like the worker: as soon as one text is done, the next one starts. A
    "semaphore" is a counter of free places; a text waits for a free place
    before it starts and gives it back when done. Returns the total time and
    each text's own time (measured only while it was with the AI).
    """
    places = asyncio.Semaphore(texts_at_once)

    async def one(review: Review) -> Measurement:
        async with places:
            return await measure_one(review, make_transport)

    start = time.perf_counter()
    results = await asyncio.gather(*(one(review) for review in shuffled(reviews)))
    return round(time.perf_counter() - start, 4), list(results)


def write_csv(path: Path, header: list[str], rows: list[list]) -> None:
    """Write one result table."""
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(header)
        writer.writerows(rows)


MEASUREMENT_COLUMNS = ["id", "characters", "input_tokens", "output_tokens", "seconds"]


def measurement_row(result: Measurement) -> list:
    """One `Measurement` as a table row, in MEASUREMENT_COLUMNS order."""
    return [
        result.id,
        result.characters,
        result.input_tokens,
        result.output_tokens,
        result.seconds,
    ]


async def clear_ollama_memory() -> None:
    """Unload the model from Ollama, which wipes its memory of earlier texts.

    Ollama's engine keeps a "prefix cache": it remembers texts it has already
    read, and when the same text comes again it skips reading it and only
    writes the summary. Real jobs bring new texts, so every measured run must
    start with an empty memory, or repeated runs of the same 100 texts measure
    remembered texts (about 25% faster; found in Slice 10). Asking Ollama to
    keep the model loaded for 0 seconds unloads it; this waits until it's gone.
    The next request loads it again, which the warm-up pays for.
    """
    async with httpx.AsyncClient(base_url=settings.ollama_url, timeout=60) as ollama:
        await ollama.post(
            "/api/generate", json={"model": settings.ollama_model, "keep_alive": 0}
        )
        for _ in range(60):
            loaded = (await ollama.get("/api/ps")).json().get("models", [])
            if not any(m["name"] == settings.ollama_model for m in loaded):
                return
            await asyncio.sleep(1)
    raise RuntimeError("Ollama did not unload the model within 60 seconds.")


# Clears the AI's memory before a run. Normally `clear_ollama_memory`; tests
# pass a pretend one that only counts the calls.
MemoryClearer = Callable[[], Awaitable[None]]


async def fresh_start(make_transport: TransportMaker, clear: MemoryClearer) -> None:
    """Empty Ollama's memory of earlier texts, then warm up again (D89, D91)."""
    await clear()
    await warm_up(make_transport)


async def heat_up(
    reviews: list[Review], make_transport: TransportMaker, seconds: float
) -> None:
    """Keep the AI busy for `seconds` before measuring; nothing is recorded.

    A fanless MacBook Air slows down by about a third once it is warm (found
    in Slice 10: 132 s for the 100 texts on a cool Mac, then 179-184 s). A long
    real job runs on a warm Mac, so every measurement starts warm. The heat-up
    sends the test texts one after another; the memory of them is emptied
    before the measured runs (D89).
    """
    end = time.perf_counter() + seconds
    sent = 0
    while time.perf_counter() < end:
        await measure_one(reviews[sent % len(reviews)], make_transport)
        sent += 1
    print(f"Heat-up: {sent} texts in {seconds:.0f} s (not recorded)")


async def run_everything(
    reviews: list[Review],
    make_transport: TransportMaker,
    clear: MemoryClearer = clear_ollama_memory,
    data: Path = DATA,
    one_at_a_time: bool = True,
    at_once: bool = True,
    heat_up_seconds: float = HEAT_UP_SECONDS,
) -> None:
    """Heat up, measure one at a time, then all 12 "at once" runs; save CSVs.

    Every run starts fresh: Ollama's memory of earlier texts is emptied and 3
    warm-up requests are sent. `one_at_a_time=False` or `at_once=False` skips
    that part and keeps its files (used to redo one part only). `data` is
    where the CSVs go (tests use a temporary folder).
    """
    await heat_up(reviews, make_transport, heat_up_seconds)
    if one_at_a_time:
        print("One at a time:")
        await fresh_start(make_transport, clear)
        singles = await run_one_at_a_time(reviews, make_transport)
        write_csv(
            data / ONE_AT_A_TIME_FILE.name,
            ["order", *MEASUREMENT_COLUMNS],
            [[order, *measurement_row(r)] for order, r in enumerate(singles, start=1)],
        )

    if not at_once:
        return
    runs, items, replay = [], [], []
    for round_number, settings_order in enumerate(ROUNDS, start=1):
        for texts_at_once in settings_order:
            await fresh_start(make_transport, clear)
            started_at = datetime.now().astimezone().isoformat(timespec="seconds")
            total, results = await run_at_once(reviews, texts_at_once, make_transport)
            print(
                f"Round {round_number}, {texts_at_once} at once: {total:.1f} s "
                f"for {len(results)} texts"
            )
            runs.append([round_number, texts_at_once, total, started_at])
            for r in results:
                items.append([round_number, texts_at_once, *measurement_row(r)])
                if texts_at_once == REPLAY_AT:
                    replay.append([round_number, r.id, r.seconds])
    write_csv(
        data / RUNS_FILE.name,
        ["round", "texts_at_once", "total_seconds", "started_at"],
        runs,
    )
    write_csv(
        data / RUN_ITEMS_FILE.name,
        ["round", "texts_at_once", *MEASUREMENT_COLUMNS],
        items,
    )
    # The fake AI reads only the `seconds` column (Slice 4).
    write_csv(data / REPLAY_AT_4_FILE.name, ["round", "id", "seconds"], replay)


# ---- Setup checks and notes -------------------------------------------------


def command_output(*command: str) -> str:
    """Run a Mac command and return what it printed ("" if it failed)."""
    try:
        return subprocess.run(
            command, capture_output=True, text=True, check=True
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return ""


def ollama_parallel_setting(log: Path = OLLAMA_LOG) -> int | None:
    """Ollama's parallel setting at its latest start, read from its log.

    Ollama writes `OLLAMA_NUM_PARALLEL:<n>` among its settings each time it
    starts; the last one is the one in use. None if it can't be found.
    """
    try:
        found = re.findall(r"OLLAMA_NUM_PARALLEL:(\d+)", log.read_text(errors="ignore"))
    except OSError:
        return None
    return int(found[-1]) if found else None


def model_digest() -> str:
    """The digest of the installed model with Kuyruk's model name."""
    reply = httpx.get(f"{settings.ollama_url}/api/tags", timeout=10).json()
    for model in reply["models"]:
        if model["name"] == settings.ollama_model:
            return model["digest"]
    return ""


def plugged_in() -> bool:
    """True if the Mac is running on the charger, not the battery."""
    return "AC Power" in command_output("pmset", "-g", "batt")


def check_setup() -> list[str]:
    """Everything that must be true before measuring; returns what's wrong."""
    problems = []
    try:
        digest = model_digest()
    # No answer at all (HTTPError), or an answer that isn't Ollama's list of
    # models (not JSON, or JSON of another shape): something else may be using
    # Ollama's address. Either way, say so plainly instead of crashing.
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        return ["Ollama doesn't answer. Open the Ollama app and try again."]
    if digest != MODEL_DIGEST:
        problems.append(
            f"The model {settings.ollama_model} is not the pinned one "
            f"(found digest {digest or 'none'}, expected {MODEL_DIGEST})."
        )
    parallel = ollama_parallel_setting()
    if parallel is None or parallel < PARALLEL_NEEDED:
        problems.append(
            f"Ollama answers {parallel} request(s) at once; {PARALLEL_NEEDED} "
            "needed. Run `launchctl setenv OLLAMA_NUM_PARALLEL 8`, then quit "
            "and reopen the Ollama app."
        )
    if not plugged_in():
        problems.append("The Mac is on battery. Plug in the charger.")
    return problems


def write_setup_notes(path: Path = SETUP_FILE) -> None:
    """Write the setup notes (spec 9) from what the Mac and Ollama report.

    If notes already exist, a new dated section is added at the end instead:
    earlier notes (run history, findings written by hand) are never erased.
    """
    hardware = command_output("system_profiler", "SPHardwareDataType")

    def hardware_field(name: str) -> str:
        """One line of the Mac's hardware report, e.g. "Chip"."""
        match = re.search(rf"{name}: (.+)", hardware)
        return match.group(1).strip() if match else "unknown"

    power = command_output("pmset", "-g", "batt").splitlines()
    version = httpx.get(f"{settings.ollama_url}/api/version", timeout=10).json()
    commit = command_output("git", "rev-parse", "HEAD")
    # Uncommitted changes mean the code measured is the commit plus this
    # slice's work in progress; say so rather than hide it.
    changed = command_output("git", "status", "--porcelain")
    temperature, seed = settings.ollama_temperature, settings.ollama_seed
    warm_ups = len(WARM_UP_TEXTS)
    table = f"""| | |
|---|---|
| Date and time | {datetime.now().astimezone().isoformat(timespec="seconds")} |
| Mac | {hardware_field("Model Name")} ({hardware_field("Model Identifier")}) |
| Chip | {hardware_field("Chip")} |
| Memory | {hardware_field("Memory")} |
| macOS | {platform.mac_ver()[0]} |
| Power | {power[0] if power else "unknown"} |
| Ollama version | {version.get("version", "unknown")} |
| Model | {settings.ollama_model} |
| Model digest | `{model_digest()}` |
| Ollama parallel setting | {ollama_parallel_setting()} (`OLLAMA_NUM_PARALLEL`, D88) |
| Temperature, seed, thinking | {temperature}, {seed}, off |
| Kuyruk commit | `{commit}`{" plus uncommitted Slice 10 work" if changed else ""} |
| Heat-up, warm-up | {HEAT_UP_SECONDS} s (D94); {warm_ups} per run (D89) |
| Other apps | Docker Desktop closed; other heavy apps closed |

The parallel setting was made with `launchctl setenv OLLAMA_NUM_PARALLEL 8`
and an Ollama restart. The Mac forgets it after a restart of the Mac.
"""
    if path.exists():
        with path.open("a", encoding="utf-8") as file:
            file.write(f"\n## New full run (notes written when it started)\n\n{table}")
        return
    path.write_text(
        "# Setup notes — real AI measurements (Slice 10)\n\n"
        "Written automatically by `bench/measure_ollama.py` when the measurement\n"
        "started. Spec 9 asks every experiment to record these.\n\n" + table,
        encoding="utf-8",
    )


def add_rerun_note(what: str, path: Path = SETUP_FILE) -> None:
    """Record in the setup notes that one part was redone, when, and how."""
    power = command_output("pmset", "-g", "batt").splitlines()
    with path.open("a", encoding="utf-8") as file:
        file.write(
            f"""
## {what} redone

Started {datetime.now().astimezone().isoformat(timespec="seconds")} with a
{HEAT_UP_SECONDS} s heat-up first, and Ollama's memory emptied before each run.
Power: {power[0] if power else "unknown"}. Ollama parallel setting:
{ollama_parallel_setting()}. Same model digest (checked).
"""
        )


async def check_parallel(make_transport: TransportMaker) -> None:
    """Show whether Ollama really answers several requests at the same time.

    8 short texts one after another, then the same 8 together. If Ollama
    serves them in parallel, together is clearly faster; if it only queues
    them, both take about as long.
    """
    texts = [Review(-n, WARM_UP_TEXTS[n % 3]) for n in range(1, 9)]
    await warm_up(make_transport)
    start = time.perf_counter()
    for review in texts:
        await measure_one(review, make_transport)
    one_by_one = time.perf_counter() - start
    together, _ = await run_at_once(texts, 8, make_transport)
    print(f"8 one after another: {one_by_one:.1f} s; 8 together: {together:.1f} s")
    print(f"Together is {one_by_one / together:.1f}x faster.")


def parse_arguments(arguments: list[str] | None = None) -> argparse.Namespace:
    """Read the command-line options (None: the ones the script was run with).

    The two "only" options can't be combined: together they would skip both
    parts, measure nothing, and still note a rerun in the setup notes.
    """
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    only = parser.add_mutually_exclusive_group()
    only.add_argument(
        "--at-once-only",
        action="store_true",
        help="redo only the texts-at-once runs; keep ollama_times.csv",
    )
    only.add_argument(
        "--one-at-a-time-only",
        action="store_true",
        help="redo only the one-at-a-time run; keep the texts-at-once files",
    )
    parser.add_argument(
        "--check-parallel",
        action="store_true",
        help="only show whether Ollama answers several requests at once",
    )
    return parser.parse_args(arguments)


def main() -> None:
    """Check the setup, write the notes, and run the measurements."""
    arguments = parse_arguments()
    if arguments.check_parallel:
        asyncio.run(check_parallel(RecordingTransport))
        return
    problems = check_setup()
    if problems:
        print("Not measuring:", *problems, sep="\n- ")
        sys.exit(1)
    if arguments.at_once_only:
        add_rerun_note("Texts-at-once runs")
    elif arguments.one_at_a_time_only:
        add_rerun_note("One-at-a-time run")
    else:
        write_setup_notes()
    asyncio.run(
        run_everything(
            load_reviews(),
            RecordingTransport,
            one_at_a_time=not arguments.at_once_only,
            at_once=not arguments.one_at_a_time_only,
        )
    )
    print("Done. Results are in bench/data/.")


if __name__ == "__main__":
    main()
