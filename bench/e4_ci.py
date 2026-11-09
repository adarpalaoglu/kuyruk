"""Experiment E4's script: the uv cache in CI, and who sees the secret (spec 9.4).

Design approved by Adar on 9 Nov 2026 (D108, D109). Everything runs on
GitHub's computers; this script only starts runs and reads GitHub's records,
with the `gh` tool (GitHub's command-line program, already logged in).

**Part `cache`** (D108). One warm-up run with uv's download cache on (not
recorded, so the cache surely exists), then 20 manual runs of the CI
workflow (`gh workflow run ci.yml -f uv_cache=true|false`, D83): 10 with the
cache on and 10 off, in the order on-off-off-on, repeated. A run is started
only when no other run of any workflow is going, so runs never compete for
GitHub's machines. All runs must test the same commit; the script stops if
one doesn't. For each run it reads from GitHub:

- the job's start and end (GitHub's own records, in whole seconds) and the
  waiting time before a machine was free;
- each step's time, from the timestamps GitHub puts on every log line
  (fractions of a second): a step lasts from its first log line to the next
  step's first log line. GitHub's whole-second step times are kept too;
- uv's own lines from the install step ("Prepared 35 packages in 1.2s" =
  downloading and unpacking, "Installed 43 packages in 18ms" = putting them
  in place), whether the cache was found ("hit"), not found ("miss") or not
  used ("off"), and the "secret present" line.

**Part `secrets`** (D109). The "secret present" line of runs that already
happened (pushes to main and to the `slice-9` branch, pull request #1 from
that branch, Release runs after a push), with links. And every Release run
that followed one of the manual runs: Release starts whenever CI finishes on
main, but its publish job must be skipped for a manual run (it publishes
only after a push), so its secret step never runs.

Results: `bench/data/e4_runs.csv` (one row per run), `e4_steps.csv` (one row
per step per run), `e4_secrets.csv` (one row per run checked for the
secret). Charts: `uv run --group bench python -m bench.charts`.

Run from the project folder (about 45 minutes; the Mac can be used
meanwhile, nothing heavy runs on it):

    uv run python -m bench.e4_ci

`--part cache` or `--part secrets` does only one part. `--trial OUT_DIR` is
a quick check (one run with the cache on, one off, no warm-up), written to
OUT_DIR instead of `bench/data/`.
"""

import argparse
import csv
import json
import re
import subprocess
import sys
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from bench.csv_out import CsvOut

DATA = Path(__file__).parent / "data"

# ---- The experiment's design (D108, D109, approved by Adar) -------------------

WORKFLOW = "ci.yml"
RELEASE = "release.yml"
PAIRS = 5  # 5 × (on, off, off, on) = 10 runs with the cache on, 10 off
PATTERN = ["on", "off", "off", "on"]
POLL_SECONDS = 15.0  # how often to ask GitHub whether a run has finished
RUN_LIMIT = 1800.0  # give up on one run after 30 minutes
START_LIMIT = 120.0  # a started run must appear on GitHub within 2 minutes
# After a CI run on main finishes, GitHub starts its Release run a few
# seconds later; wait this long before checking that nothing is running.
SETTLE_SECONDS = 15.0
GH_TRIES = 4  # a failed `gh` call is tried this many times in all ...
RETRY_SECONDS = 10.0  # ... with this long a pause between tries

# Runs that already happened and printed the secret check (D109): the
# `slice-9` branch's pushes and pull request #1 (7 Nov 2026), and the first
# push to main after the merge, with its Release run. Recent pushes to main
# and their Release runs are added by the script itself.
EARLIER_BRANCH = "slice-9"
EARLIER_RUNS = [42380913514, 42381025257]
RECENT_MAIN_PUSHES = 5  # how many of the latest pushes to main to include

RUN_COLUMNS = [
    "order",
    "cache",
    "run_id",
    "url",
    "commit",
    "conclusion",
    "created_at",
    "queue_s",
    "job_s",
    "cache_result",
    "prepared_packages",
    "prepared_s",
    "installed_packages",
    "installed_s",
    "secret_line",
    "error",
]
STEP_COLUMNS = ["order", "cache", "run_id", "step", "log_s", "api_s"]
SECRET_COLUMNS = [
    "workflow",
    "event",
    "branch",
    "run_id",
    "url",
    "created_at",
    "secret_present",
    "line",
    "note",
]

