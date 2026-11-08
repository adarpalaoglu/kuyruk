"""Experiment E1's load test: how much a slow step freezes the API (spec 9.1).

HAND-DESIGNED by Adar (D96-D98): what it sends and what it measures.

It starts one Kuyruk server (uvicorn, one process) on this Mac with the
experiment addresses switched on (`app/experiments.py`) and a throwaway
database. Then, run after run:

1. it sends all slow requests at the same moment ("time zero"): 50 (or 100)
   requests to one of the three ways of writing the slow step, A, B or C;
2. every 100 ms, on a fixed clock, it sends one request to the real `/health`
   and one to the experiment's `ping`, *without waiting* for earlier ones to
   answer. A stuck server therefore can't hide by answering slowly: a probe
   that waited for the previous one would skip the worst moments (D96);
3. it stops probing once the last slow request has answered, and waits for
   every probe still out;
4. it records every probe's time (from sending to its full answer) and every
   slow request's finish time (from time zero). Errors and timeouts are
   recorded as errors, never dropped.

Two parts:
- **sleep part** (the main experiment): the slow step is a plain wait of
  0.5 / 1 / 2 / 4 s. Settings A50, B50, C50 and B100 (100 slow requests:
  more than FastAPI's ~40 helper threads). 5 repeats; each repeat runs all
  16 setting-and-wait pairs in its own fixed shuffled order (D97).
- **AI part** (the real-AI check): the slow step is a real call to Ollama,
  10 requests per run, 3 modes x 5 repeats, interleaved. A 10-minute
  heat-up first (D94); Ollama's memory emptied and 3 warm-ups before every
  run (D89); the model's digest checked (D92) (D98).

It measures Kuyruk, not itself (Adar's safeguard): the client has no
connection limit (no probe waits on our side for a free connection), every
timeout is 900 s (longer than the longest run, so the worst waits are
measured, not cut off), the probes are sent from their own process (so a
burst of slow replies can't delay them), the server's connection queue
(backlog) is 4096 and macOS's own cap on it is checked, and the open-files
limit is raised to 10,240 for this script and the server. Every probe
records when it was meant to leave and when it really left; any probe more
than 20 ms late is reported.
All of this is written to `bench/data/setup.md`.

Results: `bench/data/e1_runs.csv` (one row per run), `e1_probes.csv` (every
probe) and `e1_slow.csv` (every slow request); rows are marked `kind`
`sleep` or `ai`. Charts: `uv run --group bench python -m bench.charts`.

Run from the project folder, plugged in, Docker closed, Mac left alone
(about an hour; `caffeinate` keeps the Mac awake):

    caffeinate -dims uv run python -m bench.e1_load

`--sleep-only` / `--ai-only` redo one part and keep the other part's rows.
`--after-fix` is the confirmation run after /health became `async def` (D99):
B50 and B100 at a 1 s wait, 5 repeats, interleaved, rows marked `after_fix`.
`--trial OUT_DIR` does a quick check (one repeat, 0.5 s only, no heat-up)
and writes its files to OUT_DIR instead of `bench/data/`.
"""

import argparse
import asyncio
import csv
import multiprocessing
import os
import platform
import random
import resource
import secrets
import statistics
import subprocess
import sys
import tempfile
import time
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, fields
from datetime import datetime
from importlib.metadata import version
from pathlib import Path

import httpx

from app.normalise import prepare_for_ai
from app.settings import settings
from bench import measure_ollama
from bench.measure_ollama import (
    HEAT_UP_SECONDS,
    RecordingTransport,
    clear_ollama_memory,
    command_output,
    fresh_start,
    heat_up,
    load_reviews,
    shuffled,
)

DATA = Path(__file__).parent / "data"

# The numbers below are the experiment's design (D96-D98), not settings of
# Kuyruk, so they live here and not in app/settings.py: if they could be
# changed through the environment, a rerun could silently use different
# values and still look comparable to the published results.

