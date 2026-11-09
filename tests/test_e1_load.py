"""Tests for E1's load test, `bench/e1_load.py` (D96-D98), against pretend
servers (never a real Kuyruk server or the real Ollama).

The most important one is the clock check (Adar's safeguard): probes leave
every 100 ms even while no earlier request has answered, so a stuck server
can't hide by answering slowly. The others: errors are recorded, never
dropped; the p50/p95 maths; the interleaved run order; the files; the limits
that make sure the test measures Kuyruk and not itself.
"""

import asyncio
import csv
import socket
import sys
import threading

import httpx
import pytest
import uvicorn
from fastapi import FastAPI

from bench import e1_load
from bench.e1_load import (
    PROBES,
    Planned,
    Probe,
    Run,
    Slow,
    ai_schedule,
    make_client,
    percentile,
    run_once,
    save_run,
    sleep_schedule,
    start_part,
    summarise,
)

pytestmark = pytest.mark.anyio


def frozen_server(release: asyncio.Event, broken: str = "") -> httpx.MockTransport:
    """A pretend server that answers *nothing* until `release` is set.

    Like a Kuyruk whose event loop is stuck (mode A). Requests to the path
    `broken` get a 500 instead, to test error recording.
    """

    async def handle(request: httpx.Request) -> httpx.Response:
        """Hold every request until released, then answer."""
        await release.wait()
        if broken and request.url.path == broken:
            return httpx.Response(500)
        return httpx.Response(200, json={"waited_seconds": 0.5})

    return httpx.MockTransport(handle)


async def slow_get(client: httpx.AsyncClient, index: int) -> httpx.Response:
    """A slow request to the pretend server."""
    return await client.get("/experiments/e1/sleep-a", params={"seconds": 0.5})


async def run_frozen(release_after: float, count: int = 3, broken: str = "") -> Run:
    """One run against a frozen server that is released after some seconds."""
    release = asyncio.Event()
    asyncio.get_running_loop().call_later(release_after, release.set)
    async with make_client("http://e1", frozen_server(release, broken)) as client:
        return await run_once(client, slow_get, count)


def frozen_until_probes(count: int) -> httpx.MockTransport:
    """A pretend server that answers nothing until it has received `count` probes.

    The freeze ends on what the server has *received*, not after a set time,
    so the test below doesn't depend on how busy the computer is: if the
    probes waited for answers, the probes it needs would never arrive.
    """
    release = asyncio.Event()
    received = 0

    async def handle(request: httpx.Request) -> httpx.Response:
        """Count the probes; hold every request until enough have arrived."""
        nonlocal received
        if request.url.path in PROBES.values():
            received += 1
            if received >= count:
                release.set()
        await release.wait()
        return httpx.Response(200, json={"waited_seconds": 0.5})

    return httpx.MockTransport(handle)


async def test_probes_leave_on_the_clock_while_nothing_answers():
    """Probes keep leaving every 100 ms although no earlier request has answered.

    The server stays frozen until the probes of 6 ticks (0, 0.1 ... 0.5 s)
    have all arrived. So the run can only finish if the probes left on their
    own clock without waiting for answers. The timeout turns a load test that
    waits for answers into a clear failure instead of a hang. (How late each
    probe leaves depends on how busy the computer is; that is checked in the
    real runs and recorded in `bench/data/setup.md`, not here.)
    """
    ticks = 6
    transport = frozen_until_probes(count=ticks * len(PROBES))
    async with asyncio.timeout(10):
        async with make_client("http://e1", transport) as client:
            run = await run_once(client, slow_get, 3)
    for target in PROBES:
        planned = [p.planned for p in run.probes if p.target == target]
        assert planned[:ticks] == pytest.approx([0.0, 0.1, 0.2, 0.3, 0.4, 0.5])
    # The first answer came only after the last frozen tick's probes had left:
    # all of them were sent while nothing had answered.
    first = run.probes[0]
    assert first.ok
    first_answer = first.sent + first.seconds
    frozen = [p for p in run.probes if p.planned < ticks * e1_load.PROBE_EVERY - 0.05]
    assert len(frozen) == ticks * len(PROBES)
    assert all(p.sent <= first_answer for p in frozen)


async def test_probing_stops_after_the_last_slow_request_answers():
    """No tick starts after all slow requests have answered."""
    run = await run_frozen(release_after=0.25)
    last_finish = max(s.finished for s in run.slow)
    assert all(p.planned <= last_finish for p in run.probes)
    assert [s.index for s in run.slow] == [0, 1, 2]
    assert all(s.ok and s.server_seconds == 0.5 for s in run.slow)