# A function that runs one command and returns its result; tests pass a fake.
Runner = Callable[..., subprocess.CompletedProcess]


def run(command: list[str]) -> subprocess.CompletedProcess:
    """Run one command, keep its output as text, and never raise on failure."""
    return subprocess.run(command, capture_output=True, text=True, check=False)


class Stop(Exception):
    """A problem that makes the measurement meaningless, so the script stops."""


def pause(seconds: float) -> None:
    """Wait between two tries of a `gh` call (tests replace it)."""
    time.sleep(seconds)


def gh(command: list[str], runner: Runner = run) -> subprocess.CompletedProcess:
    """Run a `gh` command that only reads, trying again if it fails.

    A brief problem on GitHub's side (e.g. "HTTP 502") shouldn't end a
    50-minute series, so a failed call is tried up to GH_TRIES times. Only
    for reading: starting a run is never retried, as that could start two.
    """
    for attempt in range(GH_TRIES):
        result = runner(["gh", *command])
        if result.returncode == 0:
            break
        if attempt < GH_TRIES - 1:
            pause(RETRY_SECONDS)
    return result


def gh_json(command: list[str], runner: Runner = run):
    """Run a reading `gh` command that prints JSON, and return what it printed."""
    result = gh(command, runner)
    if result.returncode != 0:
        raise Stop(f"gh {' '.join(command[:3])} failed: {result.stderr.strip()}")
    return json.loads(result.stdout)


# ---- Plain helpers (no GitHub) -------------------------------------------------


def cache_plan(pairs: int = PAIRS) -> list[str]:
    """The order of the recorded runs: on, off, off, on, repeated (D108)."""
    return PATTERN * pairs


def when(text: str) -> datetime:
    """A GitHub time such as 2026-11-09T13:57:50.0187071Z, as a datetime.

    GitHub writes up to 7 digits after the second; Python reads at most 6,
    so the 7th (tenths of a microsecond) is dropped.
    """
    text = text.lstrip("﻿").rstrip("Z")
    if "." in text:
        whole, fraction = text.split(".")
        text = f"{whole}.{fraction[:6]}"
    return datetime.fromisoformat(text + "+00:00")


def seconds_between(start: str, end: str) -> float:
    """Seconds from one GitHub time to another."""
    return (when(end) - when(start)).total_seconds()


def log_lines(log: str) -> list[tuple[str, str, str]]:
    """A run's log as (step, timestamp, text) for every line.

    `gh run view --log` prints "job <tab> step <tab> timestamp text". Some
    lines start with an invisible byte-order mark, which is dropped.
    """
    lines = []
    for line in log.splitlines():
        parts = line.split("\t", 2)
        if len(parts) < 3:
            continue
        stamp, _, text = parts[2].lstrip("﻿").partition(" ")
        lines.append((parts[1], stamp, text))
    return lines


def log_step_seconds(lines: list[tuple[str, str, str]], job_end: str) -> dict:
    """{step: seconds} from the log's timestamps.

    A step lasts from its first log line to the next step's first line; the
    last step lasts until the job's end. GitHub gives the job's end in whole
    seconds (rounded down), so if the last log line is later, that is used.
    """
    starts: dict[str, str] = {}
    for step, stamp, _ in lines:
        starts.setdefault(step, stamp)
    names = list(starts)
    last_line = lines[-1][1]
    end = last_line if when(last_line) > when(job_end) else job_end
    ends = [starts[n] for n in names[1:]] + [end]
    return {
        name: round(seconds_between(starts[name], end), 3)
        for name, end in zip(names, ends, strict=True)
    }


def duration_seconds(amount: str, unit: str) -> float:
    """uv's "1.2s" or "18ms" as seconds."""
    return float(amount) / 1000 if unit == "ms" else float(amount)


def uv_lines(lines: list[tuple[str, str, str]]) -> dict:
    """uv's "Prepared" and "Installed" numbers from the install step."""
    found = {}
    for step, _, text in lines:
        if step != "Install dependencies":
            continue
        match = re.match(
            r"(Prepared|Installed) (\d+) packages? in ([\d.]+)(ms|s)", text
        )
        if match:
            word = match.group(1).lower()
            found[f"{word}_packages"] = int(match.group(2))
            found[f"{word}_s"] = duration_seconds(match.group(3), match.group(4))
    return found


