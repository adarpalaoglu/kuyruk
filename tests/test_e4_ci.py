"""Tests for experiment E4's script (`bench/e4_ci.py`).

No GitHub is needed: every `gh` command goes to a pretend runner
(`FakeGh`) that records it and answers like GitHub would, with small
made-up logs in GitHub's format.
"""

import json
import subprocess

import pytest

from bench import e4_ci
from bench.e4_ci import (
    RUN_COLUMNS,
    SECRET_COLUMNS,
    STEP_COLUMNS,
    Stop,
    cache_part,
    cache_plan,
    cache_result,
    log_lines,
    log_step_seconds,
    measure_run,
    seconds_between,
    secret_line,
    secrets_part,
    uv_lines,
)


@pytest.fixture(autouse=True)
def no_pauses(monkeypatch):
    """No real waiting between retries of a `gh` call in these tests."""
    monkeypatch.setattr(e4_ci, "pause", lambda seconds: None)


def done(stdout="", returncode=0, stderr=""):
    """A finished command's result, as subprocess.run would return it."""
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


def log(*lines):
    """A log in `gh run view --log` form: job, step, timestamp and text."""
    return "\n".join(f"test\t{step}\t{stamp} {text}" for step, stamp, text in lines)


CACHE_ON_LOG = log(
    ("Set up job", "2026-11-09T13:57:50.0000000Z", "Runner"),
    ("Secret check (E4)", "2026-11-09T13:58:01.0000000Z", 'echo "… $present"'),
    (
        "Secret check (E4)",
        "2026-11-09T13:58:01.0100000Z",
        "event: workflow_dispatch · from fork: false · secret present: yes",
    ),
    (
        "Run astral-sh/setup-uv@x",
        "2026-11-09T13:58:01.5000000Z",
        "Trying to restore cache",
    ),
    ("Run astral-sh/setup-uv@x", "2026-11-09T13:58:02.0000000Z", "Cache hit for: key"),
    ("Install dependencies", "2026-11-09T13:58:04.0000000Z", "uv sync --locked"),
    (
        "Install dependencies",
        "2026-11-09T13:58:04.0100000Z",
        "Installed 43 packages in 18ms",
    ),
    ("Fast tests", "2026-11-09T13:58:04.2500000Z", "pytest"),
    ("Complete job", "﻿2026-11-09T13:59:05.5000000Z", "Cleaning up"),
)

CACHE_OFF_LOG = log(
    ("Set up job", "2026-11-09T14:00:00.0000000Z", "Runner"),
    ("Run astral-sh/setup-uv@x", "2026-11-09T14:00:01.0000000Z", "enable-cache: false"),
    ("Install dependencies", "2026-11-09T14:00:02.0000000Z", "uv sync --locked"),
    (
        "Install dependencies",
        "2026-11-09T14:00:03.0000000Z",
        "Prepared 35 packages in 1.25s",
    ),
    (
        "Install dependencies",
        "2026-11-09T14:00:03.1000000Z",
        "Installed 43 packages in 20ms",
    ),
    ("Complete job", "2026-11-09T14:01:00.0000000Z", "Cleaning up"),
)


def job(start="2026-11-09T13:57:50Z", end="2026-11-09T13:59:05Z", conclusion="success"):
    """GitHub's record of a run's one job, with two steps."""
    return {
        "jobs": [
            {
                "started_at": start,
                "completed_at": end,
                "conclusion": conclusion,
                "steps": [
                    {
                        "name": "Set up job",
                        "started_at": start,
                        "completed_at": "2026-11-09T13:57:51Z",
                    },
                    {"name": "Install dependencies", "started_at": None},
                ],
            }
        ]
    }


def details(run_id, sha="abc1234", status="completed", conclusion="success"):
    """GitHub's details of one run, as `gh run view --json` prints them."""
    return {
        "status": status,
        "conclusion": conclusion,
        "headSha": sha,
        "createdAt": "2026-11-09T13:57:45Z",
        "url": f"https://github.com/o/r/actions/runs/{run_id}",
        "event": "workflow_dispatch",
        "headBranch": "main",
    }


