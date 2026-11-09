"""Tests for the Turkish vs English cost chart in `bench/charts.py` (Slice 15).

The chart needs matplotlib, which is only in the separate `bench` group, so
these tests are skipped in the normal test run and in CI. Run them with:

    uv run --group bench pytest tests/test_cost_charts.py --no-cov

They draw from a tiny made-up CSV file and check the picture appears and
the printed numbers use the sum ratio (D112).
"""

import csv

import pytest

charts = pytest.importorskip("bench.charts")
from bench.cost_tr_en import COLUMNS  # noqa: E402


def row(review_id, group, o200k, gemma):
    """One made-up review: (Turkish, English) tokens for each tokenizer."""
    return {
        "id": review_id,
        "group": group,
        "characters_tr": 10,
        "characters_en": 10,
        "words_tr": 2,
        "words_en": 3,
        "o200k_tr": o200k[0],
        "o200k_en": o200k[1],
        "gemma_tr": gemma[0],
        "gemma_en": gemma[1],
    }


def test_cost_chart_is_drawn_and_numbers_printed(tmp_path, monkeypatch, capsys):
    """Picture written; "all" line shows the sum ratio, not the mean."""
    with (tmp_path / "cost_tokens.csv").open("w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(
            [
                row(1, "short", (6, 4), (5, 4)),
                row(2, "medium", (30, 20), (22, 20)),
                row(3, "long", (10, 10), (9, 10)),
            ]
        )
    monkeypatch.setattr(charts, "DATA", tmp_path)
    monkeypatch.setattr(charts, "CHARTS", tmp_path / "charts")
    charts.cost_charts()
    assert (tmp_path / "charts" / "cost_tr_en.png").stat().st_size > 0
    out = capsys.readouterr().out
    # 46 / 34 = 1.35, while the mean of 1.5, 1.5 and 1.0 would be 1.33.
    assert "o200k all (3 reviews): TR 46 / EN 34 tokens; sum ratio 1.35" in out


def test_no_cost_data_draws_nothing(tmp_path, monkeypatch):
    """Before the cost count exists, the charts script just skips it."""
    monkeypatch.setattr(charts, "DATA", tmp_path)
    monkeypatch.setattr(charts, "CHARTS", tmp_path / "charts")
    charts.cost_charts()
    assert not (tmp_path / "charts").exists()