# What runs (D97). A setting is a mode (A, B or C) and a number of slow
# requests sent at once.
WAITS = [0.5, 1.0, 2.0, 4.0]
SETTINGS = [("a", 50), ("b", 50), ("c", 50), ("b", 100)]
REPEATS = 5
# Each repeat's order comes from this seed plus the repeat number, so the
# order is mixed but the same every time the script runs.
ORDER_SEED = 11

# The confirmation run after /health became `async def` (D99): the two B
# settings, where /health had waited for a helper thread, at a 1 s wait.
AFTER_FIX_SETTINGS = [("b", 50), ("b", 100)]

# The real-AI check (D98).
AI_MODES = ["a", "b", "c"]
AI_REQUESTS = 10

# How long the probe process may take to start and warm up, in seconds.
PROBE_START_TIMEOUT = 60

# How the probes are sent (D96).
PROBE_EVERY = 0.1  # seconds between two probe ticks
PROBES = {"health": "/health", "ping": "/experiments/e1/ping"}
LATE_WARNING_MS = 20  # report any probe that left later than this

# Rest between two runs, so one run's leftovers can't spill into the next.
REST_SECONDS = 2.0

# The safeguards: measure Kuyruk, not the load test.
TIMEOUT_SECONDS = 900.0  # longer than the longest run (A50 at 4 s, ~200 s)
BACKLOG = 4096  # the server's queue of connections not yet picked up
OPEN_FILES = 10_240  # every open connection uses one "file" on macOS
PORT = 8011  # not 8000, so a normal Kuyruk on this Mac can't be hit by mistake
KEEP_ALIVE_SECONDS = 60  # the server keeps idle connections open this long

# The server command. One process, asyncio loop and h11 (uvicorn's plain
# defaults without extra packages), written out so the notes show it.
SERVER_COMMAND = [
    sys.executable,
    "-m",
    "uvicorn",
    "app.main:app",
    "--host",
    "127.0.0.1",
    "--port",
    str(PORT),
    "--workers",
    "1",
    "--loop",
    "asyncio",
    "--http",
    "h11",
    "--backlog",
    str(BACKLOG),
    "--timeout-keep-alive",
    str(KEEP_ALIVE_SECONDS),
    "--no-access-log",
]


@dataclass
class Probe:
    """One /health or ping request. Times in seconds from the run's time zero.

    `planned` is when it was meant to leave (a multiple of 0.1 s), `sent`
    when it really left, `seconds` how long it took to answer (or to fail).
    """

    target: str
    planned: float
    sent: float
    seconds: float
    ok: bool
    error: str


@dataclass
class Slow:
    """One slow request: when it finished, from time zero, and how long the
    server says its slow step took (the wait, or the AI's answer)."""

    index: int
    finished: float
    server_seconds: float | None
    ok: bool
    error: str


@dataclass
class Run:
    """Everything measured in one run."""

    probes: list[Probe]
    slow: list[Slow]


# Sends slow request number `index` with the given client.
SlowSender = Callable[[httpx.AsyncClient, int], Awaitable[httpx.Response]]


def make_client(base_url: str, transport=None) -> httpx.AsyncClient:
    """The load test's web client: no connection limit, 900 s timeouts.

    `max_connections=None` means "no limit": every probe opens its own
    connection at once if no idle one is free, so it never queues on our side.
    Tests pass a pretend server as `transport`.
    """
    return httpx.AsyncClient(
        base_url=base_url,
        timeout=TIMEOUT_SECONDS,
        limits=httpx.Limits(max_connections=None, max_keepalive_connections=None),
        transport=transport,
    )


def clock() -> float:
    """Seconds on the Mac's steady clock, the same in every process.

    The probes are sent from their own process (see `run_split`), so both
    processes must count time on one shared clock. CLOCK_MONOTONIC is the
    system-wide steady clock: it never jumps when the time of day is changed.
    """
    return time.clock_gettime(time.CLOCK_MONOTONIC)


async def sleep_until(moment: float) -> None:
    """Wait until `clock()` reaches `moment` (at once if it already has)."""
    delay = moment - clock()
    if delay > 0:
        await asyncio.sleep(delay)


