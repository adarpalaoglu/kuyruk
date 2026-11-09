"""Tests for E2's kill script, `bench/e2_kill.py` (D100-D104).

Only the parts that need no Docker, database or AI: the run plans
(right counts, interleaved), counting redone texts from the log lines,
recovery times from the watch's looks, when the watch stops, the cost
counters, the lines-of-code counter and the CSV files. The kills themselves
are checked by the trial run (`--trial`), never in the test suite.
"""

import ast
import csv
import time
from collections import Counter

import pytest

from bench import e2_kill
from bench.e2_kill import Kill, Look, Planned


def look(t, finished=0, crashes=0, state="running", late_ms=0.0):
    """A look at the job at `t` seconds."""
    return Look(t, late_ms, state, finished, crashes)


# --- Plans --------------------------------------------------------------------


def test_main_plan_has_broken_and_fixed_in_every_repeat():
    """5 repeats, each with one broken and one fixed run, in its own order."""
    plan = e2_kill.main_plan()
    assert len(plan) == 10
    for repeat in range(1, 6):
        kinds = [p.kind for p in plan if p.repeat == repeat]
        assert sorted(kinds) == ["broken", "fixed"]
    # Interleaved: not always the same one first.
    firsts = {p.kind for p in plan if p.order == 1}
    assert firsts == {"broken", "fixed"}
    assert all(p.wait == "replay" and p.stale_seconds == 30 for p in plan)
    assert all(p.texts == 40 and p.kill_at == 20 for p in plan)
    assert all(p.watch_after_restart == 300 for p in plan)


def test_stale_plan_is_fixed_only_at_three_stale_times():
    """Fixed only, 10 / 30 / 60 s, each once per repeat, in shuffled orders."""
    plan = e2_kill.stale_plan()
    assert len(plan) == 15
    assert {p.kind for p in plan} == {"fixed"}
    assert Counter(p.stale_seconds for p in plan) == {10: 5, 30: 5, 60: 5}
    orders = {
        tuple(p.stale_seconds for p in plan if p.repeat == r) for r in range(1, 6)
    }
    assert len(orders) > 1


def test_sensitivity_plan_covers_every_wait_and_kind():
    """4 waits x broken and fixed x 5 repeats; broken watched 60 s here."""
    plan = e2_kill.sensitivity_plan()
    assert len(plan) == 40
    assert Counter((p.wait, p.kind) for p in plan) == {
        (w, k): 5 for w in ["0.5", "1", "2", "4"] for k in ["broken", "fixed"]
    }
    assert {p.watch_after_restart for p in plan} == {60}
    # The two runs of one wait are next to each other (one container change).
    for repeat in range(1, 6):
        waits = [p.wait for p in plan if p.repeat == repeat]
        assert all(waits[i] == waits[i + 1] for i in range(0, 8, 2))
    orders = {tuple(p.wait for p in plan if p.repeat == r) for r in range(1, 6)}
    assert len(orders) > 1


def test_ai_plan_kills_at_6_of_12():
    """The real-AI check: fixed, Ollama, 12 texts, killed at 6 (D103)."""
    plan = e2_kill.ai_plan()
    assert len(plan) == 5
    assert all(
        (p.kind, p.ai, p.texts, p.kill_at) == ("fixed", "ollama", 12, 6) for p in plan
    )


def test_trial_plan_is_short():
    """One broken run watched 30 s, one fixed run."""
    plan = e2_kill.trial_plan()
    assert [p.kind for p in plan] == ["broken", "fixed"]
    assert plan[0].watch_after_restart == 30


def test_plans_for_keeps_the_part_order_and_skips_cost():
    """Cost has no kill runs; the rest come in the order asked."""
    plan = e2_kill.plans_for(["stale", "cost", "main"])
    assert [p.part for p in plan] == ["stale"] * 15 + ["main"] * 10


def test_parse_arguments():
    """Parts can be named more than once; the trial takes a folder."""
    arguments = e2_kill.parse_arguments(["--part", "main", "--part", "ai"])
    assert arguments.part == ["main", "ai"]
    assert e2_kill.parse_arguments([]).part is None
    assert str(e2_kill.parse_arguments(["--trial", "x"]).trial) == "x"
    with pytest.raises(SystemExit):
        e2_kill.parse_arguments(["--part", "nonsense"])


# --- Counting -----------------------------------------------------------------

