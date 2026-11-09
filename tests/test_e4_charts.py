"""Tests for the E4 chart and key numbers in `bench/charts.py`.

The chart needs matplotlib, which is only in the separate `bench` group, so
these tests are skipped in the normal test run and in CI. Run them with:

    uv run --group bench pytest tests/test_e4_charts.py --no-cov

They draw from tiny made-up CSV files (including a failed run) and check
the picture appears and the numbers add up.
"""

import csv

import pytest

charts = pytest.importorskip("bench.charts")
from bench.e4_ci import RUN_COLUMNS, SECRET_COLUMNS, STEP_COLUMNS  # noqa: E402


def write(path, columns, rows):
    """Write a small CSV file."""
    with path.open("w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=columns)
        writer.writeheader()
        writer.writerows([{c: row.get(c, "") for c in columns} for row in rows])


def run(run_id, cache, job_s, **values):
    """One made-up row of e4_runs.csv."""
    row = {"run_id": run_id, "cache": cache, "job_s": job_s, "queue_s": 4}
    row["cache_result"] = "hit" if cache == "on" else "off"
    return row | values


def steps(run_id, cache, setup, install, tests):
    """Made-up step rows for one run: two test steps and three others."""
    rows = [
        ("Set up job", 1.0),
        ("Run astral-sh/setup-uv@x", setup),
        ("Install dependencies", install),
        ("Fast tests (coverage gate 85%)", tests - 15),
        ("Postgres tests", 15.0),
    ]
    return [
        {"run_id": run_id, "cache": cache, "step": step, "log_s": seconds}
        for step, seconds in rows
    ]


@pytest.fixture
def e4_data(tmp_path, monkeypatch):
    """Point the charts at a folder of made-up data and a folder for pictures."""
    monkeypatch.setattr(charts, "DATA", tmp_path)
    monkeypatch.setattr(charts, "CHARTS", tmp_path / "charts")
    runs = [
        run(1, "on", 90, installed_s=0.02),
        run(2, "off", 80, prepared_s=0.4),
        run(3, "on", 100, installed_s=0.02),
        run(4, "off", 84, prepared_s=0.5),
        run(5, "off", "", error="run ended failure"),
    ]
    write(tmp_path / "e4_runs.csv", RUN_COLUMNS, runs)
    rows = steps(1, "on", 3.0, 0.1, 75) + steps(3, "on", 3.2, 0.1, 85)
    rows += steps(2, "off", 1.0, 0.5, 70) + steps(4, "off", 1.0, 0.6, 72)
    write(tmp_path / "e4_steps.csv", STEP_COLUMNS, rows)
    secrets = [
        {"workflow": "ci.yml", "event": "push", "secret_present": "yes"},
        {"workflow": "ci.yml", "event": "push", "secret_present": "yes"},
        {
            "workflow": "release.yml",
            "event": "workflow_run",
            "secret_present": "step not run",
        },
    ]
    write(tmp_path / "e4_secrets.csv", SECRET_COLUMNS, secrets)
    return tmp_path


def test_chart_and_numbers_from_the_csvs(e4_data, capsys):
    """The picture is drawn; failed runs are left out and counted."""
    charts.e4_charts()
    assert (e4_data / "charts" / "e4_cache.png").stat().st_size > 0
    printed = capsys.readouterr().out
    assert "E4 cache on: 2 runs (0 not counted); job 95.0 (90.0-100.0) s" in printed
    assert "E4 cache off: 2 runs (1 not counted); job 82.0 (80.0-84.0) s" in printed
    assert "E4 cache on: uv setup + cache 3.10 (3.00-3.20) s" in printed
    assert "E4 cache off: Tests (ruff, fast, Postgres) 71.00 (70.00-72.00) s" in printed
    assert "E4 cache off: uv prepared_s 0.45 (0.40-0.50) s" in printed
    assert "E4 cache on: cache result {'hit': 2}" in printed
    assert "E4 secret: ci.yml push: yes (2 runs)" in printed
    assert "E4 secret: release.yml workflow_run: step not run (1 runs)" in printed


def test_steps_are_grouped_into_parts():
    assert charts.e4_part_of("Post Run astral-sh/setup-uv@x") == "uv setup + cache"
    assert charts.e4_part_of("Initialize containers") == "Postgres start-up"
    assert charts.e4_part_of("Stop containers") == charts.E4_REST


def test_without_steps_or_secrets_still_draws(e4_data):
    (e4_data / "e4_steps.csv").unlink()
    (e4_data / "e4_secrets.csv").unlink()
    charts.e4_charts()
    assert (e4_data / "charts" / "e4_cache.png").exists()


def test_no_e4_data_draws_nothing(tmp_path, monkeypatch):
    """Before E4 is measured, nothing happens."""
    monkeypatch.setattr(charts, "DATA", tmp_path)
    monkeypatch.setattr(charts, "CHARTS", tmp_path / "charts")
    charts.e4_charts()
    assert not (tmp_path / "charts").exists()