async def probe_until(
    client: httpx.AsyncClient,
    start: float,
    should_stop: Callable[[], bool],
    probe_every: float = PROBE_EVERY,
) -> list[Probe]:
    """Probe /health and ping on a fixed clock from `start` until told to stop.

    Ticks are planned at 0, 0.1, 0.2 ... s after `start` (a fixed clock: one
    late tick never pushes the later ones back). Each tick starts one probe
    per target as its own task, so a probe never waits for an earlier one.
    At the first tick where `should_stop()` is true, no more probes leave;
    probes still out are awaited, so their full time is measured (D96).
    """

    async def probe(target: str, planned: float) -> Probe:
        """Send one probe; record when it left and how long it took."""
        sent = clock() - start
        try:
            response = await client.get(PROBES[target])
            response.raise_for_status()
            ok, error = True, ""
        except httpx.HTTPError as problem:
            ok, error = False, type(problem).__name__
        return Probe(target, planned, sent, clock() - start - sent, ok, error)

    tasks = []
    tick = 0
    while True:
        planned = tick * probe_every
        await sleep_until(start + planned)
        if should_stop():
            break
        for target in PROBES:
            tasks.append(asyncio.create_task(probe(target, planned)))
        tick += 1
    return list(await asyncio.gather(*tasks))


async def send_all_slow(
    client: httpx.AsyncClient, send_slow: SlowSender, count: int, start: float
) -> list[Slow]:
    """Send `count` slow requests at `start`, all at once; record each finish."""

    async def one_slow(index: int) -> Slow:
        """Send one slow request; record when it finished, from `start`."""
        try:
            response = await send_slow(client, index)
            response.raise_for_status()
            body = response.json()
            server_seconds = body.get("waited_seconds", body.get("seconds"))
            return Slow(index, clock() - start, server_seconds, True, "")
        except (httpx.HTTPError, ValueError) as error:
            return Slow(index, clock() - start, None, False, type(error).__name__)

    await sleep_until(start)
    return list(await asyncio.gather(*(one_slow(i) for i in range(count))))


async def run_once(
    client: httpx.AsyncClient,
    send_slow: SlowSender,
    count: int,
    probe_every: float = PROBE_EVERY,
) -> Run:
    """A whole run inside one process: the slow requests and the probes.

    Used by the tests (with a pretend server). The real runs use `run_split`,
    which sends the probes from their own process.
    """
    start = clock()
    slow_task = asyncio.create_task(send_all_slow(client, send_slow, count, start))
    probes = await probe_until(client, start, slow_task.done, probe_every)
    return Run(probes=probes, slow=await slow_task)


def probe_process(url: str, start_pipe, stop) -> None:
    """The probe process: wait for the start time, probe until `stop` is set,
    then send every probe back through `start_pipe`.

    It runs in its own process with its own event loop and web client, so a
    burst of slow replies arriving in the main process (e.g. 40 helper
    threads finishing together in mode B) can't make a probe leave late or
    look slow. Found in the trial run: in one process, a probe left 82 ms
    late at such a burst.
    """

    async def probe_from_here() -> list[Probe]:
        """Open a client, warm it up, report ready, get the start time, probe."""
        async with make_client(url) as client:
            # One unrecorded probe of each kind first: the client's one-time
            # setup on its first request took ~20 ms in the trial and made the
            # first real probe leave late.
            for path in PROBES.values():
                await client.get(path)
            start_pipe.send("ready")
            start = start_pipe.recv()
            return await probe_until(client, start, stop.is_set)

    start_pipe.send(asyncio.run(probe_from_here()))