LOG = """\
2026-11-09 INFO text started job=abc position=1 worker=w1
2026-11-09 INFO text started job=abc position=2 worker=w1
2026-11-09 INFO text started job=other position=2 worker=w1
2026-11-09 INFO text started job=abc position=2 worker=w2
2026-11-09 INFO text started job=abc position=12 worker=w2
2026-11-09 INFO text started job=abc position=12 worker=w3
2026-11-09 INFO text started job=abc position=12 worker=w4
"""


def test_started_positions_counts_only_this_job():
    """Each position's starts, for this job only (D75)."""
    starts = e2_kill.started_positions(LOG, "abc")
    assert starts == {1: 1, 2: 2, 12: 3}


def test_count_redone_counts_every_extra_start():
    """A text started twice is redone once; three times, twice."""
    assert e2_kill.count_redone(Counter({1: 1, 2: 2, 12: 3})) == 3
    assert e2_kill.count_redone(Counter()) == 0


def test_recovery_times_after_the_kill():
    """Takeover, first text after the kill and the end, from the kill."""
    looks = [
        look(9.0, finished=20),
        look(10.0, finished=20),
        look(40.0, finished=20, crashes=1),
        look(47.5, finished=21, crashes=1),
        look(80.0, finished=40, crashes=1, state="done"),
    ]
    kill = Kill(t_kill=10.0, finished_at_kill=20)
    assert e2_kill.recovery(looks, kill) == {
        "takeover_s": 30.0,
        "recovery_s": 37.5,
        "finish_s": 70.0,
    }


def test_recovery_is_blank_when_it_never_happens():
    """A broken run: nothing after the kill, so every time is blank."""
    looks = [look(10.0, finished=20), look(400.0, finished=20)]
    kill = Kill(t_kill=10.0, finished_at_kill=20)
    assert e2_kill.recovery(looks, kill) == {
        "takeover_s": "",
        "recovery_s": "",
        "finish_s": "",
    }
    assert e2_kill.recovery(looks, Kill())["recovery_s"] == ""


def test_late_looks():
    """Looks more than 20 ms late are counted; the worst is reported."""
    looks = [look(0, late_ms=1), look(1, late_ms=25), look(2, late_ms=21.5)]
    assert e2_kill.late_looks(looks) == (2, 25)
    assert e2_kill.late_looks([]) == (0, 0.0)


# --- When the watch stops -----------------------------------------------------


def planned(kind="fixed", watch=300.0):
    """A run of `kind`, with the usual settings."""
    return e2_kill.plan_run("main", kind, 1, 1, watch_after_restart=watch)


def test_watch_waits_for_the_kill_point():
    """Before the kill: carry on, unless the job ended or never got there."""
    assert e2_kill.next_step(planned(), Kill(), False, "running", 5, 1.0) == (
        False,
        "",
    )
    stop, error = e2_kill.next_step(planned(), Kill(), False, "done", 40, 1.0)
    assert stop and "before the kill" in error
    stop, error = e2_kill.next_step(planned(), Kill(), False, "running", 0, 601)
    assert stop and "never reached" in error


def test_watch_stops_on_a_failed_kill():
    """A kill or restart that failed ends the run with its error."""
    kill = Kill(error="CalledProcessError: boom")
    assert e2_kill.next_step(planned(), kill, True, "running", 20, 30) == (
        True,
        "CalledProcessError: boom",
    )


def test_watch_waits_while_the_kill_is_on_its_way():
    """Kill sent but not done yet: carry on."""
    assert e2_kill.next_step(planned(), Kill(), True, "running", 20, 30) == (
        False,
        "",
    )


def test_fixed_run_stops_when_the_job_is_over():
    """A fixed run ends when the job does, or 5 minutes after the kill."""
    kill = Kill(t_kill=time.perf_counter())
    assert e2_kill.next_step(planned(), kill, True, "running", 25, 0) == (False, "")
    assert e2_kill.next_step(planned(), kill, True, "done", 40, 0) == (True, "")
    old = Kill(t_kill=time.perf_counter() - 301)
    assert e2_kill.next_step(planned(), old, True, "running", 25, 0) == (True, "")


def test_broken_run_is_watched_long_enough_after_the_restart():
    """A broken run ends `watch_after_restart` after the API answers again."""
    now = time.perf_counter()
    kill = Kill(t_kill=now - 50, t_running=now - 45, t_healthy=now - 40)
    kill.done.set()
    assert e2_kill.next_step(planned("broken", 60), kill, True, "running", 20, 0) == (
        False,
        "",
    )
    assert e2_kill.next_step(planned("broken", 30), kill, True, "running", 20, 0) == (
        True,
        "",
    )
    # Restart not finished yet: carry on.
    waiting = Kill(t_kill=now)
    assert e2_kill.next_step(planned("broken"), waiting, True, "running", 20, 0)[0] is (
        False
    )