class FakeGh:
    """A pretend `gh`: starts runs, reports them finished, gives logs and jobs.

    `shas` lists the commit each started run tests (in order); `busy` is how
    many times "is anything running?" answers yes before it says no.
    """

    def __init__(self, shas=None, busy=0, logs=None, jobs=None):
        self.commands = []
        self.shas = shas or []
        self.busy = busy
        self.logs = logs or {}
        self.jobs = jobs or {}
        self.started = []

    def __call__(self, command):
        self.commands.append(command)
        text = " ".join(command)
        if text.startswith("gh run list --status"):
            if self.busy and "queued" in text:
                self.busy -= 1
                return done(json.dumps([{"databaseId": 9}]))
            return done("[]")
        if text.startswith("gh workflow run"):
            run_id = 100 + len(self.started)
            self.started.append((run_id, command[-1]))
            return done(f"https://github.com/o/r/actions/runs/{run_id}\n")
        if text.startswith("gh run view") and "--log" in command:
            return done(self.logs.get(int(command[3]), CACHE_ON_LOG))
        if text.startswith("gh run view"):
            run_id = int(command[3])
            index = run_id - 100
            sha = self.shas[index] if 0 <= index < len(self.shas) else "abc1234"
            return done(json.dumps(details(run_id, sha)))
        if text.startswith("gh api"):
            run_id = int(command[2].split("/")[-2])
            return done(json.dumps(self.jobs.get(run_id, job())))
        return done("[]")


# ---- Plain helpers -------------------------------------------------------------


def test_cache_plan_is_on_off_off_on_with_ten_of_each():
    plan = cache_plan()
    assert plan[:8] == ["on", "off", "off", "on", "on", "off", "off", "on"]
    assert plan.count("on") == plan.count("off") == 10


def test_seconds_between_reads_githubs_seven_digit_times():
    assert seconds_between("2026-11-09T13:57:50.0187071Z", "2026-11-09T13:57:51Z") == (
        pytest.approx(0.981293)
    )
    # The invisible mark some log lines start with is ignored.
    assert seconds_between("﻿2026-11-09T13:57:50Z", "2026-11-09T13:57:52Z") == 2


def test_log_lines_split_step_time_and_text_and_skip_odd_lines():
    lines = log_lines("test\tRuff\t2026-11-09T13:58:04.1Z All good\nnot a log line")
    assert lines == [("Ruff", "2026-11-09T13:58:04.1Z", "All good")]


def test_step_times_run_from_one_steps_first_line_to_the_next():
    seconds = log_step_seconds(log_lines(CACHE_ON_LOG), "2026-11-09T13:59:05Z")
    assert seconds["Set up job"] == 11.0
    assert seconds["Run astral-sh/setup-uv@x"] == 2.5
    assert seconds["Install dependencies"] == 0.25
    # The job's end is given in whole seconds, before the last line's time:
    # the last step ends at its last line instead (never negative).
    assert seconds["Complete job"] == 0.0


def test_the_last_step_ends_at_the_jobs_end_when_that_is_later():
    seconds = log_step_seconds(log_lines(CACHE_ON_LOG), "2026-11-09T13:59:07Z")
    assert seconds["Complete job"] == 1.5


def test_uv_lines_read_prepared_and_installed():
    assert uv_lines(log_lines(CACHE_OFF_LOG)) == {
        "prepared_packages": 35,
        "prepared_s": 1.25,
        "installed_packages": 43,
        "installed_s": 0.02,
    }
    assert uv_lines(log_lines(CACHE_ON_LOG)) == {
        "installed_packages": 43,
        "installed_s": 0.018,
    }