async def run_split(url: str, send_slow: SlowSender, count: int) -> Run:
    """A real run: slow requests from this process, probes from another.

    The probe process is started fresh for every run (with a new client, so
    every run starts with no open connections). Time zero is set a moment
    in the future, so both processes are ready and waiting when it comes.
    """
    context = multiprocessing.get_context("spawn")
    here, there = context.Pipe()
    stop = context.Event()
    prober = context.Process(target=probe_process, args=(url, there, stop))
    prober.start()
    # Only the probe process keeps its end of the pipe. If it dies, the pipe
    # then reports "closed" (EOFError) here instead of waiting forever.
    there.close()
    try:
        # Waiting for "ready" blocks this process, which is fine: nothing
        # runs yet. A probe process that isn't ready within a minute is stuck.
        if not here.poll(PROBE_START_TIMEOUT) or here.recv() != "ready":
            raise RuntimeError("The probe process did not start.")
        start = clock() + 0.2
        here.send(start)
        async with make_client(url) as client:
            slow = await send_all_slow(client, send_slow, count, start)
        stop.set()
        # The probes come back once every probe still out has answered;
        # waiting in a helper thread keeps this process's loop free
        # meanwhile. At most the probe timeout (plus a margin), never forever.
        if not await asyncio.to_thread(here.poll, TIMEOUT_SECONDS + 60):
            raise RuntimeError("The probe process sent no probes back.")
        probes = here.recv()
    except EOFError as error:
        raise RuntimeError("The probe process stopped unexpectedly.") from error
    finally:
        prober.join(timeout=10)
        if prober.is_alive():
            prober.terminate()
    return Run(probes=probes, slow=slow)


def percentile(values: list[float], share: float) -> float:
    """The value below which `share` of the values lie (e.g. 0.95 → p95).

    The "inclusive" method, as in `bench/charts.py`: the smallest value is the
    0th percentile and the largest the 100th, straight lines in between.
    """
    if len(values) == 1:
        return values[0]
    cuts = statistics.quantiles(values, n=100, method="inclusive")
    return cuts[round(share * 100) - 1]


def summarise(run: Run) -> dict:
    """The one-line summary of a run for `e1_runs.csv`.

    Probe percentiles are over the probes that answered; failed probes and
    slow requests are counted in `errors`. Lateness is how much later than
    planned a probe really left (the clock check).
    """
    row: dict = {}
    for target in PROBES:
        times = [p.seconds * 1000 for p in run.probes if p.target == target and p.ok]
        row[f"{target}_probes"] = sum(p.target == target for p in run.probes)
        row[f"{target}_p50_ms"] = round(percentile(times, 0.50), 2) if times else ""
        row[f"{target}_p95_ms"] = round(percentile(times, 0.95), 2) if times else ""
        row[f"{target}_max_ms"] = round(max(times), 2) if times else ""
    finishes = sorted(s.finished for s in run.slow if s.ok)
    row["slow_first_finish_s"] = round(finishes[0], 3) if finishes else ""
    row["slow_median_finish_s"] = (
        round(statistics.median(finishes), 3) if finishes else ""
    )
    row["slow_last_finish_s"] = round(finishes[-1], 3) if finishes else ""
    step = [s.server_seconds for s in run.slow if s.ok and s.server_seconds is not None]
    row["server_step_median_s"] = round(statistics.median(step), 3) if step else ""
    row["errors"] = sum(not p.ok for p in run.probes) + sum(not s.ok for s in run.slow)
    late = [(p.sent - p.planned) * 1000 for p in run.probes]
    row["max_probe_late_ms"] = round(max(late), 2) if late else 0
    return row


# ---- What runs, in which order ---------------------------------------------


@dataclass
class Planned:
    """One planned run: which repeat, its place in that repeat, and what."""

    repeat: int
    order: int
    mode: str
    count: int
    wait: float | None  # None for the AI part


def label(mode: str, count: int) -> str:
    """A setting's short name, e.g. "B100"."""
    return f"{mode.upper()}{count}"


def sleep_schedule(
    repeats: int = REPEATS,
    waits: list[float] = WAITS,
    settings_run: list[tuple[str, int]] = SETTINGS,
) -> list[Planned]:
    """Every setting-and-wait pair once per repeat, each repeat shuffled (D97)."""
    plan = []
    for repeat in range(1, repeats + 1):
        pairs = [(m, c, w) for m, c in settings_run for w in waits]
        random.Random(ORDER_SEED + repeat).shuffle(pairs)
        for order, (mode, count, wait) in enumerate(pairs, start=1):
            plan.append(Planned(repeat, order, mode, count, wait))
    return plan


