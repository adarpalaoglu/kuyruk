"""One command that reruns every Kuyruk experiment and redraws every chart.

Slice 15 (spec 9.5, D113). Kuyruk's measurements live in separate scripts
(`bench/measure_ollama.py`, `e1_load.py`, `cost_tr_en.py`, `e2_kill.py`,
`e3_build.py`, `e4_ci.py`). This script runs them all, one after another, in
a fixed order, each exactly as its own documentation says, and stops at the
first one that fails. Then it redraws the charts and checks that the numbers
written in `docs/experiments.md` still match the data (`bench/check_numbers.py`,
D114).

Before anything runs it checks everything a full rerun needs and lists every
problem at once, with what to do: the charger, Ollama with the pinned model
and its parallel setting, Docker Desktop, macOS's connection limit, the
GitHub login (`gh`), and that the project folder is committed and pushed (the
GitHub runs test the pushed commit). It prints each part's expected time
before starting, how long each part really took, and a summary at the end.

Docker Desktop: the AI measurements, E1 and the cost count were made with
Docker Desktop closed, while E2 and E3 need it open. So this script quits
Docker Desktop before the first part and opens it again before E2 (D113).
It also keeps the Mac awake (macOS's `caffeinate`) for the whole run.

A full rerun takes about 5 h 20 min. Run from the project folder:

    uv run --group bench python -m bench.run_all

Options:
- `--charts-only`: only redraw the charts from the saved CSV files and check
  the numbers in `docs/experiments.md` (no Ollama, Docker or GitHub needed;
  about a minute). Exit code 1 if any number differs.
- `--check-only`: only check the prerequisites of a full rerun.

After a full rerun the new data will usually differ a little from the old,
so the number check lists what changed; `docs/experiments.md` (and its
verdicts) must then be updated by hand. Nothing is changed automatically.
"""

import argparse
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from bench import check_numbers, e1_load, e4_ci, measure_ollama

ROOT = Path(__file__).parent.parent
DATA = Path(__file__).parent / "data"

# Docker Desktop: how long to wait for it to start or stop answering.
DOCKER_APP = Path("/Applications/Docker.app")
DOCKER_START_LIMIT = 300.0
DOCKER_STOP_LIMIT = 120.0
DOCKER_POLL = 3.0

# E3 on GitHub: its manual workflow and the download it leaves (D107).
E3_WORKFLOW = "e3.yml"
E3_ARTIFACT = "e3-results"
E3_FILES = ["e3_builds_ci.csv", "e3_images_ci.csv"]

# Runs a command and returns its result; tests pass a pretend one.
Runner = Callable[[list[str]], subprocess.CompletedProcess]


@dataclass
class Part:
    """One step of the full rerun.

    `command` is run as is (output shown live); `action` is used instead for
    steps that aren't one command (E3 on GitHub, the charts). `docker` says
    whether Docker Desktop must be open (True), closed (False), or doesn't
    matter (None). `minutes` is the expected time, from the earlier runs.
    """

    name: str
    minutes: float
    docker: bool | None
    command: list[str] | None = None
    action: Callable[[], None] | None = None


class Stop(Exception):
    """A part failed or a prerequisite broke; the rerun stops here."""


def run(command: list[str]) -> subprocess.CompletedProcess:
    """Run a command quietly, keep its output as text, never raise."""
    return subprocess.run(command, capture_output=True, text=True, check=False)


def run_live(command: list[str]) -> int:
    """Run a command with its output shown as it goes; return its exit code."""
    return subprocess.run(command, cwd=ROOT, check=False).returncode


def uv_module(module: str, *arguments: str) -> list[str]:
    """The documented command for one measuring script."""
    return ["uv", "run", "python", "-m", module, *arguments]


# ---- Prerequisites -------------------------------------------------------------


def docker_answers(runner: Runner = run) -> bool:
    """True if Docker's engine answers (Docker Desktop is open and ready)."""
    return runner(["docker", "info"]).returncode == 0


