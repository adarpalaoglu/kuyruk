"""Tests for the E3 charts and key numbers in `bench/charts.py`.

The charts need matplotlib, which is only in the separate `bench` group, so
these tests are skipped in the normal test run and in CI. Run them with:

    uv run --group bench pytest tests/test_e3_charts.py --no-cov

They draw from tiny made-up CSV files (including a failed build and a
missing GitHub run) and check the pictures appear and the numbers add up.
"""

import csv

import pytest

charts = pytest.importorskip("bench.charts")
from bench.e3_build import BUILD_COLUMNS, IMAGE_COLUMNS  # noqa: E402


def write(path, columns, rows):
    """Write a small CSV file."""
    with path.open("w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=columns)
        writer.writeheader()
        writer.writerows([{c: row.get(c, "") for c in columns} for row in rows])


# Made-up sizes in MB: the base image alone explains 600 of the 800 MB gap
# between pip-good and uv-good.
SIZES = {
    "pip-bad": 1000,
    "pip-good": 1000,
    "pip-slim": 400,
    "uv-bad": 200,
    "uv-good": 200,
    "base-full": 900,
    "base-slim": 120,
}


def images():
    """Made-up rows of e3_images.csv."""
    rows = []
    for image, mb in SIZES.items():
        row = {"environment": "mac", "image": image, "image_bytes": mb * 1_000_000}
        row["compressed_bytes"] = mb * 250_000
        if not image.startswith("base"):
            row |= {"starts": "yes", "has_curl": "no", "lines": "12"}
        rows.append(row)
    return rows


def builds():
    """Made-up rows of e3_builds.csv: 2 rounds, one failed package build."""
    rows = []
    for round_number in (1, 2):
        for image in charts.E3_MAIN:
            for step, seconds in [("cold", 40), ("code", 3), ("package", 20)]:
                row = {"round": round_number, "dockerfile": image, "step": step}
                row |= {"seconds": seconds + round_number, "ok": "yes"}
                rows.append(row)
    rows[-1] |= {"ok": "no", "seconds": "", "error": "boom"}
    return rows


@pytest.fixture
def e3_data(tmp_path, monkeypatch):
    """Point the charts at a folder of made-up data and a folder for pictures."""
    monkeypatch.setattr(charts, "DATA", tmp_path)
    monkeypatch.setattr(charts, "CHARTS", tmp_path / "charts")
    write(tmp_path / "e3_images.csv", IMAGE_COLUMNS, images())
    write(tmp_path / "e3_builds.csv", BUILD_COLUMNS, builds())
    return tmp_path


def test_charts_and_numbers_from_the_csvs(e3_data, capsys):
    """Both pictures are drawn; the size split and the medians are printed."""
    charts.e3_charts()
    assert (e3_data / "charts" / "e3_image_size.png").stat().st_size > 0
    assert (e3_data / "charts" / "e3_build_time.png").stat().st_size > 0
    printed = capsys.readouterr().out
    assert "E3 Mac image pip-bad: 1,000 MB, 250 MB compressed" in printed
    assert "pip-bad is 5.0x the size of uv-good" in printed
    assert "of the 800 MB gap pip-good -> uv-good, 600 MB (75%)" in printed
    assert "200 MB (25%) is uv + two stages" in printed
    assert "E3 Mac pip-bad: cold 41.5 (41.0-42.0) s" in printed
    # The failed build is left out of the times and counted.
    assert "E3 Mac uv-good: cold 41.5 (41.0-42.0) s; code 4.5 (4.0-5.0) s; " in printed
    assert "package 21.0 (21.0-21.0) s; failed 1" in printed


def test_both_environments_get_their_own_panels(e3_data, capsys):
    """With the GitHub files too, both are drawn and printed."""
    write(e3_data / "e3_images_ci.csv", IMAGE_COLUMNS, images())
    write(e3_data / "e3_builds_ci.csv", BUILD_COLUMNS, builds())
    charts.e3_charts()
    printed = capsys.readouterr().out
    assert "E3 GitHub Actions image uv-good: 200 MB" in printed


def test_a_missing_image_is_shown_as_not_measured(e3_data, capsys):
    """An image whose build failed has no size; no split is printed then."""
    rows = images()
    rows[2]["image_bytes"] = ""  # pip-slim
    write(e3_data / "e3_images.csv", IMAGE_COLUMNS, rows)
    charts.e3_charts()
    printed = capsys.readouterr().out
    assert "E3 Mac image pip-slim: not measured" in printed
    assert "gap" not in printed


def test_no_e3_data_draws_nothing(tmp_path, monkeypatch):
    """Before E3 is measured, nothing happens."""
    monkeypatch.setattr(charts, "DATA", tmp_path)
    monkeypatch.setattr(charts, "CHARTS", tmp_path / "charts")
    charts.e3_charts()
    assert not (tmp_path / "charts").exists()