def ai_schedule(repeats: int = REPEATS) -> list[Planned]:
    """The three AI modes once per repeat, each repeat shuffled (D98)."""
    plan = []
    for repeat in range(1, repeats + 1):
        modes = list(AI_MODES)
        random.Random(ORDER_SEED + 100 + repeat).shuffle(modes)
        for order, mode in enumerate(modes, start=1):
            plan.append(Planned(repeat, order, mode, AI_REQUESTS, None))
    return plan


# ---- Saving -------------------------------------------------------------------

RUN_KEYS = ["kind", "setting", "mode", "slow_requests", "wait_seconds", "repeat"]
RUN_COLUMNS = (
    [*RUN_KEYS, "order_in_repeat", "started_at"]
    + [
        f"{target}_{part}"
        for target in PROBES
        for part in ["probes", "p50_ms", "p95_ms", "max_ms"]
    ]
    + [
        "slow_first_finish_s",
        "slow_median_finish_s",
        "slow_last_finish_s",
        "server_step_median_s",
        "errors",
        "max_probe_late_ms",
    ]
)
PROBE_COLUMNS = [*RUN_KEYS, *(f.name for f in fields(Probe))]
SLOW_COLUMNS = [*RUN_KEYS, *(f.name for f in fields(Slow))]
FILES = {"runs": "e1_runs.csv", "probes": "e1_probes.csv", "slow": "e1_slow.csv"}
COLUMNS = {"runs": RUN_COLUMNS, "probes": PROBE_COLUMNS, "slow": SLOW_COLUMNS}


def start_part(out: Path, kind: str) -> None:
    """Start a part afresh: remove its old rows, keep the other part's rows."""
    out.mkdir(parents=True, exist_ok=True)
    for name, file_name in FILES.items():
        path = out / file_name
        kept = []
        if path.exists():
            with path.open(encoding="utf-8", newline="") as file:
                kept = [row for row in csv.DictReader(file) if row["kind"] != kind]
        with path.open("w", encoding="utf-8", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=COLUMNS[name])
            writer.writeheader()
            writer.writerows(kept)


def rounded(row: dict) -> dict:
    """Times rounded to 0.1 ms, so the files stay readable."""
    return {k: round(v, 4) if isinstance(v, float) else v for k, v in row.items()}


def save_run(out: Path, kind: str, planned: Planned, started: str, run: Run) -> dict:
    """Add one run to the three files (after every run, so nothing measured
    is lost if the script stops early). Returns the run's summary row."""
    keys = {
        "kind": kind,
        "setting": label(planned.mode, planned.count),
        "mode": planned.mode.upper(),
        "slow_requests": planned.count,
        "wait_seconds": "" if planned.wait is None else planned.wait,
        "repeat": planned.repeat,
    }
    summary = {
        **keys,
        "order_in_repeat": planned.order,
        "started_at": started,
        **summarise(run),
    }
    rows = {
        "runs": [summary],
        "probes": [{**keys, **rounded(asdict(p))} for p in run.probes],
        "slow": [{**keys, **rounded(asdict(s))} for s in run.slow],
    }
    for name, file_name in FILES.items():
        with (out / file_name).open("a", encoding="utf-8", newline="") as file:
            csv.DictWriter(file, fieldnames=COLUMNS[name]).writerows(rows[name])
    return summary