def cache_result(lines: list[tuple[str, str, str]]) -> str:
    """Whether setup-uv found the cache: "hit", "miss" or "off" (not tried)."""
    texts = [text for step, _, text in lines if "setup-uv" in step]
    if any(t.startswith("Cache hit for") for t in texts):
        return "hit"
    if any(t.startswith("Trying to restore cache") for t in texts):
        return "miss"
    return "off"


def secret_line(lines: list[tuple[str, str, str]]) -> str:
    """The secret check's printed line (not the command that prints it)."""
    for _, _, text in lines:
        if re.search(r"secret present: (yes|no)$", text):
            return text.strip()
    return ""


def secret_present(line: str) -> str:
    """ "yes" or "no" from a secret check line; blank if there is none."""
    match = re.search(r"secret present: (yes|no)$", line)
    return match.group(1) if match else ""


# ---- GitHub ------------------------------------------------------------------


def busy_runs(runner: Runner = run) -> list[dict]:
    """Runs of any workflow that are waiting or going right now."""
    busy = []
    for status in ("queued", "in_progress", "waiting", "pending"):
        busy += gh_json(
            ["run", "list", "--status", status, "--json", "databaseId", "-L", "50"],
            runner,
        )
    return busy


def wait_until_quiet(
    runner: Runner = run, sleep: Callable[[float], None] = time.sleep
) -> None:
    """Wait until no run of any workflow is going, so runs never overlap.

    It first waits SETTLE_SECONDS, so that the Release run GitHub starts a
    few seconds after a CI run on main has appeared and is seen as going.
    """
    sleep(SETTLE_SECONDS)
    while busy_runs(runner):
        sleep(POLL_SECONDS)


def start_run(cache: str, runner: Runner = run) -> int:
    """Start one manual CI run on main; return its run id.

    `gh workflow run` prints the new run's address; its last number is the
    run id.
    """
    flag = "true" if cache == "on" else "false"
    result = runner(
        ["gh", "workflow", "run", WORKFLOW, "--ref", "main", "-f", f"uv_cache={flag}"]
    )
    match = re.search(r"/actions/runs/(\d+)", result.stdout)
    if result.returncode != 0 or not match:
        raise Stop(f"could not start a run: {result.stderr.strip() or result.stdout}")
    return int(match.group(1))


