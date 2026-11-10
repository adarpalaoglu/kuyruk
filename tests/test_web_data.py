"""Tests for the interface's story numbers (`bench/web_data.py`, D120) and
for the number check's interface part (`check_interface` in
`bench/check_numbers.py`).

The story numbers must come out of the CSV files exactly as the approved
mockup shows them (checked by hand against docs/experiments.md on 10 Nov
2026), and the check must catch a stale findings file, a number typed into a
page, and a worded claim the data no longer supports.
"""

import json
import shutil
from pathlib import Path

import pytest

from bench import web_data
from bench.check_numbers import WEB, check, check_interface, typed_numbers

DATA = web_data.DATA


def test_story_numbers_are_the_ones_on_the_approved_mockup():
    found = web_data.findings()
    expected = {
        "e1_bad_s": 252,
        "e1_bad_min": 4,
        "e1_good_ms": 7,
        "e1_good_s": 0.007,
        "e1_times_faster": 36000,
        "e1_slow_requests": 50,
        "e2_broken_runs": 25,
        "e2_fixed_runs": 30,
        "e2_ai_runs": 5,
        "e2_fixed_lost": 0,
        "e2_recovery_min_s": 26,
        "e2_recovery_max_s": 34,
        "e2_recovery_about_s": 30,
        "e2_stale_s": 30,
        "e3_bad_mb": 1331,
        "e3_bad_gb": 1.3,
        "e3_good_mb": 259,
        "e3_times_smaller": 5,
        "e3_rebuild_bad_s": 15.6,
        "e3_rebuild_good_s": 0.5,
        "e3_times_faster": 30,
        "e3_base_percent": 98,
        "e3_rest_percent": 2,
        "e3_builds": 120,
        "e4_cache_on_s": 90,
        "e4_cache_off_s": 90,
        "e4_saved_s": 0,
        "e4_runs": 20,
        "e4_release_skipped": 21,
        "cost_percent_more": 24,
        "cost_reviews": 50,
        "cost_tr_per_word": 2.1,
        "cost_en_per_word": 1.2,
    }
    assert {key: found[key] for key in expected} == expected


@pytest.mark.parametrize(
    ("x", "digits", "rounded"),
    [(2.5, 0, 3), (0.0065, 3, 0.007), (31.8, -1, 30), (25, -1, 30)],
)
def test_rounding_is_half_up_like_a_person(x, digits, rounded):
    assert web_data.round_half_up(x, digits) == pytest.approx(rounded)


@pytest.mark.parametrize(
    ("bad", "good", "shown"), [(251.9, 0.00694, 36000), (99.9, 1, 99), (5, 1, 5)]
)
def test_times_faster_rounds_down_to_two_figures(bad, good, shown):
    assert web_data.times_faster(bad, good) == shown


@pytest.mark.parametrize(("x", "shown"), [(31.2, 30), (36522, 40000), (0, 0), (4.4, 4)])
def test_one_significant_figure(x, shown):
    assert web_data.one_significant(x) == shown


def test_sample_reviews_are_all_100_reviews_one_per_line():
    samples = web_data.sample_reviews()
    assert len(samples) == 100
    assert all("\n" not in text and text == text.strip() for text in samples)
    assert samples[1] == "Oğlum bu oyuncağa bayıldı, iki gündür elinden bırakmıyor ❤️"
    # Review 3 had line breaks; they are single spaces now.
    assert "yoruluyordu. Bu lambayi" in samples[2]


def test_sample_reviews_fit_the_rules_and_the_daily_budget():
    from app.budget import tokens_for_job
    from app.settings import settings

    samples = web_data.sample_reviews()
    assert max(map(len, samples)) <= settings.max_text_chars
    assert len(samples) <= settings.max_texts_per_job
    assert tokens_for_job(samples) <= settings.daily_token_budget


def test_write_makes_the_committed_files(tmp_path):
    web_data.write(DATA, tmp_path)
    for name in ("findings.json", "sample_reviews.json"):
        assert (tmp_path / name).read_text(encoding="utf-8") == (
            WEB / "data" / name
        ).read_text(encoding="utf-8")