def wait_for_docker(
    want: bool,
    limit: float,
    runner: Runner = run,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> bool:
    """Wait until Docker answers (`want=True`) or stops answering; False on timeout."""
    deadline = clock() + limit
    while docker_answers(runner) != want:
        if clock() > deadline:
            return False
        sleep(DOCKER_POLL)
    return True


def open_docker(runner: Runner = run, **waiting) -> None:
    """Open Docker Desktop and wait until its engine answers."""
    runner(["open", "-a", "Docker"])
    if not wait_for_docker(True, DOCKER_START_LIMIT, runner, **waiting):
        raise Stop(f"Docker Desktop did not start within {DOCKER_START_LIMIT:.0f} s.")


def quit_docker(runner: Runner = run, **waiting) -> None:
    """Quit Docker Desktop and wait until its engine has stopped."""
    runner(["osascript", "-e", 'quit app "Docker"'])
    if not wait_for_docker(False, DOCKER_STOP_LIMIT, runner, **waiting):
        raise Stop(f"Docker Desktop did not quit within {DOCKER_STOP_LIMIT:.0f} s.")


def check_docker() -> list[str]:
    """Docker Desktop is installed (the app and its `docker` command).

    It is not opened here: the first parts need it closed, and the rerun
    opens it itself before E2 (if it then fails to start, the rerun stops).
    """
    if not DOCKER_APP.exists() or shutil.which("docker") is None:
        return ["Docker Desktop is not installed. Install it, open it once, try again."]
    return []


def check_git(runner: Runner = run) -> list[str]:
    """The project is committed and this exact commit is on GitHub's main.

    E3 and E4 on GitHub test what is on GitHub, so the Mac's runs and the
    GitHub runs must be the same commit.
    """
    problems = []
    if runner(["git", "status", "--porcelain"]).stdout.strip():
        problems.append(
            "The project has uncommitted changes. Commit them (or ask Claude "
            "to), then try again."
        )
    here = runner(["git", "rev-parse", "HEAD"]).stdout.strip()
    remote = runner(["git", "ls-remote", "origin", "refs/heads/main"]).stdout.split()
    if not remote or remote[0] != here:
        problems.append(
            "This commit is not the newest on GitHub's main. Type: ! git push "
            "origin main  (or switch to main if you are on another branch)."
        )
    return problems


def check_gh(runner: Runner = run) -> list[str]:
    """The GitHub command-line tool is logged in."""
    if runner(["gh", "auth", "status"]).returncode != 0:
        return ["GitHub's `gh` is not logged in. Type: ! gh auth login"]
    return []


def check_charts_library() -> list[str]:
    """matplotlib is there (the command was started with `--group bench`)."""
    if importlib.util.find_spec("matplotlib") is None:
        return [
            "The charting library is missing. Start the command as: uv run "
            "--group bench python -m bench.run_all"
        ]
    return []


def check_everything(runner: Runner = run) -> list[str]:
    """Every prerequisite of a full rerun; returns every problem found."""
    problems = []
    if not measure_ollama.plugged_in():
        problems.append(measure_ollama.CHARGER_PROBLEM)
    # Ollama, the pinned model and its parallel setting; the charger is above.
    problems += measure_ollama.check_setup(charger=False)
    problems += e1_load.check_limits()
    problems += check_docker()
    problems += check_gh(runner)
    problems += check_git(runner)
    problems += check_charts_library()
    return problems


# ---- The parts that aren't one command -----------------------------------------


def e3_on_github(runner: Runner = run, **waiting) -> None:
    """Start E3's GitHub workflow, wait for it, download its two CSV files.

    The files replace `bench/data/e3_*_ci.csv` only after both downloaded.
    """
    # Starting is never retried: a retry could start two runs.
    started = runner(["gh", "workflow", "run", E3_WORKFLOW, "--ref", "main"])
    # `gh workflow run` prints the new run's address; its last number is the id.
    match = re.search(r"/actions/runs/(\d+)", started.stdout)
    if started.returncode != 0 or not match:
        raise Stop(f"Could not start {E3_WORKFLOW}: {started.stderr.strip()}")
    run_id = int(match.group(1))
    print(f"E3 on GitHub: run {run_id} started; waiting for it to finish.")
    try:
        details = e4_ci.wait_for_run(run_id, runner, **waiting)
    except e4_ci.Stop as problem:
        raise Stop(str(problem)) from problem
    if details.get("conclusion") != "success":
        raise Stop(f"E3 on GitHub (run {run_id}) ended: {details.get('conclusion')}.")
    with tempfile.TemporaryDirectory() as folder:
        download = e4_ci.gh(
            ["run", "download", str(run_id), "-n", E3_ARTIFACT, "-D", folder], runner
        )
        if download.returncode != 0:
            raise Stop(f"Could not download {E3_ARTIFACT}: {download.stderr.strip()}")
        found = {path.name: path for path in Path(folder).rglob("*.csv")}
        missing = [name for name in E3_FILES if name not in found]
        if missing:
            raise Stop(f"{E3_ARTIFACT} lacks {', '.join(missing)}.")
        for name in E3_FILES:
            shutil.copyfile(found[name], DATA / name)


def charts_and_check(drawer: Callable[[], int] | None = None) -> int:
    """Redraw every chart from the CSV files, then check the doc's numbers.

    Returns how many numbers differ (0 = all match).
    """
    draw = drawer or (lambda: run_live([sys.executable, "-m", "bench.charts"]))
    if draw() != 0:
        raise Stop("Drawing the charts failed.")
    return check_numbers.report()


# ---- The full rerun -------------------------------------------------------------


def full_plan() -> list[Part]:
    """Every part, in order (D113): Docker closed first, then open."""
    return [
        Part("Real AI times (Slice 10)", 50, False, uv_module("bench.measure_ollama")),
        Part("E1 blocking the event loop", 65, False, uv_module("bench.e1_load")),
        Part(
            "E1 after the fix (D99)",
            2,
            False,
            uv_module("bench.e1_load", "--after-fix"),
        ),
        Part("Turkish vs English cost", 3, False, uv_module("bench.cost_tr_en")),
        Part("E2 losing work", 130, True, uv_module("bench.e2_kill")),
        Part("E3 on the Mac", 11, True, uv_module("bench.e3_build")),
        Part("E3 on GitHub", 12, None, action=e3_on_github),
        Part("E4 CI on GitHub", 50, None, uv_module("bench.e4_ci")),
    ]


def minutes(seconds: float) -> str:
    """A duration as "1 h 05 min" or "12.5 min"."""
    if seconds >= 3600:
        hours, rest = divmod(round(seconds / 60), 60)
        return f"{hours} h {rest:02d} min"
    return f"{seconds / 60:.1f} min"


def print_plan(plan: list[Part]) -> None:
    """The parts and their expected times, before anything starts."""
    print("Full rerun, in this order:")
    for number, part in enumerate(plan, 1):
        print(f"  {number}. {part.name}: about {minutes(part.minutes * 60)}")
    print(f"Expected in all: about {minutes(sum(p.minutes for p in plan) * 60)}.")


def print_summary(done: list[tuple[str, float]], failed: str | None) -> None:
    """How long each part took, and which one failed (if any)."""
    print("\nTime per part:")
    for name, seconds in done:
        print(f"  {name}: {minutes(seconds)}")
    if failed:
        print(f"  {failed}: FAILED (the rerun stopped here)")
    print(f"In all: {minutes(sum(seconds for _, seconds in done))}.")


def set_docker(
    want: bool | None, is_open: bool | None, runner: Runner = run, **waiting
) -> bool | None:
    """Open or quit Docker Desktop if a part needs it; return its new state."""
    if want is None or want == is_open:
        return is_open
    if want:
        print("Opening Docker Desktop (E2 and E3 need it).")
        open_docker(runner, **waiting)
    else:
        print("Quitting Docker Desktop (closed, as in the original runs).")
        quit_docker(runner, **waiting)
    return want


def run_plan(
    plan: list[Part],
    live: Callable[[list[str]], int] = run_live,
    runner: Runner = run,
    clock: Callable[[], float] = time.monotonic,
    **waiting,
) -> list[tuple[str, float]]:
    """Run each part in order; stop at the first failure (raises Stop)."""
    done: list[tuple[str, float]] = []
    docker_open: bool | None = None  # not known yet: set before the first part
    for part in plan:
        try:
            docker_open = set_docker(part.docker, docker_open, runner, **waiting)
            print(f"\n=== {part.name} (about {minutes(part.minutes * 60)}) ===")
            start = clock()
            if part.action is not None:
                part.action()
            elif live(part.command) != 0:
                raise Stop(f"{part.name} failed (see its output above).")
            done.append((part.name, clock() - start))
            print(f"=== {part.name}: took {minutes(done[-1][1])} ===")
        except BaseException:
            # Whatever stops the rerun (a failed part, an unexpected error,
            # Ctrl+C), first show what was already done and how long it took.
            print_summary(done, part.name)
            raise
    return done


def keep_awake(runner: Callable[[list[str]], object] = subprocess.Popen) -> None:
    """Keep the Mac awake until this script ends (`caffeinate -w` our process)."""
    runner(["caffeinate", "-dims", "-w", str(os.getpid())])


# ---- Command line -----------------------------------------------------------------


def parse_arguments(arguments: list[str] | None = None) -> argparse.Namespace:
    """Read the command-line options."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    only = parser.add_mutually_exclusive_group()
    only.add_argument(
        "--charts-only",
        action="store_true",
        help="only redraw the charts and check the numbers in docs/experiments.md",
    )
    only.add_argument(
        "--check-only",
        action="store_true",
        help="only check the prerequisites of a full rerun",
    )
    return parser.parse_args(arguments)


def main(arguments: list[str] | None = None) -> None:
    """Charts only, prerequisites only, or the full rerun."""
    options = parse_arguments(arguments)
    if options.charts_only:
        sys.exit(1 if charts_and_check() else 0)
    print("Checking the prerequisites of a full rerun...")
    problems = check_everything()
    if problems:
        print("Not ready:", *problems, sep="\n- ")
        sys.exit(1)
    print("All prerequisites are met.")
    plan = full_plan()
    print_plan(plan)
    if options.check_only:
        return
    keep_awake()
    try:
        done = run_plan(plan)
    except Stop as problem:
        # run_plan has already printed the time per part.
        sys.exit(f"Stopped: {problem}")
    start = time.monotonic()
    print("\n=== Charts and number check ===")
    try:
        differ = charts_and_check()
    except Exception as problem:
        # The measurements are saved; show their times before stopping.
        print_summary(done, "Charts and number check")
        sys.exit(f"Stopped: {problem}")
    done.append(("Charts and number check", time.monotonic() - start))
    print_summary(done, None)
    if differ:
        print(
            "The measurements finished. The new data differs from the numbers "
            "in docs/experiments.md (listed above): update the doc and its "
            "verdicts by hand."
        )


if __name__ == "__main__":
    main()
