"""Tests for Slice 15's one-command rerun (`bench/run_all.py`, D113).

Nothing real is started: Docker, `gh`, git and the measuring scripts are
replaced by pretend commands that record what they were asked. The tests
check the prerequisite messages, the order of the parts, that Docker
Desktop is quit before the first part and opened before E2, that a failing
part stops everything, the E3-on-GitHub download, and the three ways to
start the command.
"""

import json
import subprocess
from pathlib import Path

import pytest

from bench import run_all
from bench.run_all import Part, Stop


def done(code: int = 0, out: str = "", err: str = "") -> subprocess.CompletedProcess:
    """A finished pretend command."""
    return subprocess.CompletedProcess([], code, out, err)


class Pretend:
    """A pretend command runner: records commands, answers from a table.

    `docker_up` is whether `docker info` answers; `open -a Docker` and the
    quit command switch it, unless `docker_stuck` is set.
    """

    def __init__(self, answers: dict | None = None, docker_up: bool = False) -> None:
        """`answers`: {first words of a command: result or function}."""
        self.answers = answers or {}
        self.docker_up = docker_up
        self.docker_stuck = False
        self.commands: list[list[str]] = []

    def __call__(self, command: list[str]) -> subprocess.CompletedProcess:
        """Answer one command."""
        self.commands.append(command)
        if command[:2] == ["docker", "info"]:
            return done(0 if self.docker_up else 1)
        if command[:3] == ["open", "-a", "Docker"] and not self.docker_stuck:
            self.docker_up = True
        if command[0] == "osascript" and not self.docker_stuck:
            self.docker_up = False
        for start, answer in self.answers.items():
            if tuple(command[: len(start)]) == start:
                return answer(command) if callable(answer) else answer
        return done()


NO_WAIT = {"sleep": lambda seconds: None}


class Clock:
    """A pretend clock that moves 60 s every time it is read."""

    def __init__(self) -> None:
        """Start at 0."""
        self.now = 0.0

    def __call__(self) -> float:
        """The time, then move on."""
        self.now += 60.0
        return self.now


# ---- Docker -------------------------------------------------------------------------


def test_open_and_quit_docker_wait_for_the_engine():
    """Opening waits until `docker info` answers; quitting until it doesn't."""
    pretend = Pretend()
    run_all.open_docker(pretend, **NO_WAIT)
    assert pretend.docker_up
    run_all.quit_docker(pretend, **NO_WAIT)
    assert not pretend.docker_up
    assert ["osascript", "-e", 'quit app "Docker"'] in pretend.commands


def test_docker_that_never_starts_stops_the_rerun():
    """Waiting has a limit; then a clear message."""
    pretend = Pretend()
    pretend.docker_stuck = True
    with pytest.raises(Stop, match="did not start"):
        run_all.open_docker(pretend, sleep=lambda s: None, clock=Clock())


def test_docker_that_never_quits_stops_the_rerun():
    """Quitting has a limit too."""
    pretend = Pretend(docker_up=True)
    pretend.docker_stuck = True
    with pytest.raises(Stop, match="did not quit"):
        run_all.quit_docker(pretend, sleep=lambda s: None, clock=Clock())


def test_check_docker_when_not_installed(monkeypatch, tmp_path):
    """No Docker Desktop app: say so."""
    monkeypatch.setattr(run_all, "DOCKER_APP", tmp_path / "missing.app")
    assert "not installed" in run_all.check_docker()[0]


def test_check_docker_never_opens_it(monkeypatch, tmp_path):
    """Installed is enough: the check doesn't open Docker Desktop.

    The first parts need it closed; the rerun opens it itself before E2.
    """
    monkeypatch.setattr(run_all, "DOCKER_APP", tmp_path)
    monkeypatch.setattr(run_all.shutil, "which", lambda name: "/usr/bin/docker")
    monkeypatch.setattr(run_all, "run", lambda command: pytest.fail(str(command)))
    assert run_all.check_docker() == []


def test_check_docker_needs_the_docker_command(monkeypatch, tmp_path):
    """The app without its command-line tool counts as not installed."""
    monkeypatch.setattr(run_all, "DOCKER_APP", tmp_path)
    monkeypatch.setattr(run_all.shutil, "which", lambda name: None)
    assert "not installed" in run_all.check_docker()[0]


# ---- Other prerequisites ------------------------------------------------------------