# --- Docker settings ----------------------------------------------------------


def test_stack_environment_sets_what_the_compose_file_asks_for():
    """Every `${E2_...:?}` value is set; a fixed wait switches the fake AI mode."""
    stack = e2_kill.Stack()
    replay = stack.environment(planned())
    assert replay["E2_FAKE_AI_WAIT_MODE"] == "replay"
    assert replay["E2_STALE_AFTER_SECONDS"] == "30"
    assert replay["E2_HEARTBEAT_SECONDS"] == "5"
    assert replay["E2_AI_BACKEND"] == "fake"
    fixed = stack.environment(e2_kill.plan_run("s", "fixed", 1, 1, wait="0.5"))
    assert (fixed["E2_FAKE_AI_WAIT_MODE"], fixed["E2_FAKE_AI_WAIT_SECONDS"]) == (
        "fixed",
        "0.5",
    )
    compose = (e2_kill.ROOT / "bench" / "e2_compose.yaml").read_text()
    asked = set(__import__("re").findall(r"\$\{(E2_[A-Z_]+):\?\}", compose))
    assert asked <= set(replay)


def test_stack_secrets_are_new_each_session():
    """Each session makes its own throwaway secrets."""
    one, two = e2_kill.Stack(), e2_kill.Stack()
    assert one.jwt_secret != two.jwt_secret
    assert len(one.jwt_secret) >= 32


def test_settings_that_need_new_containers():
    """Changing the AI, the wait or the stale time means new containers."""
    a = e2_kill.plan_run("main", "fixed", 1, 1)
    b = e2_kill.plan_run("main", "broken", 2, 1)
    c = e2_kill.plan_run("stale", "fixed", 1, 1, stale_seconds=10.0)
    assert e2_kill.Stack.settings_of(a) == e2_kill.Stack.settings_of(b)
    assert e2_kill.Stack.settings_of(a) != e2_kill.Stack.settings_of(c)


# --- Cost ---------------------------------------------------------------------


def test_cost_row_takes_the_heartbeat_out_of_the_jobs_updates():
    """jobs updates = claim + one per text + done + heartbeats."""
    before = {"jobs_ins": 5, "jobs_upd": 100, "job_items_ins": 0, "job_items_upd": 0}
    after = {
        "jobs_ins": 6,
        "jobs_upd": 100 + 1 + 40 + 1 + 14,
        "job_items_ins": 40,
        "job_items_upd": 40,
        "users_ins": 1,
    }
    row = e2_kill.cost_row(1, 40, 73.4, before, after)
    assert row["heartbeat_writes"] == 14
    assert row["heartbeats_expected"] == 14
    assert row["all_writes"] == 1 + 56 + 40 + 40 + 1
    assert row["heartbeat_share"] == round(14 / 138, 3)
    assert row["jobs_inserts"] == 1 and row["items_updates"] == 40


def test_cost_row_with_no_writes():
    """No writes at all: the share is blank, not a division by zero."""
    row = e2_kill.cost_row(1, 0, 0.0, {}, {"jobs_upd": 2})
    assert row["heartbeat_share"] == 0.0 or row["all_writes"] == 2
    empty = e2_kill.cost_row(1, 0, 0.0, {}, {})
    assert empty["heartbeat_share"] == ""


# --- Lines of code ------------------------------------------------------------

SOURCE = '''"""A module docstring
over two lines."""

# A comment line.
import os


def one():
    """A docstring."""
    # Another comment.
    x = 1  # a comment after code
    return x


@staticmethod
def two(
    a,
):
    return """not a docstring,
    a string that is code"""
'''


def test_code_lines_skip_comments_blanks_and_docstrings():
    """Only lines with code count; a multi-line string that is code counts."""
    assert e2_kill.count_code(SOURCE) == 10


def test_count_code_of_named_functions():
    """Only the lines inside the named functions (with their decorators)."""
    assert e2_kill.count_code(SOURCE, ["one"]) == 3
    assert e2_kill.count_code(SOURCE, ["two"]) == 6
    assert e2_kill.count_code(SOURCE, ["missing"]) == 0