def report(summary: dict) -> None:
    """Print one line per run, and warn loudly about errors or late probes."""
    print(
        f"  {summary['setting']:>5} wait {summary['wait_seconds'] or 'AI':>4}"
        f"  /health p50 {summary['health_p50_ms']} ms, p95 {summary['health_p95_ms']}"
        f" ms; ping p95 {summary['ping_p95_ms']} ms; last slow done at "
        f"{summary['slow_last_finish_s']} s"
    )
    if summary["errors"]:
        print(f"  WARNING: {summary['errors']} error(s) in this run.")
    if summary["max_probe_late_ms"] > LATE_WARNING_MS:
        print(
            f"  WARNING: a probe left {summary['max_probe_late_ms']} ms late "
            f"(limit {LATE_WARNING_MS} ms): the load test itself was slow."
        )


# ---- The two parts --------------------------------------------------------------


def base_url() -> str:
    """The test server's address."""
    return f"http://127.0.0.1:{PORT}"


async def warm_up_server() -> None:
    """A few requests of every kind, not recorded, so first-time costs
    (imports, opening the database, connections) don't land in a run."""
    async with make_client(base_url()) as client:
        for _ in range(3):
            for mode in "abc":
                await asyncio.gather(
                    *(
                        client.get(f"/experiments/e1/sleep-{mode}?seconds=0.05")
                        for _ in range(5)
                    )
                )
            await asyncio.gather(
                *(client.get(path) for path in PROBES.values() for _ in range(20))
            )


async def run_sleep_part(out: Path, plan: list[Planned], kind: str = "sleep") -> None:
    """The main experiment: waits of 0.5-4 s, written three ways (D97).

    `kind` labels the rows: `sleep` for the main experiment, `after_fix` for
    the confirmation run after /health became `async def` (D99).
    """
    start_part(out, kind)
    await warm_up_server()
    for number, planned in enumerate(plan, start=1):

        async def send(client: httpx.AsyncClient, index: int, p=planned):
            """One slow request of this run's mode and wait."""
            return await client.get(
                f"/experiments/e1/sleep-{p.mode}", params={"seconds": p.wait}
            )

        print(f"Run {number}/{len(plan)} (repeat {planned.repeat}):")
        started = datetime.now().astimezone().isoformat(timespec="seconds")
        run = await run_split(base_url(), send, planned.count)
        report(save_run(out, kind, planned, started, run))
        await asyncio.sleep(REST_SECONDS)


async def run_ai_part(out: Path, plan: list[Planned], heat_up_seconds: float) -> None:
    """The real-AI check: 10 texts through Ollama, three ways (D98)."""
    reviews = load_reviews()
    # The same 10 texts every run, cleaned exactly as the worker cleans them.
    texts = [prepare_for_ai(r.text) for r in shuffled(reviews)[:AI_REQUESTS]]
    start_part(out, "ai")
    if heat_up_seconds:
        await heat_up(reviews, RecordingTransport, heat_up_seconds)
    for number, planned in enumerate(plan, start=1):

        async def send(client: httpx.AsyncClient, index: int, p=planned):
            """One AI request of this run's mode, with text number `index`."""
            return await client.post(
                f"/experiments/e1/ai-{p.mode}", json={"text": texts[index]}
            )

        # Empty Ollama's memory of earlier texts, then 3 warm-ups (D89).
        await fresh_start(RecordingTransport, clear_ollama_memory)
        print(f"AI run {number}/{len(plan)} (repeat {planned.repeat}):")
        started = datetime.now().astimezone().isoformat(timespec="seconds")
        run = await run_split(base_url(), send, planned.count)
        report(save_run(out, "ai", planned, started, run))
        await asyncio.sleep(REST_SECONDS)


# ---- The server and the Mac's limits ----------------------------------------------


def raise_open_files_limit(wanted: int = OPEN_FILES) -> int:
    """Raise this script's open-files limit (the server inherits it).

    Every open connection counts as one open file. macOS's usual limit for a
    program started from the terminal is 256, far below the ~2,000 probes
    that can be waiting at once in mode A. Returns the new limit.
    """
    _, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    target = wanted if hard == resource.RLIM_INFINITY else min(wanted, hard)
    resource.setrlimit(resource.RLIMIT_NOFILE, (target, hard))
    return resource.getrlimit(resource.RLIMIT_NOFILE)[0]