def wait_for_run(
    run_id: int,
    runner: Runner = run,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> dict:
    """Wait until a run has finished; return GitHub's details of it.

    Just after a run is started, GitHub may still answer "not found" for a
    few seconds (its records catch up a moment later), so that answer is
    retried for up to START_LIMIT seconds.
    """
    started = clock()
    deadline = started + RUN_LIMIT
    fields = "status,conclusion,headSha,createdAt,url,event,headBranch"
    while True:
        try:
            details = gh_json(["run", "view", str(run_id), "--json", fields], runner)
        except Stop as problem:
            if "Not Found" not in str(problem) or clock() - started > START_LIMIT:
                raise
            sleep(POLL_SECONDS)
            continue
        if details["status"] == "completed":
            return details
        if clock() > deadline:
            raise Stop(f"run {run_id} did not finish within {RUN_LIMIT:.0f} s")
        sleep(POLL_SECONDS)


def job_of(run_id: int, runner: Runner = run) -> dict:
    """The run's one job, with its steps, from GitHub's records."""
    jobs = gh_json(
        ["api", f"repos/{{owner}}/{{repo}}/actions/runs/{run_id}/jobs"], runner
    )["jobs"]
    return jobs[0] if jobs else {}


def run_log(run_id: int, runner: Runner = run) -> str:
    """The run's full log; blank if GitHub has none (e.g. a skipped job)."""
    result = gh(["run", "view", str(run_id), "--log"], runner)
    return result.stdout if result.returncode == 0 else ""


def measure_run(
    order: int, cache: str, run_id: int, details: dict, runner: Runner = run
) -> tuple[dict, list[dict]]:
    """One finished run as a row of e4_runs.csv and rows of e4_steps.csv."""
    row = {
        "order": order,
        "cache": cache,
        "run_id": run_id,
        "url": details["url"],
        "commit": details["headSha"],
        "conclusion": details["conclusion"],
        "created_at": details["createdAt"],
    }
    job = job_of(run_id, runner)
    if not job.get("started_at") or not job.get("completed_at"):
        row["error"] = "no job times"
        return row, []
    row["queue_s"] = seconds_between(details["createdAt"], job["started_at"])
    row["job_s"] = seconds_between(job["started_at"], job["completed_at"])
    lines = log_lines(run_log(run_id, runner))
    if not lines:
        row["error"] = "no log"
        return row, []
    row["cache_result"] = cache_result(lines)
    row["secret_line"] = secret_line(lines)
    row.update(uv_lines(lines))
    log_seconds = log_step_seconds(lines, job["completed_at"])
    steps = []
    for step in job.get("steps", []):
        api = ""
        if step.get("started_at") and step.get("completed_at"):
            api = seconds_between(step["started_at"], step["completed_at"])
        steps.append(
            {
                "order": order,
                "cache": cache,
                "run_id": run_id,
                "step": step["name"],
                "log_s": log_seconds.get(step["name"], ""),
                "api_s": api,
            }
        )
    # A step that ran must appear in the log; otherwise its time would be
    # counted in the step before it, so the run isn't counted.
    missing = [
        s["name"]
        for s in job.get("steps", [])
        if s.get("conclusion") != "skipped" and s["name"] not in log_seconds
    ]
    if details["conclusion"] != "success":
        row["error"] = f"run ended {details['conclusion']}"
    elif missing:
        row["error"] = "steps missing from the log: " + ", ".join(missing)
    return row, steps


def cache_part(
    plan: list[str],
    warm_up: bool,
    write_run: Callable[[dict], None],
    write_step: Callable[[dict], None],
    runner: Runner = run,
    sleep: Callable[[float], None] = time.sleep,
) -> list[int]:
    """Part `cache`: the warm-up, then the planned runs, one at a time.

    Returns the recorded runs' ids (not the warm-up's), so the secrets part
    checks the same runs as the table. Stops if a run tests another commit
    than the first one.
    """
    recorded = []
    commit = ""
    runs = ([("warm-up", "on")] if warm_up else []) + list(enumerate(plan, 1))
    for order, cache in runs:
        wait_until_quiet(runner, sleep)
        run_id = start_run(cache, runner)
        print(f"Run {order} (cache {cache}): {run_id} started", flush=True)
        details = wait_for_run(run_id, runner, sleep)
        commit = commit or details["headSha"]
        if details["headSha"] != commit:
            raise Stop(
                f"run {run_id} tested {details['headSha'][:7]}, not {commit[:7]}: "
                "a commit was pushed during the runs"
            )
        if order == "warm-up":
            print(f"  warm-up done ({details['conclusion']}), not recorded")
            continue
        row, steps = measure_run(order, cache, run_id, details, runner)
        recorded.append(run_id)
        write_run(row)
        for step in steps:
            write_step(step)
        print(
            f"  job {row.get('job_s', '-')} s, cache {row.get('cache_result', '-')}, "
            f"{row['conclusion']}",
            flush=True,
        )
    return recorded


def secret_row(details: dict, run_id: int, workflow: str, runner: Runner = run) -> dict:
    """One run's secret check as a row of e4_secrets.csv."""
    row = {
        "workflow": workflow,
        "event": details.get("event", ""),
        "branch": details.get("headBranch", ""),
        "run_id": run_id,
        "url": details.get("url", ""),
        "created_at": details.get("createdAt", ""),
    }
    job = job_of(run_id, runner)
    if job.get("conclusion") == "skipped":
        row["secret_present"] = "step not run"
        row["note"] = "job skipped"
        return row
    line = secret_line(log_lines(run_log(run_id, runner)))
    row["line"] = line
    row["secret_present"] = secret_present(line) or "no line found"
    return row


def earlier_run_ids(runner: Runner = run) -> list[tuple[int, str]]:
    """(run id, workflow) of the runs that already happened (D109).

    Each Release run is matched to a push by commit: only Release runs that
    published the commit of one of the pushes listed are taken.
    """
    fields = "databaseId,createdAt,headSha"
    branch = gh_json(
        [
            "run",
            "list",
            "-w",
            WORKFLOW,
            "-b",
            EARLIER_BRANCH,
            "-L",
            "20",
            "--json",
            fields,
        ],
        runner,
    )
    pushes = gh_json(
        [
            "run",
            "list",
            "-w",
            WORKFLOW,
            "-b",
            "main",
            "-e",
            "push",
            "-L",
            str(RECENT_MAIN_PUSHES),
            "--json",
            fields,
        ],
        runner,
    )
    # Release runs that published (after a push), for the pushes' commits.
    # Release runs after manual runs are skipped (conclusion "skipped") and
    # are collected separately, so they are left out here.
    releases = gh_json(
        ["run", "list", "-w", RELEASE, "-L", "200", "--json", fields + ",conclusion"],
        runner,
    )
    pushed = {r["headSha"] for r in pushes}
    published = [
        r for r in releases if r["conclusion"] == "success" and r["headSha"] in pushed
    ]
    found = [(r["databaseId"], WORKFLOW) for r in branch + pushes]
    found += [(r["databaseId"], RELEASE) for r in published]
    found += [(i, RELEASE if i == EARLIER_RUNS[1] else WORKFLOW) for i in EARLIER_RUNS]
    unique = {}
    for run_id, workflow in found:
        unique.setdefault(run_id, workflow)
    return sorted(unique.items())


def releases_after(run_ids: list[int], runner: Runner = run) -> list[int]:
    """The Release runs that followed the manual runs (same commit, after them)."""
    if not run_ids:
        return []
    first = gh_json(
        ["run", "view", str(min(run_ids)), "--json", "createdAt,headSha"], runner
    )
    releases = gh_json(
        [
            "run",
            "list",
            "-w",
            RELEASE,
            "-e",
            "workflow_run",
            "-L",
            "100",
            "--json",
            "databaseId,createdAt,headSha",
        ],
        runner,
    )
    return sorted(
        r["databaseId"]
        for r in releases
        if r["headSha"] == first["headSha"]
        and when(r["createdAt"]) >= when(first["createdAt"])
    )


def secrets_part(
    manual_runs: list[int], write: Callable[[dict], None], runner: Runner = run
) -> None:
    """Part `secrets`: earlier runs, the manual runs, and the Releases after them."""
    fields = "createdAt,url,event,headBranch"
    groups = [(i, w, "") for i, w in earlier_run_ids(runner)]
    groups += [(i, WORKFLOW, "manual run (E4 cache part)") for i in manual_runs]
    groups += [
        (i, RELEASE, "after a manual run") for i in releases_after(manual_runs, runner)
    ]
    seen: set[int] = set()
    for run_id, workflow, note in groups:
        # A run found twice (e.g. a recent run that is also an earlier one)
        # is written once.
        if run_id in seen:
            continue
        seen.add(run_id)
        details = gh_json(["run", "view", str(run_id), "--json", fields], runner)
        row = secret_row(details, run_id, workflow, runner)
        if note:
            row["note"] = "; ".join(filter(None, [note, row.get("note", "")]))
        write(row)
        print(f"Secret: {workflow} {row['event']} {run_id}: {row['secret_present']}")


# ---- Output and the command line ---------------------------------------------


def parse_arguments(arguments: list[str] | None = None) -> argparse.Namespace:
    """Read the command line: which part, and where results go."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--part", choices=["cache", "secrets"], action="append")
    parser.add_argument(
        "--trial",
        type=Path,
        metavar="OUT_DIR",
        help="a quick check: one run with the cache on, one off, no warm-up",
    )
    options = parser.parse_args(arguments)
    options.part = options.part or ["cache", "secrets"]
    return options


def main(arguments: list[str] | None = None) -> None:
    """Run the chosen parts and write the CSV files."""
    options = parse_arguments(arguments)
    folder = options.trial or DATA
    plan = ["on", "off"] if options.trial else cache_plan()
    manual_runs: list[int] = []
    try:
        if "cache" in options.part:
            runs = CsvOut(folder / "e4_runs.csv", RUN_COLUMNS)
            steps = CsvOut(folder / "e4_steps.csv", STEP_COLUMNS)
            # The recorded runs only (not the warm-up), as in the table.
            manual_runs = cache_part(plan, not options.trial, runs.write, steps.write)
            runs.close()
            steps.close()
        if "secrets" in options.part:
            # Run alone, the secrets part takes the recorded runs from the
            # file the cache part wrote (the warm-up isn't in it).
            if not manual_runs and (folder / "e4_runs.csv").exists():
                with (folder / "e4_runs.csv").open() as file:
                    manual_runs = [int(r["run_id"]) for r in csv.DictReader(file)]
            secrets = CsvOut(folder / "e4_secrets.csv", SECRET_COLUMNS)
            secrets_part(manual_runs, secrets.write)
            secrets.close()
    except Stop as problem:
        sys.exit(f"Stopped: {problem}")
    print(f"Results written to {folder}.")


if __name__ == "__main__":
    main()