async def test_failed_probes_and_slow_requests_are_recorded_not_dropped():
    """A 500 is kept as an error with its kind, and counted in the summary."""
    run = await run_frozen(release_after=0.15, broken="/health")
    health = [p for p in run.probes if p.target == "health"]
    assert health and all(not p.ok and p.error == "HTTPStatusError" for p in health)
    summary = summarise(run)
    assert summary["errors"] == len(health)
    # No /health answered, so it has no percentiles (not a made-up number).
    assert summary["health_p95_ms"] == ""
    assert summary["ping_p95_ms"] != ""


async def test_a_slow_request_that_fails_is_recorded():
    """A slow request answered with an error is kept with ok = False."""
    run = await run_frozen(
        release_after=0.05, count=1, broken="/experiments/e1/sleep-a"
    )
    assert run.slow[0].ok is False
    assert run.slow[0].error == "HTTPStatusError"
    assert summarise(run)["slow_last_finish_s"] == ""


def test_the_client_has_no_connection_limit_and_long_timeouts():
    """The safeguards: no queue on our side, nothing cut off before 900 s."""
    client = make_client("http://e1")
    pool = client._transport._pool
    # httpx stores "no limit" as the largest number Python has.
    assert pool._max_connections == sys.maxsize
    assert client.timeout.read == client.timeout.connect == 900
    assert e1_load.TIMEOUT_SECONDS > 50 * max(e1_load.WAITS)


def test_percentiles_use_the_inclusive_method():
    """0..100: p50 is 50 and p95 is 95; a single value is its own percentile."""
    values = [float(n) for n in range(101)]
    assert percentile(values, 0.50) == 50
    assert percentile(values, 0.95) == 95
    assert percentile([7.0], 0.95) == 7.0


def test_summary_numbers():
    """Percentiles in ms, finish times in s, lateness from planned to sent."""
    run = Run(
        probes=[
            Probe("health", 0.0, 0.001, 0.010, True, ""),
            Probe("health", 0.1, 0.105, 0.030, True, ""),
            Probe("ping", 0.0, 0.002, 0.002, True, ""),
        ],
        slow=[Slow(0, 1.0, 1.0, True, ""), Slow(1, 2.0, 1.0, True, "")],
    )
    summary = summarise(run)
    assert summary["health_probes"] == 2
    assert summary["health_p50_ms"] == 20
    assert summary["health_max_ms"] == 30
    assert summary["slow_first_finish_s"] == 1.0
    assert summary["slow_median_finish_s"] == 1.5
    assert summary["slow_last_finish_s"] == 2.0
    assert summary["server_step_median_s"] == 1.0
    assert summary["max_probe_late_ms"] == 5
    assert summary["errors"] == 0


def test_sleep_runs_are_interleaved_every_pair_once_per_repeat():
    """5 repeats x 16 setting-and-wait pairs, each repeat in its own order."""
    plan = sleep_schedule()
    assert len(plan) == 80
    orders = []
    for repeat in range(1, 6):
        runs = [p for p in plan if p.repeat == repeat]
        pairs = [(p.mode, p.count, p.wait) for p in runs]
        assert sorted(pairs) == sorted(
            (m, c, w) for m, c in e1_load.SETTINGS for w in e1_load.WAITS
        )
        assert [p.order for p in runs] == list(range(1, 17))
        orders.append(pairs)
    assert len({tuple(order) for order in orders}) == 5
    # The same order every time the script runs.
    assert sleep_schedule() == plan


def test_ai_runs_are_interleaved():
    """5 repeats of the three AI modes with 10 texts, the order mixed."""
    plan = ai_schedule()
    assert len(plan) == 15
    for repeat in range(1, 6):
        assert sorted(p.mode for p in plan if p.repeat == repeat) == ["a", "b", "c"]
    assert all(p.count == 10 and p.wait is None for p in plan)
    assert len({tuple(p.mode for p in plan if p.repeat == r) for r in range(1, 6)}) > 1


def read_rows(path):
    """The rows of a CSV file."""
    with path.open(encoding="utf-8", newline="") as file:
        return list(csv.DictReader(file))


def test_files_keep_the_other_parts_rows(tmp_path):
    """Redoing one part replaces its rows and keeps the other part's rows."""
    run = Run(
        probes=[Probe("health", 0.0, 0.0, 0.01, True, "")],
        slow=[Slow(0, 1.0, 1.0, True, "")],
    )
    start_part(tmp_path, "sleep")
    save_run(tmp_path, "sleep", Planned(1, 1, "b", 100, 0.5), "t", run)
    start_part(tmp_path, "ai")
    save_run(tmp_path, "ai", Planned(1, 1, "c", 10, None), "t", run)
    start_part(tmp_path, "ai")  # redo the AI part: its old row goes
    runs = read_rows(tmp_path / "e1_runs.csv")
    assert [(r["kind"], r["setting"], r["wait_seconds"]) for r in runs] == [
        ("sleep", "B100", "0.5")
    ]
    probes = read_rows(tmp_path / "e1_probes.csv")
    assert list(probes[0]) == e1_load.PROBE_COLUMNS
    assert probes[0]["target"] == "health"
    assert list(read_rows(tmp_path / "e1_slow.csv")[0]) == e1_load.SLOW_COLUMNS


