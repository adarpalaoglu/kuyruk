"""Experiment E2's kill script: what a kill halfway through a job loses (spec 9.2).

Design approved by Adar on 9 Nov 2026 (D100-D104). Kuyruk runs in Docker
Compose under its own project name (`kuyruk-e2`, with `bench/e2_compose.yaml`:
real timings, experiments on, demo off, its own database). Then, run after run:

1. the tables are emptied and the worker stopped, so every run starts alike;
2. a new user sends a job of 40 texts for the AI (12 in the real-AI check).
   **Broken** runs: no worker runs; the job is started inside the API with
   FastAPI's BackgroundTasks (`app/experiments_e2.py`). **Fixed** runs: the
   worker is started and claims the job, as in normal use;
3. every 100 ms, on a fixed clock, the script reads the job straight from
   the database (so it can watch even while the API is dead);
4. when 20 of the 40 texts are finished (6 of 12), it kills the program
   doing the job with a real SIGKILL (`docker kill`): the API in broken runs,
   the worker in fixed runs. A SIGKILL can't be caught: no clean-up runs,
   like a power cut. Then the script starts the same container again
   (`docker start`). The worker has a restart rule in `compose.yaml`
   (`restart: unless-stopped`), but Docker's restart rule ignores a container
   stopped with `docker kill`, because it treats that as a manual stop; so
   the script restarting it is the correct simulation of a real crash
   restart. The API has no restart rule, so after a real crash it would stay
   down; restarting it gives the broken version its best chance. The script
   first checks for 2 s whether Docker restarted the container itself, which
   it never does; that check adds about 2 s to the restart time, not to the
   recovery time, which waits for the stale time anyway;
5. it keeps watching: a fixed run until the job is over (at most 5 minutes);
   a broken run for 5 minutes after the restart (60 s in the sensitivity
   runs), to show the job never moves again.

Per run (D101): texts **lost** (not finished at the end, whatever their
state), texts **redone** (the same job and position in two "text started"
log lines, D75), **recovery time** (kill -> first text finished after it;
also kill -> takeover and takeover -> first text), restart time, the job's
final state and crash count, and whether /health answers after the restart.

Parts (D102, D103):
- `main`: broken vs fixed, the fake AI replaying the measured 4-at-once
  times, 5 repeats, interleaved;
- `stale`: fixed only, "stuck after" 10 / 30 / 60 s, 5 repeats, interleaved;
- `sensitivity`: broken vs fixed at fixed waits of 0.5 / 1 / 2 / 4 s per
  text, 5 repeats, interleaved;
- `cost`: 3 jobs with no kill; Postgres's own write counters before and after
  each job, and the lines of code of the fix (D104);
- `ai`: fixed only, real Ollama, a 12-text job killed at 6 of 12, 5 repeats,
  after a 10-minute heat-up, with Ollama's memory emptied and 3 warm-ups
  before each run (D89, D94).

It measures Kuyruk, not itself: all times come from this Mac's one clock
(the database's clock is never mixed in), the Docker commands run beside the
watch (never inside it), and every watch that left more than 20 ms late is
counted and reported. All of this goes into `bench/data/setup.md`.

Results: `bench/data/e2_runs.csv` (one row per run), `e2_cost.csv` (one row
per uninterrupted job), `e2_code.csv` (lines of code). Charts:
`uv run --group bench python -m bench.charts`.

Run from the project folder, plugged in, Docker Desktop open, Mac left alone
(about 2.5 hours; `caffeinate` keeps the Mac awake):

    caffeinate -dims uv run python -m bench.e2_kill

`--part NAME` (repeatable) redoes only those parts and keeps the others'
rows. `--trial OUT_DIR` is a quick check (1 broken + 1 fixed run, broken
watched 30 s) written to OUT_DIR instead of `bench/data/`.
"""

import argparse
import ast
import asyncio
import csv
import io
import os
import platform
import random
import re
import secrets
import subprocess
import sys
import threading
import time
import tokenize
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import httpx
import psycopg

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

ROOT = Path(__file__).parent.parent
DATA = Path(__file__).parent / "data"

# ---- The experiment's design (D100-D104, approved by Adar) -------------------

PROJECT = "kuyruk-e2"  # its own containers and database (like D86)
COMPOSE = ["docker", "compose", "-p", PROJECT, "-f", "compose.yaml"]
COMPOSE += ["-f", "bench/e2_compose.yaml"]
API_URL = "http://127.0.0.1:8020"
DB_PORT = 5442

TEXTS, KILL_AT = 40, 20  # main, stale and sensitivity runs (D101)
AI_TEXTS, AI_KILL_AT = 12, 6  # the real-AI check (D103)
REPEATS = 5
WAITS = [0.5, 1.0, 2.0, 4.0]  # sensitivity: fixed waits per text
STALES = [10.0, 30.0, 60.0]  # the stale-time check
HEARTBEAT_SECONDS = 5.0  # real use (D68)
STALE_SECONDS = 30.0  # real use (D68)
COST_JOBS = 3