def somaxconn() -> int:
    """macOS's cap on any server's connection queue (`kern.ipc.somaxconn`).

    A server asking for a longer queue (backlog) silently gets this cap
    instead; with the usual 128, connections over the cap wait or fail in the
    Mac's network layer, which would be measured as if Kuyruk were slow.
    """
    value = command_output("sysctl", "-n", "kern.ipc.somaxconn")
    return int(value) if value.isdigit() else 0


def check_limits() -> list[str]:
    """The safeguards that must hold before measuring; returns what's wrong."""
    problems = []
    if raise_open_files_limit() < OPEN_FILES:
        problems.append(f"Could not raise the open-files limit to {OPEN_FILES}.")
    if somaxconn() < BACKLOG:
        problems.append(
            f"macOS caps the server's connection queue at {somaxconn()}; "
            f"{BACKLOG} needed. Type: ! sudo sysctl -w kern.ipc.somaxconn="
            f"{BACKLOG}  (it asks for your Mac password; a restart undoes it)."
        )
    return problems


@contextmanager
def kuyruk_server(folder: Path) -> Iterator[None]:
    """Start one Kuyruk server for the experiment; stop it afterwards.

    Experiments on, demo mode off, a throwaway SQLite database in `folder`
    (E1 never touches the database), and a throwaway login key (no logins
    happen). Its log goes to `folder/server.log` and is shown if it fails
    to start.
    """
    environment = {
        **os.environ,
        "EXPERIMENTS_ENABLED": "true",
        "DEMO_MODE": "false",
        "DATABASE_URL": f"sqlite:///{folder / 'e1.db'}",
        "JWT_SECRET": secrets.token_urlsafe(48),
    }
    log = (folder / "server.log").open("w")
    server = subprocess.Popen(SERVER_COMMAND, env=environment, stdout=log, stderr=log)
    try:
        for _ in range(100):
            try:
                if httpx.get(f"{base_url()}/experiments/e1/ping").status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.1)
        else:
            log.flush()
            raise RuntimeError(
                "The server did not start. Its log:\n"
                + (folder / "server.log").read_text()[-2000:]
            )
        yield
    finally:
        server.terminate()
        server.wait(timeout=30)
        log.close()


def setup_notes(
    ai: bool, heat_up_seconds: float, title: str = "E1 — blocking the event loop"
) -> str:
    """The E1 setup notes (spec 9 and Adar's safeguard) as a Markdown section."""
    hardware = command_output("system_profiler", "SPHardwareDataType")

    def hardware_field(name: str) -> str:
        """One line of the Mac's hardware report, e.g. "Chip"."""
        for line in hardware.splitlines():
            if line.strip().startswith(f"{name}:"):
                return line.split(":", 1)[1].strip()
        return "unknown"

    power = command_output("pmset", "-g", "batt").splitlines()
    commit = command_output("git", "rev-parse", "HEAD")
    changed = command_output("git", "status", "--porcelain")
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    hard_text = "unlimited" if hard == resource.RLIM_INFINITY else hard
    when = datetime.now().astimezone().isoformat(timespec="seconds")
    mac = f"{hardware_field('Model Name')} ({hardware_field('Model Identifier')})"
    lines = [
        "",
        f"## {title} (Slice 11)",
        "",
        "Written automatically by `bench/e1_load.py` when the run started.",
        "",
        "| | |",
        "|---|---|",
        f"| Date and time | {when} |",
        f"| Mac | {mac}, {hardware_field('Chip')}, {hardware_field('Memory')} |",
        f"| macOS | {platform.mac_ver()[0]} |",
        f"| Power | {power[0] if power else 'unknown'} |",
        f"| Python, FastAPI, uvicorn, httpx | {platform.python_version()}, "
        f"{version('fastapi')}, {version('uvicorn')}, {version('httpx')} |",
        f"| Kuyruk commit | `{commit}`"
        f"{' plus uncommitted Slice 11 work' if changed else ''} |",
        f"| Server | one uvicorn process: `{' '.join(SERVER_COMMAND[1:])}` |",
        f"| Server connection queue (backlog) | {BACKLOG}; macOS cap "
        f"`kern.ipc.somaxconn` = {somaxconn()} |",
        f"| Open-files limit | {soft} (hard: {hard_text}), for the load test "
        "and the server (inherited) |",
        "| Client connection limit | none (`max_connections=None`) |",
        f"| Timeouts (probes and slow requests) | {TIMEOUT_SECONDS:g} s |",
        f"| Probes | /health and ping every {PROBE_EVERY * 1000:g} ms on a fixed "
        f"clock; warning if one leaves > {LATE_WARNING_MS} ms late |",
        "| Probe process | its own process and client, started fresh each run |",
        f"| Rest between runs | {REST_SECONDS:g} s; new clients every run |",
        "| Warm-up | 3 rounds of 5 slow requests per mode (0.05 s) and 20 of "
        "each probe, not recorded |",
        "| Other apps | Docker Desktop closed; other heavy apps closed |",
    ]
    if ai:
        reply = httpx.get(f"{settings.ollama_url}/api/version", timeout=10).json()
        lines += [
            f"| Ollama version, model | {reply.get('version', 'unknown')}, "
            f"{settings.ollama_model} (digest `{measure_ollama.model_digest()}`) |",
            f"| Ollama parallel setting | "
            f"{measure_ollama.ollama_parallel_setting()} (D88) |",
            f"| AI part | heat-up {heat_up_seconds:g} s (D94); memory emptied and "
            f"3 warm-ups before every run (D89); {AI_REQUESTS} texts per run |",
        ]
    return "\n".join(lines) + "\n"