def test_git_must_be_clean_and_pushed():
    """Uncommitted changes and an unpushed commit are both named."""
    dirty = Pretend(
        {
            ("git", "status"): done(out=" M app/main.py\n"),
            ("git", "rev-parse"): done(out="aaa\n"),
            ("git", "ls-remote"): done(out="bbb\trefs/heads/main\n"),
        }
    )
    problems = run_all.check_git(dirty)
    assert len(problems) == 2
    assert "uncommitted" in problems[0] and "git push" in problems[1]
    clean = Pretend(
        {
            ("git", "rev-parse"): done(out="aaa\n"),
            ("git", "ls-remote"): done(out="aaa\trefs/heads/main\n"),
        }
    )
    assert run_all.check_git(clean) == []


def test_no_main_branch_on_github_counts_as_not_pushed():
    """An empty answer from GitHub is not "pushed"."""
    pretend = Pretend({("git", "rev-parse"): done(out="aaa\n")})
    assert "git push" in run_all.check_git(pretend)[0]


def test_gh_must_be_logged_in():
    """`gh auth status` failing gives the login command."""
    assert "gh auth login" in run_all.check_gh(Pretend({("gh", "auth"): done(1)}))[0]
    assert run_all.check_gh(Pretend()) == []


def test_charts_library_must_be_there(monkeypatch):
    """Started without `--group bench`: say how to start it."""
    monkeypatch.setattr(run_all.importlib.util, "find_spec", lambda name: None)
    assert "--group bench" in run_all.check_charts_library()[0]


def test_check_everything_lists_every_problem_once(monkeypatch, tmp_path):
    """All problems at once; the charger line only once."""
    monkeypatch.setattr(run_all.measure_ollama, "plugged_in", lambda: False)
    monkeypatch.setattr(run_all.measure_ollama, "model_digest", lambda: "wrong")
    monkeypatch.setattr(run_all.measure_ollama, "ollama_parallel_setting", lambda: 8)
    monkeypatch.setattr(run_all.e1_load, "check_limits", lambda: ["Raise somaxconn."])
    monkeypatch.setattr(run_all, "DOCKER_APP", tmp_path / "missing.app")
    monkeypatch.setattr(run_all, "check_charts_library", lambda: [])
    pretend = Pretend({("gh", "auth"): done(1), ("git", "status"): done(out="x")})
    problems = run_all.check_everything(pretend)
    assert sum("battery" in p for p in problems) == 1
    for words in [
        "not the pinned one",
        "somaxconn",
        "Docker",
        "gh auth",
        "uncommitted",
    ]:
        assert any(words in p for p in problems), words


# ---- The plan and running it --------------------------------------------------------


def test_full_plan_runs_docker_free_parts_first():
    """Closed-Docker parts, then open-Docker parts, then the GitHub ones (D113)."""
    plan = run_all.full_plan()
    needs = [p.docker for p in plan]
    assert needs == [False, False, False, False, True, True, None, None]
    commands = [p.command for p in plan if p.command]
    assert all(c[:4] == ["uv", "run", "python", "-m"] for c in commands)
    modules = [c[4] for c in commands]
    assert modules == [
        "bench.measure_ollama",
        "bench.e1_load",
        "bench.e1_load",
        "bench.cost_tr_en",
        "bench.e2_kill",
        "bench.e3_build",
        "bench.e4_ci",
    ]
    assert plan[6].action is run_all.e3_on_github
    assert sum(p.minutes for p in plan) > 300


def test_run_plan_quits_docker_first_opens_it_once_and_times_each_part(capsys):
    """Docker quit before part 1, opened before E2, never toggled again."""
    pretend = Pretend(docker_up=True)
    ran = []
    plan = [
        Part("one", 1, False, ["one"]),
        Part("two", 1, False, ["two"]),
        Part("three", 1, True, ["three"]),
        Part("four", 1, True, ["four"]),
        Part("five", 1, None, action=lambda: ran.append("five")),
    ]
    times = run_all.run_plan(
        plan,
        live=lambda command: ran.append(command[0]) or 0,
        runner=pretend,
        clock=Clock(),
        **NO_WAIT,
    )
    assert ran == ["one", "two", "three", "four", "five"]
    switches = [c[0] for c in pretend.commands if c[0] in ("open", "osascript")]
    assert switches == ["osascript", "open"]
    assert [name for name, _ in times] == ["one", "two", "three", "four", "five"]
    assert all(seconds == 60.0 for _, seconds in times)
    assert "took 1.0 min" in capsys.readouterr().out


def test_a_failing_part_stops_the_rerun_and_says_which(capsys):
    """Part two fails: part three never runs; the summary names part two."""
    ran = []
    plan = [Part(n, 1, None, [n]) for n in ["one", "two", "three"]]
    with pytest.raises(Stop, match="two failed"):
        run_all.run_plan(
            plan,
            live=lambda command: ran.append(command[0]) or (command[0] == "two"),
            runner=Pretend(),
            clock=Clock(),
        )
    assert ran == ["one", "two"]
    out = capsys.readouterr().out
    assert "two: FAILED" in out and "one: 1.0 min" in out