BROKEN_WATCH_MAIN = 300.0  # watch a broken job 5 min after the restart
BROKEN_WATCH_SENSITIVITY = 60.0  # (the 5-minute proof is in the main runs)
BROKEN_WATCH_TRIAL = 30.0
FIXED_LIMIT = 300.0  # give up on a fixed run 5 min after the kill
KILL_LIMIT = 600.0  # give up if the kill point is never reached
WATCH_EVERY = 0.1  # seconds between two looks at the database
LATE_WARNING_MS = 20  # report any look that left later than this
DOCKER_RESTART_WAIT = 2.0  # check this long that Docker didn't restart it
STATS_FLUSH_SECONDS = 12.0  # Postgres saves its counters within ~10 s of idle
REST_SECONDS = 2.0
ORDER_SEED = 12

# The worker's crash-recovery parts, whose lines of code are the fix's cost.
# Their demo-only lines (the demo crash button's check in `beat`, D74) are
# left out: they belong to demo mode, not to the fix.
RECOVERY_PARTS = ["take_over_stuck_job", "heartbeat", "beat", "hand_back"]

FINISHED = ("ok", "failed")  # a text in any other state is not finished
ENDED = ("done", "failed", "cancelled")  # a job in one of these is over


@dataclass(frozen=True)
class Planned:
    """One run to do: which part, broken or fixed, and the settings."""

    part: str
    kind: str  # "broken" or "fixed"
    wait: str  # "replay" or a fixed wait per text, e.g. "0.5"
    stale_seconds: float
    ai: str  # "fake" or "ollama"
    repeat: int
    order: int
    texts: int
    kill_at: int
    watch_after_restart: float  # broken runs only


@dataclass
class Look:
    """One look at the job in the database, at `t` seconds on the Mac's clock."""

    t: float
    late_ms: float
    state: str
    finished: int
    crash_count: int


@dataclass
class Kill:
    """What happened at the kill (filled in by the kill thread)."""

    t_kill: float | None = None
    finished_at_kill: int | None = None
    t_running: float | None = None  # the killed container runs again
    t_healthy: float | None = None  # the API answers /health again (broken)
    restarted_by: str = ""  # "docker" (its restart rule) or "script"
    error: str = ""
    done: threading.Event = field(default_factory=threading.Event)


# ---- Plans ---------------------------------------------------------------------


def plan_run(part: str, kind: str, repeat: int, order: int, **changes) -> Planned:
    """A run with the usual settings, changed by `changes`."""
    base = {
        "wait": "replay",
        "stale_seconds": STALE_SECONDS,
        "ai": "fake",
        "texts": TEXTS,
        "kill_at": KILL_AT,
        "watch_after_restart": BROKEN_WATCH_MAIN,
    }
    return Planned(part, kind, repeat=repeat, order=order, **{**base, **changes})


def main_plan(repeats: int = REPEATS) -> list[Planned]:
    """Broken and fixed once per repeat, each repeat in its own order."""
    plan = []
    for repeat in range(1, repeats + 1):
        kinds = ["broken", "fixed"]
        random.Random(ORDER_SEED + repeat).shuffle(kinds)
        plan += [plan_run("main", k, repeat, n) for n, k in enumerate(kinds, 1)]
    return plan


def stale_plan(repeats: int = REPEATS) -> list[Planned]:
    """Fixed only, each stale time once per repeat, each repeat shuffled."""
    plan = []
    for repeat in range(1, repeats + 1):
        stales = list(STALES)
        random.Random(ORDER_SEED + 100 + repeat).shuffle(stales)
        plan += [
            plan_run("stale", "fixed", repeat, n, stale_seconds=s)
            for n, s in enumerate(stales, 1)
        ]
    return plan


def sensitivity_plan(repeats: int = REPEATS) -> list[Planned]:
    """Every wait once per repeat (shuffled), broken and fixed at each (shuffled).

    The runs of one wait stay together, because changing the wait means
    starting new containers (about 15 s each time).
    """
    plan = []
    for repeat in range(1, repeats + 1):
        shuffler = random.Random(ORDER_SEED + 200 + repeat)
        waits = list(WAITS)
        shuffler.shuffle(waits)
        order = 0
        for wait in waits:
            kinds = ["broken", "fixed"]
            shuffler.shuffle(kinds)
            for kind in kinds:
                order += 1
                plan.append(
                    plan_run(
                        "sensitivity",
                        kind,
                        repeat,
                        order,
                        wait=f"{wait:g}",
                        watch_after_restart=BROKEN_WATCH_SENSITIVITY,
                    )
                )
    return plan


def ai_plan(repeats: int = REPEATS) -> list[Planned]:
    """The real-AI check: fixed only, 12 texts killed at 6 (D103)."""
    return [
        plan_run(
            "ai", "fixed", repeat, 1, ai="ollama", texts=AI_TEXTS, kill_at=AI_KILL_AT
        )
        for repeat in range(1, repeats + 1)
    ]


def trial_plan() -> list[Planned]:
    """A quick check: one broken run (watched 30 s) and one fixed run."""
    return [
        plan_run("trial", "broken", 1, 1, watch_after_restart=BROKEN_WATCH_TRIAL),
        plan_run("trial", "fixed", 1, 2),
    ]