def test_cache_result_hit_miss_or_off():
    assert cache_result(log_lines(CACHE_ON_LOG)) == "hit"
    assert cache_result(log_lines(CACHE_OFF_LOG)) == "off"
    miss = log(
        ("Run astral-sh/setup-uv@x", "2026-11-09T13:58:01Z", "Trying to restore cache")
    )
    assert cache_result(log_lines(miss)) == "miss"


def test_secret_line_is_the_printed_line_not_the_command():
    line = secret_line(log_lines(CACHE_ON_LOG))
    assert line == "event: workflow_dispatch · from fork: false · secret present: yes"
    assert e4_ci.secret_present(line) == "yes"
    assert e4_ci.secret_present("") == ""
    assert secret_line(log_lines(CACHE_OFF_LOG)) == ""


# ---- The cache part -------------------------------------------------------------


def test_measure_run_makes_one_run_row_and_step_rows():
    gh = FakeGh()
    row, steps = measure_run(3, "on", 100, details(100), gh)
    assert set(row) <= set(RUN_COLUMNS)
    assert row["queue_s"] == 5.0 and row["job_s"] == 75.0
    assert row["cache_result"] == "hit"
    assert row["installed_s"] == 0.018
    assert "error" not in row
    assert [s["step"] for s in steps] == ["Set up job", "Install dependencies"]
    assert steps[0]["log_s"] == 11.0 and steps[0]["api_s"] == 1.0
    # GitHub had no time for this step; the log still has one.
    assert steps[1]["api_s"] == "" and steps[1]["log_s"] == 0.25
    assert all(set(s) <= set(STEP_COLUMNS) for s in steps)


def test_a_failed_run_is_recorded_with_its_error():
    gh = FakeGh()
    row, _ = measure_run(1, "off", 100, details(100, conclusion="failure"), gh)
    assert row["error"] == "run ended failure"


def test_a_run_without_job_times_or_log_is_recorded_not_hidden():
    gh = FakeGh(jobs={100: {"jobs": []}})
    row, steps = measure_run(1, "on", 100, details(100), gh)
    assert row["error"] == "no job times" and steps == []
    gh = FakeGh(logs={100: ""})
    row, steps = measure_run(1, "on", 100, details(100), gh)
    assert row["error"] == "no log" and steps == []


def test_cache_part_warms_up_then_runs_the_plan_one_at_a_time():
    gh = FakeGh(busy=2, logs={102: CACHE_OFF_LOG})
    runs, steps = [], []
    started = cache_part(
        ["on", "off"], True, runs.append, steps.append, gh, sleep=lambda s: None
    )
    # The warm-up (cache on) is started first and not recorded.
    assert gh.started == [
        (100, "uv_cache=true"),
        (101, "uv_cache=true"),
        (102, "uv_cache=false"),
    ]
    # Only the recorded runs are returned, not the warm-up.
    assert started == [101, 102]
    assert [(r["order"], r["cache"], r["run_id"]) for r in runs] == [
        (1, "on", 101),
        (2, "off", 102),
    ]
    assert runs[1]["cache_result"] == "off" and runs[1]["prepared_packages"] == 35
    # Each run waited until nothing else was running before it started.
    texts = [" ".join(c) for c in gh.commands]
    starts = [i for i, t in enumerate(texts) if t.startswith("gh workflow run")]
    for index in starts:
        assert texts[index - 1].startswith("gh run list --status")


def test_cache_part_stops_if_a_run_tests_another_commit():
    gh = FakeGh(shas=["abc1234", "abc1234", "def5678"])
    with pytest.raises(Stop, match="tested def5678, not abc1234"):
        cache_part(["on", "off"], True, [].append, [].append, gh, sleep=lambda s: None)


def test_a_run_that_never_finishes_stops_the_script():
    class Never(FakeGh):
        def __call__(self, command):
            if command[:3] == ["gh", "run", "view"] and "--log" not in command:
                self.commands.append(command)
                return done(json.dumps(details(100, status="in_progress")))
            return super().__call__(command)

    ticks = iter(range(0, 100_000, 1000))
    with pytest.raises(Stop, match="did not finish"):
        e4_ci.wait_for_run(
            100, Never(), sleep=lambda s: None, clock=lambda: next(ticks)
        )