def test_an_unexpected_error_in_a_part_still_prints_the_summary(capsys):
    """Not only a failed command: any error first shows what was done."""

    def broken():
        raise KeyError("oops")

    plan = [Part("one", 1, None, ["one"]), Part("two", 1, None, action=broken)]
    with pytest.raises(KeyError):
        run_all.run_plan(plan, live=lambda command: 0, runner=Pretend(), clock=Clock())
    out = capsys.readouterr().out
    assert "one: 1.0 min" in out and "two: FAILED" in out


def test_minutes_are_readable():
    """Short in minutes, long in hours and minutes."""
    assert run_all.minutes(90) == "1.5 min"
    assert run_all.minutes(5 * 3600 + 20 * 60) == "5 h 20 min"


def test_keep_awake_watches_this_process():
    """caffeinate ends by itself when this script ends."""
    started = []
    run_all.keep_awake(started.append)
    assert started[0][:3] == ["caffeinate", "-dims", "-w"]
    assert started[0][3] == str(run_all.os.getpid())


# ---- E3 on GitHub -------------------------------------------------------------------

RUN_URL = "https://github.com/adarpalaoglu/kuyruk/actions/runs/123\n"


def github(conclusion: str = "success", files: tuple = tuple(run_all.E3_FILES)):
    """A pretend `gh`: starts run 123, says it finished, downloads `files`."""

    def download(command):
        folder = Path(command[command.index("-D") + 1])
        for name in files:
            (folder / name).write_text(f"{name}\n")
        return done()

    view = {"status": "completed", "conclusion": conclusion}
    return Pretend(
        {
            ("gh", "workflow", "run"): done(out=RUN_URL),
            ("gh", "run", "view"): done(out=json.dumps(view)),
            ("gh", "run", "download"): download,
        }
    )


def test_e3_on_github_downloads_both_files(monkeypatch, tmp_path):
    """Run started on main, waited for, both CSV files copied into the data."""
    monkeypatch.setattr(run_all, "DATA", tmp_path)
    pretend = github()
    run_all.e3_on_github(pretend, **NO_WAIT)
    assert ["gh", "workflow", "run", "e3.yml", "--ref", "main"] in pretend.commands
    for name in run_all.E3_FILES:
        assert (tmp_path / name).read_text() == f"{name}\n"


def test_e3_on_github_that_fails_keeps_the_old_files(monkeypatch, tmp_path):
    """A failed run stops the rerun and nothing is copied."""
    monkeypatch.setattr(run_all, "DATA", tmp_path)
    with pytest.raises(Stop, match="failure"):
        run_all.e3_on_github(github("failure"), **NO_WAIT)
    assert list(tmp_path.iterdir()) == []


def test_e3_on_github_with_a_missing_file_keeps_the_old_files(monkeypatch, tmp_path):
    """Both files or none."""
    monkeypatch.setattr(run_all, "DATA", tmp_path)
    with pytest.raises(Stop, match="lacks"):
        run_all.e3_on_github(github(files=("e3_builds_ci.csv",)), **NO_WAIT)
    assert list(tmp_path.iterdir()) == []


def test_e3_on_github_that_cannot_start():
    """`gh workflow run` failing gives its error."""
    pretend = Pretend({("gh", "workflow", "run"): done(1, err="HTTP 403")})
    with pytest.raises(Stop, match="HTTP 403"):
        run_all.e3_on_github(pretend, **NO_WAIT)


def test_e3_on_github_whose_download_fails(monkeypatch):
    """A failed download (after retries) stops the rerun."""
    monkeypatch.setattr(run_all.e4_ci, "pause", lambda seconds: None)
    pretend = github()
    pretend.answers[("gh", "run", "download")] = done(1, err="no artifact")
    with pytest.raises(Stop, match="no artifact"):
        run_all.e3_on_github(pretend, **NO_WAIT)


def test_e3_on_github_whose_status_cannot_be_read(monkeypatch):
    """E4's reading error is turned into this script's Stop."""
    monkeypatch.setattr(run_all.e4_ci, "pause", lambda seconds: None)
    pretend = github()
    pretend.answers[("gh", "run", "view")] = done(1, err="HTTP 500")
    with pytest.raises(Stop, match="HTTP 500"):
        run_all.e3_on_github(pretend, **NO_WAIT)


# ---- Charts and the number check ----------------------------------------------------