# ---- What the watch saw --------------------------------------------------------


def started_positions(log_text: str, job_id: str) -> Counter:
    """How many times each text of the job was started, from the log lines (D75).

    The lines look like `text started job=<id> position=<n> worker=<name>`.
    """
    pattern = re.compile(rf"text started job={re.escape(job_id)} position=(\d+)\b")
    return Counter(int(n) for n in pattern.findall(log_text))


def count_redone(starts: Counter) -> int:
    """Texts done more than once: every start of a position after its first."""
    return sum(count - 1 for count in starts.values() if count > 1)


def first_look(looks: list[Look], after: float, test: Callable[[Look], bool]):
    """The first look at or after `after` that passes `test`, or None."""
    return next((look for look in looks if look.t >= after and test(look)), None)


def recovery(looks: list[Look], kill: Kill) -> dict:
    """Times after the kill, in seconds ("" where it never happened).

    - takeover: the crash count went up (another worker took the job over);
    - recovery: the first text finished after the kill;
    - finish: the job ended (done, failed or cancelled).
    """
    if kill.t_kill is None or kill.finished_at_kill is None:
        return {"takeover_s": "", "recovery_s": "", "finish_s": ""}
    t_kill, finished = kill.t_kill, kill.finished_at_kill

    def since_kill(look: Look | None) -> float | str:
        """Seconds from the kill to that look, or "" if there was none."""
        return "" if look is None else round(look.t - t_kill, 2)

    return {
        "takeover_s": since_kill(first_look(looks, t_kill, lambda x: x.crash_count)),
        "recovery_s": since_kill(
            first_look(looks, t_kill, lambda x: x.finished > finished)
        ),
        "finish_s": since_kill(first_look(looks, t_kill, lambda x: x.state in ENDED)),
    }


def late_looks(looks: list[Look]) -> tuple[int, float]:
    """How many looks left more than LATE_WARNING_MS late, and the worst."""
    late = [look.late_ms for look in looks]
    return sum(1 for ms in late if ms > LATE_WARNING_MS), max(late, default=0.0)


# ---- Lines of code (the fix's cost, D104) -------------------------------------


def code_lines(source: str) -> set[int]:
    """The line numbers that hold code: not blank, not only comments, not docstrings.

    A *docstring* is the text in quotes at the top of a file, class or
    function that explains it; it is documentation, not code.
    """
    tree = ast.parse(source)
    docstring_lines = set()
    for node in ast.walk(tree):
        if isinstance(
            node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef
        ):
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                docstring_lines.update(range(body[0].lineno, body[0].end_lineno + 1))
    not_code = {
        tokenize.COMMENT,
        tokenize.NL,
        tokenize.NEWLINE,
        tokenize.INDENT,
        tokenize.DEDENT,
        tokenize.ENCODING,
        tokenize.ENDMARKER,
    }
    lines = set()
    for token in tokenize.generate_tokens(io.StringIO(source).readline):
        if token.type not in not_code:
            lines.update(range(token.start[0], token.end[0] + 1))
    return lines - docstring_lines


def demo_only_lines(function: ast.FunctionDef | ast.AsyncFunctionDef) -> set[int]:
    """The lines of `function` that only matter in demo mode.

    In Kuyruk's worker, demo-only code comes last in a function: everything
    from the first line that reads `demo_mode` to the function's end (in
    `beat`: "if not in demo mode, stop here; else check for the demo crash
    mark", D74). Empty if the function never reads `demo_mode`.
    """
    first = [
        node.lineno
        for node in ast.walk(function)
        if isinstance(node, ast.Attribute) and node.attr == "demo_mode"
    ]
    if not first:
        return set()
    return set(range(min(first), function.end_lineno + 1))


def count_code(source: str, names: list[str] | None = None) -> int:
    """Lines of code in `source`, or only in the functions called `names`.

    For named functions, their demo-only lines are left out (see
    `demo_only_lines`), so only the fix itself is counted.
    """
    lines = code_lines(source)
    if names is None:
        return len(lines)
    inside = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and (
            node.name in names
        ):
            start = min([node.lineno, *(d.lineno for d in node.decorator_list)])
            inside.update(range(start, node.end_lineno + 1))
            inside -= demo_only_lines(node)
    return len(lines & inside)


def code_rows(root: Path = ROOT) -> list[dict]:
    """The lines-of-code table: the worker, and its crash-recovery parts.

    The broken way (`app/experiments_e2.py`) is not counted: it runs the
    worker's own code (D100), so its own few lines would understate it.
    """
    worker = (root / "app" / "worker.py").read_text(encoding="utf-8")
    return [
        {"what": "worker (app/worker.py, all of it)", "lines": count_code(worker)},
        {
            "what": "of which crash recovery ("
            + ", ".join(RECOVERY_PARTS)
            + "; demo-only lines left out)",
            "lines": count_code(worker, RECOVERY_PARTS),
        },
    ]


# ---- Saving -------------------------------------------------------------------