def test_not_found_just_after_starting_is_retried():
    # GitHub may not know a new run for a few seconds (seen in the real runs).
    class Late(FakeGh):
        def __init__(self):
            super().__init__()
            self.misses = 2

        def __call__(self, command):
            if command[:3] == ["gh", "run", "view"] and self.misses:
                self.misses -= 1
                self.commands.append(command)
                return done(returncode=1, stderr="HTTP 404: Not Found")
            return super().__call__(command)

    gh = Late()
    result = e4_ci.wait_for_run(100, gh, sleep=lambda s: None, clock=lambda: 0.0)
    assert result["status"] == "completed"
    assert len(gh.commands) == 3


def test_not_found_for_too_long_stops_the_script():
    def missing(command):
        return done(returncode=1, stderr="HTTP 404: Not Found")

    ticks = iter(range(0, 10_000, 100))
    with pytest.raises(Stop, match="Not Found"):
        e4_ci.wait_for_run(
            100, missing, sleep=lambda s: None, clock=lambda: next(ticks)
        )


def test_other_errors_are_tried_again_then_stop():
    calls = []

    def broken(command):
        calls.append(command)
        return done(returncode=1, stderr="HTTP 500")

    with pytest.raises(Stop, match="HTTP 500"):
        e4_ci.wait_for_run(100, broken, sleep=lambda s: None, clock=lambda: 0.0)
    assert len(calls) == e4_ci.GH_TRIES


def test_a_brief_github_problem_is_tried_again():
    answers = iter([done(returncode=1, stderr="HTTP 502"), done("[1]")])
    assert e4_ci.gh_json(["run", "list"], lambda command: next(answers)) == [1]


def test_starting_a_run_is_never_tried_again():
    # Trying again could start two runs.
    calls = []

    def refuse(command):
        calls.append(command)
        return done(returncode=1, stderr="HTTP 502")

    with pytest.raises(Stop):
        e4_ci.start_run("off", refuse)
    assert len(calls) == 1


def test_waiting_for_quiet_first_lets_release_appear():
    waits = []
    e4_ci.wait_until_quiet(FakeGh(), sleep=waits.append)
    assert waits == [e4_ci.SETTLE_SECONDS]


def test_a_step_missing_from_the_log_makes_the_run_not_counted():
    record = job()
    record["jobs"][0]["steps"].append({"name": "Ruff", "started_at": None})
    record["jobs"][0]["steps"].append(
        {"name": "Skipped step", "started_at": None, "conclusion": "skipped"}
    )
    row, _ = measure_run(1, "on", 100, details(100), FakeGh(jobs={100: record}))
    # Ruff ran but has no log lines; the skipped step is fine.
    assert row["error"] == "steps missing from the log: Ruff"


def test_a_run_that_cannot_start_stops_the_script():
    def refuse(command):
        return done(returncode=1, stderr="HTTP 403")

    with pytest.raises(Stop, match="HTTP 403"):
        e4_ci.start_run("on", refuse)


def test_a_failing_gh_command_stops_with_its_message():
    def fail(command):
        return done(returncode=1, stderr="not logged in")

    with pytest.raises(Stop, match="not logged in"):
        e4_ci.gh_json(["run", "list"], fail)


# ---- The secrets part -----------------------------------------------------------


def test_a_skipped_job_means_the_secret_step_never_ran():
    gh = FakeGh(jobs={300: job(conclusion="skipped")})
    row = e4_ci.secret_row(details(300), 300, "release.yml", gh)
    assert row["secret_present"] == "step not run" and row["note"] == "job skipped"
    assert set(row) <= set(SECRET_COLUMNS)


def test_a_run_without_the_line_says_so():
    gh = FakeGh(logs={300: CACHE_OFF_LOG})
    row = e4_ci.secret_row(details(300), 300, "ci.yml", gh)
    assert row["secret_present"] == "no line found"