def test_main_writes_and_says_where(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(web_data, "write", lambda: None)
    web_data.main()
    assert "web/data/findings.json" in capsys.readouterr().out


def test_the_real_interface_passes_the_check():
    checked, problems = check_interface()
    assert problems == []
    assert checked > 40


def test_check_with_web_adds_the_interface():
    plain, _ = check()
    with_web, problems = check(web=WEB)
    assert problems == []
    assert with_web > plain


@pytest.fixture
def web_copy(tmp_path: Path) -> Path:
    """A throwaway copy of the interface's pages, language files and data."""
    copy = tmp_path / "web"
    for part in ("i18n", "data"):
        shutil.copytree(WEB / part, copy / part)
    for page in WEB.glob("*.html"):
        shutil.copy(page, copy / page.name)
    return copy


def test_a_stale_findings_file_is_reported(web_copy):
    path = web_copy / "data" / "findings.json"
    found = json.loads(path.read_text(encoding="utf-8"))
    found["e1_bad_s"] = 250
    path.write_text(json.dumps(found), encoding="utf-8")
    problems = check_interface(DATA, web_copy)[1]
    assert problems == [
        "Interface: findings.json says e1_bad_s = 250, the data gives 252 "
        "(run bench.web_data)"
    ]


def test_a_missing_findings_file_is_reported(web_copy):
    (web_copy / "data" / "findings.json").unlink()
    problems = check_interface(DATA, web_copy)[1]
    assert any("e1_bad_s = None" in problem for problem in problems)


def test_changed_samples_are_reported(web_copy):
    (web_copy / "data" / "sample_reviews.json").write_text('["x"]', encoding="utf-8")
    problems = check_interface(DATA, web_copy)[1]
    assert problems == ["Interface: sample_reviews.json doesn't match reviews_tr.csv"]


def test_a_number_typed_into_a_language_file_is_reported(web_copy):
    path = web_copy / "i18n" / "en.json"
    path.write_text(
        path.read_text(encoding="utf-8").replace(
            "Sent {e1_slow_requests} slow requests", "Sent 50 slow requests"
        ),
        encoding="utf-8",
    )
    problems = check_interface(DATA, web_copy)[1]
    assert len(problems) == 1
    assert problems[0].startswith("Interface: i18n/en.json has a typed number: 50")


def test_a_number_typed_into_a_page_is_reported(web_copy):
    page = web_copy / "index.html"
    page.write_text(
        page.read_text(encoding="utf-8").replace(
            '<p class="lead" data-i18n="welcome.lead"></p>',
            '<p class="lead">252 s</p>',
        ),
        encoding="utf-8",
    )
    problems = check_interface(DATA, web_copy)[1]
    assert problems == ["Interface: index.html has a typed number: 252"]


def test_digits_in_markup_slots_names_and_code_are_not_numbers():
    assert typed_numbers("{e1_bad_s} s · Experiment 1 · Deney 2 · Gemma 4 E4B") == []
    assert typed_numbers("about 5 s") == ["5"]


def test_a_worded_claim_the_data_no_longer_supports_is_reported(tmp_path, web_copy):
    data = tmp_path / "data"
    shutil.copytree(DATA, data)
    runs = (data / "e4_runs.csv").read_text(encoding="utf-8")
    # Make every download take 0.9 s: "under half a second" is now false.
    lines = runs.splitlines()
    header = lines[0].split(",")
    column = header.index("prepared_s")
    changed = [lines[0]]
    for line in lines[1:]:
        cells = line.split(",")
        if cells[column]:
            cells[column] = "0.9"
        changed.append(",".join(cells))
    (data / "e4_runs.csv").write_text("\n".join(changed) + "\n", encoding="utf-8")
    shutil.copy(WEB / "data" / "findings.json", web_copy / "data" / "findings.json")
    problems = check_interface(data, web_copy)[1]
    claim = 'E4 "Downloading took under half a second"'
    assert f"Interface: {claim} is no longer true of the data" in problems