def test_limits_refused_when_macos_caps_the_queue(monkeypatch):
    """A queue cap below 4096 stops the run, with the command to fix it."""
    monkeypatch.setattr(e1_load, "somaxconn", lambda: 128)
    monkeypatch.setattr(e1_load, "raise_open_files_limit", lambda: 10_240)
    [problem] = e1_load.check_limits()
    assert "sudo sysctl -w kern.ipc.somaxconn=4096" in problem


def test_limits_pass_when_large_enough(monkeypatch):
    """Nothing to fix when both limits are large enough."""
    monkeypatch.setattr(e1_load, "somaxconn", lambda: 4096)
    monkeypatch.setattr(e1_load, "raise_open_files_limit", lambda: 10_240)
    assert e1_load.check_limits() == []


def test_open_files_limit_is_raised():
    """This process can really open 10,240 connections afterwards."""
    assert e1_load.raise_open_files_limit() >= e1_load.OPEN_FILES


def test_only_one_part_option_at_a_time():
    """--sleep-only and --ai-only together would measure nothing."""
    with pytest.raises(SystemExit):
        e1_load.parse_arguments(["--sleep-only", "--ai-only"])
    assert e1_load.parse_arguments(["--ai-only"]).ai_only


async def test_a_split_run_sends_probes_from_their_own_process():
    """A real two-process run against a tiny real server: the probe process
    keeps its 100 ms clock and its probes come back with the slow requests."""
    tiny = FastAPI()

    @tiny.get("/health")
    @tiny.get("/experiments/e1/ping")
    async def ok() -> dict:
        """Answer at once."""
        return {"status": "ok"}

    @tiny.get("/slow")
    async def slow() -> dict:
        """Answer after 0.35 s."""
        await asyncio.sleep(0.35)
        return {"waited_seconds": 0.35}

    # Ask the Mac for a free port, then run the tiny server on it.
    with socket.socket() as free:
        free.bind(("127.0.0.1", 0))
        port = free.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(tiny, port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    while not server.started:
        await asyncio.sleep(0.01)
    try:

        async def send(client, index):
            """One slow request."""
            return await client.get("/slow")

        run = await e1_load.run_split(f"http://127.0.0.1:{port}", send, 5)
    finally:
        server.should_exit = True
        thread.join()
    assert [s.ok for s in run.slow] == [True] * 5
    assert all(0.3 < s.finished < 1 for s in run.slow)
    health = [p.planned for p in run.probes if p.target == "health"]
    assert health[:4] == pytest.approx([0.0, 0.1, 0.2, 0.3])
    assert all(p.ok for p in run.probes)
    # Every probe left before the next tick was due. (Kept loose: the tiny
    # server shares this test's process, and CI machines are slower. The
    # 20 ms check is done on every real run and reported.)
    assert summarise(run)["max_probe_late_ms"] < 100


async def test_a_probe_process_that_dies_stops_the_run_with_an_error(monkeypatch):
    """If the probe process crashes (here: no server to warm up against), the
    run stops with a clear error instead of waiting forever."""
    monkeypatch.setattr(e1_load, "PROBE_START_TIMEOUT", 20)
    with socket.socket() as free:
        free.bind(("127.0.0.1", 0))
        port = free.getsockname()[1]
    # Nothing listens on that port, so the probe process's warm-up fails.
    with pytest.raises(RuntimeError, match="probe process"):
        await e1_load.run_split(f"http://127.0.0.1:{port}", slow_get, 1)


def test_after_fix_runs_b50_and_b100_at_1_s_five_times_interleaved():
    """The D99 confirmation run: 2 settings x 1 wait x 5 repeats."""
    plan = sleep_schedule(waits=[1.0], settings_run=e1_load.AFTER_FIX_SETTINGS)
    assert len(plan) == 10
    for repeat in range(1, 6):
        runs = [(p.mode, p.count, p.wait) for p in plan if p.repeat == repeat]
        assert sorted(runs) == [("b", 50, 1.0), ("b", 100, 1.0)]
    assert len({tuple(p.count for p in plan if p.repeat == r) for r in range(1, 6)}) > 1
    arguments = e1_load.parse_arguments(["--after-fix"])
    assert arguments.after_fix
    with pytest.raises(SystemExit):
        e1_load.parse_arguments(["--after-fix", "--ai-only"])