class SecretsGh(FakeGh):
    """A pretend `gh` with earlier runs and Release runs to list."""

    def __call__(self, command):
        text = " ".join(command)
        if text.startswith("gh run list -w ci.yml -b slice-9"):
            self.commands.append(command)
            return done(json.dumps([{"databaseId": 5, "createdAt": "x"}]))
        if text.startswith("gh run list -w ci.yml -b main"):
            self.commands.append(command)
            return done(
                json.dumps([{"databaseId": 7, "createdAt": "x", "headSha": "p7"}])
            )
        if text.startswith("gh run list -w release.yml -e workflow_run"):
            self.commands.append(command)
            releases = [
                # Before the manual runs (a push's release): left out.
                {
                    "databaseId": 400,
                    "createdAt": "2026-11-09T13:00:00Z",
                    "headSha": "abc1234",
                },
                # After them, same commit: checked.
                {
                    "databaseId": 401,
                    "createdAt": "2026-11-09T14:00:00Z",
                    "headSha": "abc1234",
                },
                # Another commit: left out.
                {
                    "databaseId": 402,
                    "createdAt": "2026-11-09T14:00:00Z",
                    "headSha": "zzz",
                },
            ]
            return done(json.dumps(releases))
        if text.startswith("gh run list -w release.yml"):
            self.commands.append(command)
            releases = [
                # Skipped (after a manual run): not a push's release.
                {"databaseId": 401, "headSha": "p7", "conclusion": "skipped"},
                # Published the pushed commit: taken.
                {"databaseId": 8, "headSha": "p7", "conclusion": "success"},
                # Published, but for a push not in the list: left out.
                {"databaseId": 9, "headSha": "old", "conclusion": "success"},
            ]
            return done(json.dumps(releases))
        return super().__call__(command)


def test_secrets_part_collects_earlier_manual_and_release_runs():
    gh = SecretsGh(jobs={401: job(conclusion="skipped")})
    rows = []
    secrets_part([100], rows.append, gh)
    found = {(r["run_id"], r["workflow"]) for r in rows}
    assert (
        (5, "ci.yml") in found
        and (7, "ci.yml") in found
        and (8, "release.yml") in found
    )
    assert (e4_ci.EARLIER_RUNS[1], "release.yml") in found
    assert (100, "ci.yml") in found and (401, "release.yml") in found
    assert not {400, 402, 9} & {r["run_id"] for r in rows}
    # A skipped Release run is not taken as a "recent push" release, and no
    # run is written twice.
    ids = [r["run_id"] for r in rows]
    assert len(ids) == len(set(ids))
    manual = next(r for r in rows if r["run_id"] == 100)
    assert manual["note"] == "manual run (E4 cache part)"
    assert manual["secret_present"] == "yes"
    release = next(r for r in rows if r["run_id"] == 401)
    assert release["note"] == "after a manual run; job skipped"


def test_releases_after_needs_manual_runs():
    assert e4_ci.releases_after([], FakeGh()) == []


# ---- Command line and output ----------------------------------------------------


def test_parse_arguments(tmp_path):
    options = e4_ci.parse_arguments([])
    assert options.part == ["cache", "secrets"] and options.trial is None
    options = e4_ci.parse_arguments(["--part", "secrets", "--trial", str(tmp_path)])
    assert options.part == ["secrets"] and options.trial == tmp_path


def test_secrets_alone_reads_the_recorded_runs(tmp_path, monkeypatch):
    (tmp_path / "e4_runs.csv").write_text("order,run_id\n1,100\n2,101\n")
    seen = {}

    def fake_secrets(manual_runs, write, runner=None):
        seen["runs"] = manual_runs

    monkeypatch.setattr(e4_ci, "secrets_part", fake_secrets)
    e4_ci.main(["--part", "secrets", "--trial", str(tmp_path)])
    assert seen["runs"] == [100, 101]