DEMO_SOURCE = """
class Worker:
    def beat(self):
        update = 1
        save = 2
        if not self.demo_mode:
            return
        mark = 3
        if mark:
            crash()

    def other(self):
        return 4
"""


def test_count_code_leaves_out_demo_only_lines():
    """From the first `demo_mode` line to the function's end is left out."""
    assert e2_kill.count_code(DEMO_SOURCE, ["beat"]) == 3  # def, update, save
    assert e2_kill.count_code(DEMO_SOURCE, ["other"]) == 2  # nothing demo-only
    assert e2_kill.count_code(DEMO_SOURCE) == 11  # the whole file: all of it


def test_code_rows_from_the_repo():
    """The worker and its recovery parts, without the demo crash check."""
    rows = e2_kill.code_rows()
    assert len(rows) == 2
    worker, recovery_part = (row["lines"] for row in rows)
    assert 0 < recovery_part < worker
    assert "demo-only lines left out" in rows[1]["what"]
    source = (e2_kill.ROOT / "app" / "worker.py").read_text(encoding="utf-8")
    with_demo = len(
        e2_kill.code_lines(source)
        & {
            line
            for name in e2_kill.RECOVERY_PARTS
            for line in function_lines(source, name)
        }
    )
    assert recovery_part < with_demo  # the demo crash check was left out


def function_lines(source, name):
    """Every line of the function called `name` in `source`."""
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and (
            node.name == name
        ):
            return range(node.lineno, node.end_lineno + 1)
    return range(0)


def test_a_failed_run_keeps_its_settings(tmp_path, monkeypatch, capsys):
    """A run that raises is saved as an error in its own group, not a blank one."""

    def fail(stack, planned, texts):
        raise RuntimeError("docker compose ps failed")

    monkeypatch.setattr(e2_kill, "run_once", fail)
    monkeypatch.setattr(e2_kill, "REST_SECONDS", 0)
    planned = next(p for p in e2_kill.sensitivity_plan() if p.wait == "0.5")
    e2_kill.start_runs_file(tmp_path, ["sensitivity"])
    e2_kill.run_plan(None, tmp_path, [planned], ["a text"] * 40)
    with (tmp_path / e2_kill.FILES["runs"]).open() as file:
        (row,) = csv.DictReader(file)
    assert row["error"] == "RuntimeError: docker compose ps failed"
    assert (row["part"], row["kind"]) == ("sensitivity", planned.kind)
    assert row["ai"] == "fake"
    assert (row["wait"], row["stale_seconds"], row["texts"]) == ("0.5", "30", "40")


# --- Files --------------------------------------------------------------------


def test_start_runs_file_keeps_other_parts(tmp_path):
    """Redoing one part removes only its own rows."""
    path = tmp_path / "e2_runs.csv"
    e2_kill.write_rows(
        path,
        e2_kill.RUN_COLUMNS,
        [{"part": "main", "kind": "fixed"}, {"part": "stale", "kind": "fixed"}],
    )
    e2_kill.start_runs_file(tmp_path, ["main"])
    e2_kill.add_row(path, e2_kill.RUN_COLUMNS, {"part": "main", "kind": "broken"})
    with path.open() as file:
        rows = list(csv.DictReader(file))
    assert [(r["part"], r["kind"]) for r in rows] == [
        ("stale", "fixed"),
        ("main", "broken"),
    ]


def test_start_runs_file_makes_a_new_file(tmp_path):
    """With no file yet, it starts one with only the header."""
    e2_kill.start_runs_file(tmp_path / "new", ["trial"])
    assert (tmp_path / "new" / "e2_runs.csv").read_text().startswith("part,kind")


def test_report_warns_about_errors_and_late_looks(capsys):
    """One line per run; errors and late looks are shouted about."""
    row = {
        "kind": "broken",
        "lost": 20,
        "redone": 0,
        "recovery_s": "",
        "final_state": "running",
        "crash_count": 0,
        "health_after": True,
        "error": "boom",
        "late_looks": 2,
        "max_late_ms": 31.0,
    }
    e2_kill.report(row)
    printed = capsys.readouterr().out
    assert "recovery never" in printed
    assert "error: boom" in printed
    assert "2 looks late" in printed


def test_planned_is_frozen():
    """A plan can't be changed by accident once made."""
    run = e2_kill.plan_run("main", "fixed", 1, 1)
    assert isinstance(run, Planned)
    with pytest.raises(AttributeError):
        run.kind = "broken"  # type: ignore[misc]