def parse_arguments(arguments: list[str] | None = None) -> argparse.Namespace:
    """Read the command-line options (None: the ones the script was run with)."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    only = parser.add_mutually_exclusive_group()
    only.add_argument("--sleep-only", action="store_true", help="only the sleep part")
    only.add_argument("--ai-only", action="store_true", help="only the AI part")
    parser.add_argument(
        "--trial",
        metavar="OUT_DIR",
        type=Path,
        help="a quick check (1 repeat, 0.5 s, no heat-up) written to OUT_DIR",
    )
    only.add_argument(
        "--after-fix",
        action="store_true",
        help="the confirmation run after /health became async def (D99): "
        "B50 and B100 at 1 s, 5 repeats, rows marked after_fix",
    )
    return parser.parse_args(arguments)


def main() -> None:
    """Check the safeguards and setup, write the notes, run, save."""
    arguments = parse_arguments()
    sleep = not arguments.ai_only
    ai = not (arguments.sleep_only or arguments.after_fix)
    kind, title = "sleep", "E1 — blocking the event loop"
    out = arguments.trial or DATA
    problems = check_limits()
    if ai:
        problems += measure_ollama.check_setup()
    if problems:
        print("Not measuring:", *problems, sep="\n- ")
        sys.exit(1)
    if arguments.after_fix:
        sleep_plan = sleep_schedule(waits=[1.0], settings_run=AFTER_FIX_SETTINGS)
        ai_plan, heat = [], 0.0
        kind, title = "after_fix", "E1 confirmation after /health became async def"
    elif arguments.trial:
        sleep_plan = sleep_schedule(repeats=1, waits=[0.5])
        ai_plan, heat = ai_schedule(repeats=1), 0.0
    else:
        sleep_plan, ai_plan, heat = sleep_schedule(), ai_schedule(), HEAT_UP_SECONDS
    out.mkdir(parents=True, exist_ok=True)
    with (out / "setup.md").open("a", encoding="utf-8") as file:
        file.write(setup_notes(ai, heat, title))
    with tempfile.TemporaryDirectory() as folder, kuyruk_server(Path(folder)):
        if sleep:
            asyncio.run(run_sleep_part(out, sleep_plan, kind))
        if ai:
            asyncio.run(run_ai_part(out, ai_plan, heat))
    print(f"Done. Results are in {out}/.")


if __name__ == "__main__":
    main()