def test_charts_and_check_reports_differences(monkeypatch, capsys):
    """Each differing number is listed; the count is returned."""
    monkeypatch.setattr(
        run_all.check_numbers, "check", lambda **_: (10, ["E4: doc 90 s, data 91 s"])
    )
    assert run_all.charts_and_check(lambda: 0) == 1
    assert "1 of 10 numbers" in capsys.readouterr().out


def test_charts_and_check_all_match(monkeypatch, capsys):
    """No difference: one line saying so."""
    monkeypatch.setattr(run_all.check_numbers, "check", lambda **_: (10, []))
    assert run_all.charts_and_check(lambda: 0) == 0
    assert "All 10 numbers" in capsys.readouterr().out


def test_charts_that_fail_to_draw_stop():
    """A chart error is never hidden behind the number check."""
    with pytest.raises(Stop, match="charts failed"):
        run_all.charts_and_check(lambda: 1)


# ---- The command line ---------------------------------------------------------------


def test_the_two_options_cannot_be_combined():
    """Charts only and check only are different jobs."""
    with pytest.raises(SystemExit):
        run_all.parse_arguments(["--charts-only", "--check-only"])


def forbid(*names):
    """Replacements that fail the test if a full-run step is reached."""

    def fail(*args, **kwargs):
        raise AssertionError("must not run")

    return dict.fromkeys(names, fail)


@pytest.mark.parametrize(("differ", "code"), [(0, 0), (3, 1)])
def test_charts_only_touches_nothing_else(monkeypatch, differ, code):
    """No prerequisites, no measuring; exit code 1 if numbers differ."""
    for name, fake in forbid("check_everything", "run_plan", "keep_awake").items():
        monkeypatch.setattr(run_all, name, fake)
    monkeypatch.setattr(run_all, "charts_and_check", lambda: differ)
    with pytest.raises(SystemExit) as stopped:
        run_all.main(["--charts-only"])
    assert stopped.value.code == code


def test_check_only_stops_after_the_prerequisites(monkeypatch, capsys):
    """Prints the plan with times; runs nothing."""
    for name, fake in forbid("run_plan", "keep_awake", "charts_and_check").items():
        monkeypatch.setattr(run_all, name, fake)
    monkeypatch.setattr(run_all, "check_everything", lambda: [])
    run_all.main(["--check-only"])
    out = capsys.readouterr().out
    assert "All prerequisites are met." in out
    assert "Expected in all: about 5 h" in out


def test_problems_stop_before_anything_runs(monkeypatch, capsys):
    """Every problem listed; exit code 1."""
    monkeypatch.setattr(run_all, "run_plan", forbid("x")["x"])
    monkeypatch.setattr(run_all, "check_everything", lambda: ["Plug in.", "Log in."])
    with pytest.raises(SystemExit) as stopped:
        run_all.main([])
    assert stopped.value.code == 1
    out = capsys.readouterr().out
    assert "Plug in." in out and "Log in." in out


def test_full_rerun_then_charts_and_the_differences(monkeypatch, capsys):
    """Runs the plan, then the charts; tells to update the doc if numbers moved."""
    monkeypatch.setattr(run_all, "check_everything", lambda: [])
    monkeypatch.setattr(run_all, "keep_awake", lambda: None)
    monkeypatch.setattr(run_all, "run_plan", lambda plan: [("E1", 60.0)])
    monkeypatch.setattr(run_all, "charts_and_check", lambda: 2)
    run_all.main([])
    out = capsys.readouterr().out
    assert "update the doc and its verdicts by hand" in out
    assert "E1: 1.0 min" in out


def test_full_rerun_that_stops(monkeypatch):
    """A failed part ends the command with the reason."""

    def broken(plan):
        raise Stop("E2 failed.")

    monkeypatch.setattr(run_all, "check_everything", lambda: [])
    monkeypatch.setattr(run_all, "keep_awake", lambda: None)
    monkeypatch.setattr(run_all, "run_plan", broken)
    with pytest.raises(SystemExit, match="Stopped: E2 failed."):
        run_all.main([])


def test_charts_failing_after_the_rerun_still_print_the_times(monkeypatch, capsys):
    """Hours of measuring are saved; their times are shown before stopping."""

    def broken():
        raise Stop("Drawing the charts failed.")

    monkeypatch.setattr(run_all, "check_everything", lambda: [])
    monkeypatch.setattr(run_all, "keep_awake", lambda: None)
    monkeypatch.setattr(run_all, "run_plan", lambda plan: [("E2", 7800.0)])
    monkeypatch.setattr(run_all, "charts_and_check", broken)
    with pytest.raises(SystemExit, match="Drawing the charts failed"):
        run_all.main([])
    out = capsys.readouterr().out
    assert "E2: 2 h 10 min" in out
    assert "Charts and number check: FAILED" in out