RUN_COLUMNS = [
    "part",
    "kind",
    "ai",
    "wait",
    "stale_seconds",
    "repeat",
    "order_in_repeat",
    "started_at",
    "texts",
    "kill_at",
    "finished_at_kill",
    "killed",
    "restarted_by",
    "restart_s",
    "takeover_s",
    "recovery_s",
    "finish_s",
    "watched_after_restart_s",
    "final_state",
    "finished_end",
    "lost",
    "redone",
    "text_starts",
    "crash_count",
    "health_after",
    "late_looks",
    "max_late_ms",
    "error",
]
COST_COLUMNS = [
    "repeat",
    "texts",
    "job_s",
    "jobs_inserts",
    "jobs_updates",
    "items_inserts",
    "items_updates",
    "all_writes",
    "heartbeat_writes",
    "heartbeats_expected",
    "heartbeat_share",
]
CODE_COLUMNS = ["what", "lines"]
FILES = {"runs": "e2_runs.csv", "cost": "e2_cost.csv", "code": "e2_code.csv"}


def start_runs_file(out: Path, parts: list[str]) -> None:
    """Remove the old rows of the parts about to run; keep the other parts' rows."""
    out.mkdir(parents=True, exist_ok=True)
    path = out / FILES["runs"]
    kept = []
    if path.exists():
        with path.open(encoding="utf-8", newline="") as file:
            kept = [row for row in csv.DictReader(file) if row["part"] not in parts]
    write_rows(path, RUN_COLUMNS, kept)


def write_rows(path: Path, columns: list[str], rows: list[dict]) -> None:
    """Write a whole CSV file afresh."""
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def add_row(path: Path, columns: list[str], row: dict) -> None:
    """Add one row (after every run, so nothing measured is lost if it stops)."""
    with path.open("a", encoding="utf-8", newline="") as file:
        csv.DictWriter(file, fieldnames=columns).writerow(row)


def report(row: dict) -> None:
    """Print one line per run, and warn loudly about errors or late looks."""
    print(
        f"  {row['kind']:6} lost {row['lost']}, redone {row['redone']}, "
        f"recovery {row['recovery_s'] or 'never'} s, final {row['final_state']}, "
        f"crash count {row['crash_count']}, health after {row['health_after']}"
    )
    if row["error"]:
        print(f"  !! error: {row['error']}")
    if row["late_looks"]:
        print(f"  !! {row['late_looks']} looks late (worst {row['max_late_ms']} ms)")


# ---- Docker and the database ------------------------------------------------


