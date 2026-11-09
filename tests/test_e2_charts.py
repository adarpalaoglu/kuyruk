"""Tests for the E2 charts and key numbers in `bench/charts.py`.

The charts need matplotlib, which is only in the separate `bench` group, so
these tests are skipped in the normal test run and in CI. Run them with:

    uv run --group bench pytest tests/test_e2_charts.py --no-cov

They draw from tiny made-up CSV files, including partial data (a run with an
error, a part that hasn't been measured), and check the pictures appear.
"""

import csv

import pytest

charts = pytest.importorskip("bench.charts")
from bench.e2_kill import CODE_COLUMNS, COST_COLUMNS, RUN_COLUMNS  # noqa: E402


def write(path, columns, rows):
    """Write a small CSV file."""
    with path.open("w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=columns)
        writer.writeheader()
        writer.writerows([{c: row.get(c, "") for c in columns} for row in rows])


def run(part, kind, **values):
    """One made-up row of e2_runs.csv."""
    base = {
        "part": part,
        "kind": kind,
        "wait": "replay",
        "stale_seconds": "30",
        "lost": "0",
        "redone": "4",
        "recovery_s": "33.2",
        "takeover_s": "27.0",
        "finish_s": "70.0",
        "restart_s": "0.5",
        "final_state": "done",
        "late_looks": "0",
    }
    return {**base, **values}


@pytest.fixture
def e2_data(tmp_path, monkeypatch):
    """Point the charts at a folder of made-up data and a folder for pictures."""
    monkeypatch.setattr(charts, "DATA", tmp_path)
    monkeypatch.setattr(charts, "CHARTS", tmp_path / "charts")
    write(tmp_path / "ollama_times_at_4.csv", ["id", "seconds"], [{"seconds": 7}])
    broken = {"lost": "20", "redone": "0", "recovery_s": "", "takeover_s": ""}
    broken |= {"finish_s": "", "final_state": "running"}
    rows = [
        run("main", "broken", **broken),
        run("main", "broken", **broken),
        run("main", "fixed"),
        run("main", "fixed", redone="3", recovery_s="35.0"),
        run("main", "fixed", error="boom", recovery_s=""),
        run("sensitivity", "fixed", wait="0.5", recovery_s="28.0"),
        run("sensitivity", "broken", wait="0.5", **broken),
        run("stale", "fixed", stale_seconds="10", recovery_s="12.0"),
        run("ai", "fixed", ai="ollama", recovery_s="31.0"),
    ]
    write(tmp_path / "e2_runs.csv", RUN_COLUMNS, rows)
    return tmp_path


def test_charts_and_numbers_from_the_csvs(e2_data, capsys):
    """Both pictures are drawn and the key numbers printed, errors left out."""
    write(
        e2_data / "e2_cost.csv",
        COST_COLUMNS,
        [{"job_s": "73", "all_writes": "140", "heartbeat_share": "0.1"}],
    )
    write(e2_data / "e2_code.csv", CODE_COLUMNS, [{"what": "worker", "lines": "300"}])
    charts.e2_charts()
    assert (e2_data / "charts" / "e2_lost_redone.png").stat().st_size > 0
    assert (e2_data / "charts" / "e2_recovery.png").stat().st_size > 0
    printed = capsys.readouterr().out
    assert "E2 main fixed wait replay stale 30: 2 runs (1 errors)" in printed
    assert "recovery_s never/n.a." in printed  # the broken runs
    assert "heartbeat_share: 10.0% (median)" in printed
    assert "E2 code worker: 300 lines" in printed


def test_charts_with_only_some_parts(e2_data):
    """Only a trial measured (no cost, no stale, no AI): still draws."""
    write(
        e2_data / "e2_runs.csv",
        RUN_COLUMNS,
        [run("main", "fixed"), run("main", "broken", recovery_s="")],
    )
    charts.e2_charts()
    assert (e2_data / "charts" / "e2_recovery.png").exists()


def test_no_e2_data_draws_nothing(tmp_path, monkeypatch):
    """Before E2 is measured, nothing happens."""
    monkeypatch.setattr(charts, "DATA", tmp_path)
    monkeypatch.setattr(charts, "CHARTS", tmp_path / "charts")
    charts.e2_charts()
    assert not (tmp_path / "charts").exists()