class Stack:
    """Kuyruk in Docker Compose for E2: start, change settings, kill, restart.

    The secrets are throwaway ones made for this session; they are passed to
    Docker in the environment, never printed or written to a file.
    """

    def __init__(self) -> None:
        """Make the session's secrets; nothing is started yet."""
        self.jwt_secret = secrets.token_urlsafe(48)
        self.db_password = secrets.token_urlsafe(24)
        self.current: tuple | None = None

    def environment(self, planned: Planned) -> dict[str, str]:
        """The values `bench/e2_compose.yaml` asks for, for this run's settings."""
        fixed_wait = planned.wait != "replay"
        return {
            **os.environ,
            "E2_JWT_SECRET": self.jwt_secret,
            "E2_POSTGRES_PASSWORD": self.db_password,
            "E2_AI_BACKEND": planned.ai,
            "E2_FAKE_AI_WAIT_MODE": "fixed" if fixed_wait else "replay",
            "E2_FAKE_AI_WAIT_SECONDS": planned.wait if fixed_wait else "1",
            "E2_HEARTBEAT_SECONDS": f"{HEARTBEAT_SECONDS:g}",
            "E2_STALE_AFTER_SECONDS": f"{planned.stale_seconds:g}",
        }

    def compose(self, planned: Planned, *arguments: str) -> str:
        """Run one `docker compose` command for this project; return its output."""
        result = subprocess.run(
            [*COMPOSE, *arguments],
            cwd=ROOT,
            env=self.environment(planned),
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(f"docker compose {arguments[0]} failed: {result.stderr}")
        return result.stdout

    def start_fresh(self, planned: Planned) -> None:
        """Remove anything left from an earlier session, build, and start."""
        self.compose(planned, "down", "-v", "--remove-orphans")
        self.compose(planned, "up", "-d", "--build", "--wait")
        self.current = self.settings_of(planned)

    @staticmethod
    def settings_of(planned: Planned) -> tuple:
        """The settings that need new containers when they change."""
        return (planned.ai, planned.wait, planned.stale_seconds)

    def use(self, planned: Planned) -> None:
        """Make sure the containers run with this run's settings.

        Compose starts new containers only for settings that changed.
        """
        if self.settings_of(planned) != self.current:
            self.compose(planned, "up", "-d", "--wait")
            self.current = self.settings_of(planned)

    def container(self, planned: Planned, service: str) -> str:
        """The id of the service's container (running or not)."""
        return self.compose(planned, "ps", "-aq", service).strip()

    def database(self) -> psycopg.Connection:
        """A new connection to the E2 database, from this Mac."""
        return psycopg.connect(
            host="127.0.0.1",
            port=DB_PORT,
            user="kuyruk",
            password=self.db_password,
            dbname="kuyruk",
            autocommit=True,
        )

    def empty_tables(self, planned: Planned) -> None:
        """Stop the worker politely, then empty every table: a clean start.

        The worker goes first, so it can't pick up a job left by the last run
        (e.g. a broken run's job, stuck forever) while the tables are emptied.
        """
        self.compose(planned, "stop", "worker")
        with self.database() as db:
            db.execute("TRUNCATE users, jobs, job_items RESTART IDENTITY CASCADE")

    def stop(self, planned: Planned) -> None:
        """Stop and remove everything, including the session's database."""
        self.compose(planned, "down", "-v", "--remove-orphans")


def docker(*arguments: str) -> str:
    """Run one plain `docker` command and return its output."""
    return subprocess.run(
        ["docker", *arguments], capture_output=True, text=True, check=True
    ).stdout.strip()


def started_at(container: str) -> str:
    """When Docker last started the container (changes at every restart)."""
    return docker("inspect", "-f", "{{.State.StartedAt}}", container)


def is_running(container: str) -> bool:
    """True if the container is running right now."""
    return docker("inspect", "-f", "{{.State.Running}}", container) == "true"


def kill_and_restart(
    stack: Stack, container: str, job_id: str, kill: Kill, wait_for_api: bool
) -> None:
    """Kill the container with SIGKILL, then have it start again at once.

    Runs on its own thread, beside the watch, so the slow Docker commands never
    delay a look at the database. The kill time is taken when `docker kill`
    has returned: the program is dead by then and can write nothing more,
    so the texts finished at that moment are counted straight after.
    """
    try:
        before = started_at(container)
        docker("kill", "--signal", "KILL", container)
        kill.t_kill = time.perf_counter()
        with stack.database() as db:
            kill.finished_at_kill = db.execute(
                "SELECT count(*) FROM job_items WHERE job_id = %s AND state IN "
                "('ok', 'failed')",
                (job_id,),
            ).fetchone()[0]
        # Docker's restart rule ignores a container stopped with `docker
        # kill` (a manual stop), so the script does the worker's restart a
        # real crash would get. The API has no restart rule; restarting it
        # gives the broken version its best chance. A short check first, so
        # a Docker that did restart it is recorded ("docker") rather than
        # started twice.
        deadline = kill.t_kill + DOCKER_RESTART_WAIT
        while time.perf_counter() < deadline:
            if is_running(container) and started_at(container) != before:
                kill.restarted_by = "docker"
                break
            time.sleep(0.05)
        else:
            docker("start", container)
            kill.restarted_by = "script"
        kill.t_running = time.perf_counter()
        if wait_for_api:
            for _ in range(600):
                try:
                    if httpx.get(f"{API_URL}/health", timeout=1).status_code == 200:
                        kill.t_healthy = time.perf_counter()
                        break
                except httpx.HTTPError:
                    pass
                time.sleep(0.1)
    except Exception as error:  # noqa: BLE001 - recorded in the run's row
        kill.error = f"{type(error).__name__}: {error}"
    finally:
        kill.done.set()


# ---- One run --------------------------------------------------------------------


def send_job(texts: list[str]) -> tuple[dict[str, str], str]:
    """Sign up a new user, log in, send the job; return the login header and ticket."""
    account = {"username": f"e2{secrets.token_hex(4)}", "password": "e2-gizli-1234"}
    with httpx.Client(base_url=API_URL, timeout=30) as client:
        client.post("/register", json=account).raise_for_status()
        token = client.post("/token", data=account).json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        body = {"task": "llm", "texts": texts}
        reply = client.post("/jobs", json=body, headers=headers)
        reply.raise_for_status()
    return headers, reply.json()["id"]


def look_at(db: psycopg.Connection, job_id: str) -> tuple[str, int, int]:
    """The job's state, finished texts and crash count, from the database."""
    return db.execute(
        "SELECT j.state, (SELECT count(*) FROM job_items i WHERE i.job_id = j.id "
        "AND i.state IN ('ok', 'failed')), j.crash_count FROM jobs j WHERE j.id = %s",
        (job_id,),
    ).fetchone()


def health_ok() -> bool:
    """True if the API answers /health with 200."""
    try:
        return httpx.get(f"{API_URL}/health", timeout=5).status_code == 200
    except httpx.HTTPError:
        return False


def next_step(
    planned: Planned,
    kill: Kill,
    kill_sent: bool,
    state: str,
    finished: int,
    elapsed: float,
) -> tuple[bool, str]:
    """After one look: should the watch stop, and with what error ("" = none)?

    - before the kill: stop with an error if the job ended first, or if the
      kill point is never reached;
    - a failed kill or restart: stop with its error;
    - a fixed run: stop when the job is over, or FIXED_LIMIT after the kill;
    - a broken run: stop once it has been watched long enough after the
      restart (from when the API answers again).
    """
    if not kill_sent:
        if state in ENDED:
            return True, f"the job ended ({state}) before the kill"
        if elapsed > KILL_LIMIT:
            return True, "the kill point was never reached"
        return False, ""
    if kill.error:
        return True, kill.error
    if kill.t_kill is None:
        return False, ""
    now = time.perf_counter()
    if planned.kind == "fixed":
        return state in ENDED or now - kill.t_kill > FIXED_LIMIT, ""
    restarted = kill.t_healthy or kill.t_running
    if not kill.done.is_set() or restarted is None:
        return False, ""
    return now - restarted >= planned.watch_after_restart, ""


def run_once(stack: Stack, planned: Planned, texts: list[str]) -> dict:
    """Do one run (steps 1-5 of the file's description) and return its row."""
    stack.use(planned)
    stack.empty_tables(planned)
    victim = "api" if planned.kind == "broken" else "worker"
    if planned.kind == "fixed":
        stack.compose(planned, "start", "worker")
    started = datetime.now().astimezone().isoformat(timespec="seconds")
    headers, job_id = send_job(texts)
    if planned.kind == "broken":
        reply = httpx.post(
            f"{API_URL}/experiments/e2/jobs/{job_id}/run-in-api",
            headers=headers,
            timeout=30,
        )
        reply.raise_for_status()
    container = stack.container(planned, victim)
    kill = Kill()
    looks: list[Look] = []
    error = ""
    kill_sent = False
    start = time.perf_counter()
    tick = 0
    with stack.database() as db:
        while True:
            # A fixed clock: look number n leaves at start + n x 100 ms.
            planned_at = start + tick * WATCH_EVERY
            time.sleep(max(0.0, planned_at - time.perf_counter()))
            now = time.perf_counter()
            state, finished, crashes = look_at(db, job_id)
            looks.append(
                Look(now, round((now - planned_at) * 1000, 1), state, finished, crashes)
            )
            tick += 1
            elapsed = now - start
            stop, error = next_step(planned, kill, kill_sent, state, finished, elapsed)
            if stop:
                break
            if not kill_sent and finished >= planned.kill_at:
                kill_sent = True
                threading.Thread(
                    target=kill_and_restart,
                    args=(stack, container, job_id, kill, victim == "api"),
                    daemon=True,
                ).start()
    # Wait for the kill thread to finish its restart, if it was started (a run
    # that stopped before the kill point has none to wait for).
    if kill_sent:
        kill.done.wait(timeout=120)
    return finish_row(stack, planned, started, job_id, container, looks, kill, error)


def finish_row(
    stack: Stack,
    planned: Planned,
    started: str,
    job_id: str,
    container: str,
    looks: list[Look],
    kill: Kill,
    error: str,
) -> dict:
    """Count what the run lost and redid, and build its row."""
    with stack.database() as db:
        state, crash_count = db.execute(
            "SELECT state, crash_count FROM jobs WHERE id = %s", (job_id,)
        ).fetchone()
        finished_end = db.execute(
            "SELECT count(*) FROM job_items WHERE job_id = %s AND state IN "
            "('ok', 'failed')",
            (job_id,),
        ).fetchone()[0]
    logs = subprocess.run(["docker", "logs", container], capture_output=True, text=True)
    starts = started_positions(logs.stdout + logs.stderr, job_id)
    late, worst = late_looks(looks)
    restarted = kill.t_healthy or kill.t_running
    watched = "" if restarted is None else round(looks[-1].t - restarted, 1)
    restart_s = "" if kill.t_running is None else round(kill.t_running - kill.t_kill, 2)
    return {
        "part": planned.part,
        "kind": planned.kind,
        "ai": planned.ai,
        "wait": planned.wait,
        "stale_seconds": f"{planned.stale_seconds:g}",
        "repeat": planned.repeat,
        "order_in_repeat": planned.order,
        "started_at": started,
        "texts": planned.texts,
        "kill_at": planned.kill_at,
        "finished_at_kill": kill.finished_at_kill,
        "killed": "api" if planned.kind == "broken" else "worker",
        "restarted_by": kill.restarted_by,
        "restart_s": restart_s,
        **recovery(looks, kill),
        "watched_after_restart_s": watched,
        "final_state": state,
        "finished_end": finished_end,
        "lost": planned.texts - finished_end,
        "redone": count_redone(starts),
        "text_starts": sum(starts.values()),
        "crash_count": crash_count,
        "health_after": health_ok(),
        "late_looks": late,
        "max_late_ms": worst,
        "error": error,
    }


# ---- The cost of the fix (D104) ----------------------------------------------


def write_counters(db: psycopg.Connection) -> dict[str, int]:
    """Postgres's own counters of rows written, for every Kuyruk table.

    `pg_stat_user_tables` keeps, per table, `n_tup_ins` (rows inserted),
    `n_tup_upd` (rows updated) and `n_tup_del` (rows deleted) since the
    database started.
    """
    counters = {}
    rows = db.execute(
        "SELECT relname, n_tup_ins, n_tup_upd, n_tup_del FROM pg_stat_user_tables"
    ).fetchall()
    for table, inserted, updated, deleted in rows:
        counters[f"{table}_ins"] = inserted
        counters[f"{table}_upd"] = updated
        counters[f"{table}_del"] = deleted
    return counters


def cost_row(repeat: int, texts: int, job_s: float, before: dict, after: dict) -> dict:
    """One uninterrupted job's writes, and the heartbeat's share of them.

    The `jobs` row of a job is updated once when it is claimed, once per
    finished text (its count goes up), once when it is done, and once per
    heartbeat. So the heartbeat's writes are what is left after the others.
    """

    def grew(key: str) -> int:
        """How much one counter grew during the job."""
        return after.get(key, 0) - before.get(key, 0)

    jobs_updates = grew("jobs_upd")
    all_writes = sum(grew(key) for key in after)
    heartbeats = jobs_updates - texts - 2
    return {
        "repeat": repeat,
        "texts": texts,
        "job_s": round(job_s, 1),
        "jobs_inserts": grew("jobs_ins"),
        "jobs_updates": jobs_updates,
        "items_inserts": grew("job_items_ins"),
        "items_updates": grew("job_items_upd"),
        "all_writes": all_writes,
        "heartbeat_writes": heartbeats,
        "heartbeats_expected": int(job_s // HEARTBEAT_SECONDS),
        "heartbeat_share": round(heartbeats / all_writes, 3) if all_writes else "",
    }


def run_cost_job(stack: Stack, planned: Planned, texts: list[str]) -> dict:
    """One job with no kill, with Postgres's write counters read around it."""
    stack.use(planned)
    stack.empty_tables(planned)
    # Postgres saves its counters a little after the writes (within ~10 s of a
    # connection going quiet), so wait before reading, before and after.
    time.sleep(STATS_FLUSH_SECONDS)
    with stack.database() as db:
        before = write_counters(db)
    stack.compose(planned, "start", "worker")
    _, job_id = send_job(texts)
    claimed = None
    with stack.database() as db:
        while True:
            state, _, _ = look_at(db, job_id)
            if claimed is None and state != "queued":
                claimed = time.perf_counter()
            if state in ENDED:
                ended = time.perf_counter()
                break
            time.sleep(WATCH_EVERY)
    time.sleep(STATS_FLUSH_SECONDS)
    with stack.database() as db:
        after = write_counters(db)
    return cost_row(planned.repeat, len(texts), ended - claimed, before, after)


# ---- Parts ---------------------------------------------------------------------


def run_plan(stack: Stack, out: Path, plan: list[Planned], texts: list[str]) -> None:
    """Do every run of a plan, saving each row as soon as it is measured."""
    for number, planned in enumerate(plan, start=1):
        print(
            f"Run {number}/{len(plan)}: {planned.part} {planned.kind}, wait "
            f"{planned.wait}, stale {planned.stale_seconds:g} s, "
            f"repeat {planned.repeat}"
        )
        if planned.ai == "ollama":
            # Empty Ollama's memory of earlier texts, then 3 warm-ups (D89).
            asyncio.run(fresh_start(RecordingTransport, clear_ollama_memory))
        try:
            row = run_once(stack, planned, texts[: planned.texts])
        except Exception as error:  # noqa: BLE001 - recorded, the next run goes on
            # Keep the run's settings, so the failed run is counted (as an
            # error) in its own group.
            row = {
                **{column: "" for column in RUN_COLUMNS},
                "part": planned.part,
                "kind": planned.kind,
                "ai": planned.ai,
                "wait": planned.wait,
                "stale_seconds": f"{planned.stale_seconds:g}",
                "texts": planned.texts,
                "kill_at": planned.kill_at,
                "repeat": planned.repeat,
                "order_in_repeat": planned.order,
                "error": f"{type(error).__name__}: {error}",
            }
        add_row(out / FILES["runs"], RUN_COLUMNS, row)
        report(row)
        time.sleep(REST_SECONDS)


def run_cost(stack: Stack, out: Path, texts: list[str]) -> None:
    """The cost part: 3 uninterrupted jobs, then the lines of code."""
    rows = []
    for repeat in range(1, COST_JOBS + 1):
        planned = plan_run("cost", "fixed", repeat, 1)
        row = run_cost_job(stack, planned, texts[:TEXTS])
        print(
            f"Cost job {repeat}: {row['job_s']} s, {row['all_writes']} writes, "
            f"{row['heartbeat_writes']} by the heartbeat "
            f"(expected {row['heartbeats_expected']})"
        )
        rows.append(row)
    write_rows(out / FILES["cost"], COST_COLUMNS, rows)
    write_rows(out / FILES["code"], CODE_COLUMNS, code_rows())


# ---- Setup notes -------------------------------------------------------------


def setup_notes(parts: list[str], heat_up_seconds: float) -> str:
    """The E2 setup notes (spec 9) as a Markdown section."""
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
    when = datetime.now().astimezone().isoformat(timespec="seconds")
    mac = f"{hardware_field('Model Name')} ({hardware_field('Model Identifier')})"
    docker_engine = command_output("docker", "version", "-f", "{{.Server.Version}}")
    docker_compose = command_output("docker", "compose", "version", "--short")
    lines = [
        "",
        "## E2 — losing work (Slice 12)",
        "",
        "Written automatically by `bench/e2_kill.py` when the run started.",
        "",
        "| | |",
        "|---|---|",
        f"| Date and time | {when} |",
        f"| Mac | {mac}, {hardware_field('Chip')}, {hardware_field('Memory')} |",
        f"| macOS | {platform.mac_ver()[0]} |",
        f"| Power | {power[0] if power else 'unknown'} |",
        f"| Docker Engine, Compose | {docker_engine}, {docker_compose} |",
        f"| Kuyruk commit | `{commit}`"
        f"{' plus uncommitted Slice 12 work' if changed else ''} |",
        f"| Parts | {', '.join(parts)} |",
        f"| Compose | project `{PROJECT}`, `compose.yaml` + `bench/e2_compose.yaml`; "
        "own database, emptied before every run; no `.env` values |",
        f"| Worker settings | heartbeat {HEARTBEAT_SECONDS:g} s, stuck after "
        f"{STALE_SECONDS:g} s (stale part: {', '.join(f'{s:g}' for s in STALES)} s), "
        "4 texts at once, looks for jobs every 1 s |",
        f"| Job | {TEXTS} texts, killed at {KILL_AT} finished (real AI: "
        f"{AI_TEXTS}, killed at {AI_KILL_AT}) |",
        "| Kill | `docker kill --signal KILL` (API in broken runs, worker in "
        "fixed runs); then the script starts the same container again "
        "(`docker start`). Docker's restart rule ignores a container stopped "
        "with `docker kill`, because it treats that as a manual stop, so the "
        "script restarting the worker is the correct simulation of a real "
        "crash restart. The API has no restart rule, so after a real crash it "
        "would stay down; restarting it gave the broken version its best "
        f"chance. First a {DOCKER_RESTART_WAIT:g} s check that Docker didn't "
        "restart it |",
        f"| Watch | the database every {WATCH_EVERY * 1000:g} ms on a fixed clock, "
        f"from this Mac; warning if a look leaves > {LATE_WARNING_MS} ms late |",
        f"| Broken watched after restart | {BROKEN_WATCH_MAIN:g} s (main), "
        f"{BROKEN_WATCH_SENSITIVITY:g} s (sensitivity) |",
        "| Other apps | Docker Desktop open (needed); other heavy apps closed |",
    ]
    if "ai" in parts:
        reply = httpx.get(f"{settings.ollama_url}/api/version", timeout=10).json()
        lines += [
            f"| Ollama version, model | {reply.get('version', 'unknown')}, "
            f"{settings.ollama_model} (digest `{measure_ollama.model_digest()}`) |",
            f"| Ollama parallel setting | "
            f"{measure_ollama.ollama_parallel_setting()} (D88) |",
            f"| AI part | heat-up {heat_up_seconds:g} s (D94); memory emptied and "
            "3 warm-ups before every run (D89) |",
        ]
    return "\n".join(lines) + "\n"


# ---- Start -----------------------------------------------------------------

PARTS = ["main", "stale", "sensitivity", "cost", "ai"]


def parse_arguments(arguments: list[str] | None = None) -> argparse.Namespace:
    """Read the command-line options (None: the ones the script was run with)."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--part",
        action="append",
        choices=PARTS,
        help="only this part (can be given more than once); default: all",
    )
    parser.add_argument(
        "--trial",
        metavar="OUT_DIR",
        type=Path,
        help="a quick check (1 broken + 1 fixed run) written to OUT_DIR",
    )
    return parser.parse_args(arguments)


def plans_for(parts: list[str]) -> list[Planned]:
    """The runs of the chosen parts, in the order they are done."""
    makers = {
        "main": main_plan,
        "stale": stale_plan,
        "sensitivity": sensitivity_plan,
        "ai": ai_plan,
    }
    return [run for part in parts if part in makers for run in makers[part]()]


def main() -> None:
    """Check the setup, write the notes, run every part, save."""
    arguments = parse_arguments()
    parts = ["trial"] if arguments.trial else arguments.part or PARTS
    out = arguments.trial or DATA
    if "ai" in parts:
        problems = measure_ollama.check_setup()
        if problems:
            print("Not measuring:", *problems, sep="\n- ")
            sys.exit(1)
    texts = [review.text for review in shuffled(load_reviews())]
    out.mkdir(parents=True, exist_ok=True)
    with (out / "setup.md").open("a", encoding="utf-8") as file:
        file.write(setup_notes(parts, HEAT_UP_SECONDS if "ai" in parts else 0))
    plan = trial_plan() if arguments.trial else plans_for(parts)
    start_runs_file(out, parts)
    stack = Stack()
    first = plan[0] if plan else plan_run("cost", "fixed", 1, 1)
    print("Building and starting Kuyruk in Docker (project kuyruk-e2)...")
    stack.start_fresh(first)
    try:
        fake = [p for p in plan if p.ai == "fake"]
        run_plan(stack, out, fake, texts)
        if "cost" in parts:
            run_cost(stack, out, texts)
        real = [p for p in plan if p.ai == "ollama"]
        if real:
            asyncio.run(heat_up(load_reviews(), RecordingTransport, HEAT_UP_SECONDS))
            run_plan(stack, out, real, texts)
    finally:
        stack.stop(first)
    print(f"Done. Results are in {out}/.")


if __name__ == "__main__":
    main()
