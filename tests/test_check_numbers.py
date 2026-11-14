"""Tests for `bench/check_numbers.py`: do the write-up's numbers match the data?

Slice 15 (D114). The real check runs on the real `docs/experiments.md` and
the real CSV files in `bench/data/` (it needs no charting library, so it runs
in CI too). The other tests change one thing in a throwaway copy (a number in
the document, a value in a CSV file, a missing row or section) and check the
difference is found and named.
"""

import shutil
from pathlib import Path

import pytest

from bench import check_numbers
from bench.check_numbers import Cell, Sentence, check, compare, tables

ROOT = Path(__file__).parent.parent
DOC = ROOT / "docs" / "experiments.md"
DATA = ROOT / "bench" / "data"
# The CSV files the check reads (the big E1 probe files are not needed).
NEEDED = [
    "e1_runs.csv",
    "e2_runs.csv",
    "e2_cost.csv",
    "e2_code.csv",
    "ollama_times_at_4.csv",
    "e3_images.csv",
    "e3_images_ci.csv",
    "e3_builds.csv",
    "e3_builds_ci.csv",
    "e4_runs.csv",
    "e4_steps.csv",
    "e4_secrets.csv",
    "cost_tokens.csv",
]


@pytest.fixture
def copies(tmp_path: Path) -> tuple[Path, Path]:
    """A throwaway copy of the document and of the CSV files it is checked against.

    The tests below compare against what the unchanged copy already reports
    (see `new_problems`), so they test only the one change they make.
    """
    data = tmp_path / "data"
    data.mkdir()
    for name in NEEDED:
        shutil.copy(DATA / name, data / name)
    doc = tmp_path / "experiments.md"
    shutil.copy(DOC, doc)
    return doc, data


def replace_once(path: Path, old: str, new: str) -> None:
    """Change one exact piece of a file (it must be there exactly once)."""
    text = path.read_text(encoding="utf-8")
    assert text.count(old) == 1, old
    path.write_text(text.replace(old, new), encoding="utf-8")


def new_problems(doc: Path, data: Path) -> list[str]:
    """Problems in the changed copy that the real document doesn't have."""
    before = set(check(DOC, DATA)[1])
    return [p for p in check(doc, data)[1] if p not in before]


def test_the_real_document_matches_the_real_data():
    """Every number in the write-up's Results tables matches the CSV files."""
    checked, problems = check(DOC, DATA)
    assert problems == []
    assert checked > 300  # every table cell, not a handful


def test_a_changed_number_in_the_document_is_named(copies):
    """A mistyped build time is found, with its table, row and column."""
    doc, data = copies
    replace_once(
        doc,
        "| uv-good | 5.5 (5.5–5.5) | 0.5 (0.5–0.5) | 3.0 (2.9–4.0) |",
        "| uv-good | 5.5 (5.5–5.5) | 0.5 (0.5–0.5) | 3.1 (2.9–4.0) |",
    )
    problems = new_problems(doc, data)
    assert problems == [
        "E3 Mac build time, uv-good, After adding one package: "
        'doc says "3.1 (2.9–4.0)", data gives "3.0 (2.9–4.0)"'
    ]


def test_a_changed_value_in_the_data_is_found(copies):
    """New measurements (here 100 more Gemma tokens for one review) no longer match."""
    doc, data = copies
    path = data / "cost_tokens.csv"
    lines = path.read_text(encoding="utf-8").splitlines()
    cells = lines[1].split(",")
    cells[8] = str(int(cells[8]) + 100)  # gemma_tr of the first review
    lines[1] = ",".join(cells)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    problems = new_problems(doc, data)
    assert problems
    assert all(p.startswith("Cost") for p in problems)
    assert any("Gemma (from Ollama), all, Turkish tokens" in p for p in problems)


def test_a_headline_sentence_is_checked(copies):
    """The "pip-bad is 5.1× the size" sentence under the table is checked too."""
    doc, data = copies
    replace_once(doc, "pip-bad is **5.1×**", "pip-bad is **5.2×**")
    problems = new_problems(doc, data)
    assert len(problems) == 1
    assert problems[0].startswith('E3 Mac ratio: sentence "pip-bad is 5.1×')


def test_a_missing_row_is_reported(copies):
    """A row taken out of a table is reported, not skipped."""
    doc, data = copies
    replace_once(doc, "| pip-slim | 286 | 95 | 159 | no | 12 |\n", "")
    problems = new_problems(doc, data)
    assert "E3 Mac size table, pip-slim: row not found" in problems


def test_a_missing_section_is_reported(copies):
    """If a section's Results disappear, the check says so."""
    doc, data = copies
    replace_once(doc, "## E4 — CI", "## Something else")
    problems = new_problems(doc, data)
    assert "E4: no Results section found in experiments.md" in problems


def test_tables_fill_in_a_blank_first_cell_from_the_row_above():
    """`| | B50 |` under `| 0.5 s | A50 |` belongs to 0.5 s; emphasis is ignored."""
    text = "| Wait | Setting |\n|---|---|\n| 0.5 s | A50 |\n| | **B50** |\n"
    [table] = tables(text)
    assert table.header == ["Wait", "Setting"]
    assert table.rows == [["0.5 s", "A50"], ["0.5 s", "B50"]]


def test_compare_reports_a_missing_table_and_column():
    """Unknown tables and columns are problems, never silent passes."""
    text = "| A | B |\n|---|---|\n| x | 1 |\n"
    problems = compare(
        text,
        [
            Cell("T", ("Nope",), ("x",), "B", "1"),
            Cell("T", ("A",), ("x",), "C", "1"),
            Cell("T", ("A",), ("x",), "B", "1"),
            Sentence("S", "not here"),
        ],
    )
    assert problems == [
        "T: table not found",
        "T: column 'C' not found",
        'S: sentence "not here" not found',
    ]


def test_main_prints_each_problem_and_fails(monkeypatch, capsys):
    """Any difference ends with exit code 1 and lists every difference."""
    monkeypatch.setattr(check_numbers, "check", lambda **_: (5, ["E1: one", "E2: two"]))
    with pytest.raises(SystemExit) as stopped:
        check_numbers.main()
    assert stopped.value.code == 1
    out = capsys.readouterr().out
    assert "2 of 5 numbers" in out and "- E1: one" in out and "- E2: two" in out


def test_main_says_all_match(monkeypatch, capsys):
    """With no difference it says how many numbers it checked."""
    monkeypatch.setattr(check_numbers, "check", lambda **_: (308, []))
    check_numbers.main()
    assert "All 308 numbers" in capsys.readouterr().out


def test_missing_measurements_are_reported_not_a_crash(copies):
    """A rerun where every E3 build failed: a clear difference, no traceback."""
    doc, data = copies
    path = data / "e3_builds.csv"
    text = path.read_text(encoding="utf-8").replace(",yes,", ",no,")
    path.write_text(text, encoding="utf-8")
    problems = new_problems(doc, data)
    assert any(p.startswith("E3: the numbers can't be recomputed") for p in problems), (
        problems
    )


def test_pip_adding_different_amounts_is_reported_as_itself(copies):
    """The data contradicts "exactly N MB on either base": said plainly."""
    doc, data = copies
    path = data / "e3_images.csv"
    lines = path.read_text(encoding="utf-8").splitlines()
    for index, line in enumerate(lines):
        if line.startswith("mac,pip-slim,"):
            cells = line.split(",")
            cells[2] = str(int(cells[2]) + 5_000_000)  # 5 MB more on slim
            lines[index] = ",".join(cells)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    problems = new_problems(doc, data)
    assert any("pip adds exactly the same on either base, but" in p for p in problems)


def test_the_download_row_follows_the_measured_library_count(copies):
    """44 libraries in the data and the doc: both columns found, no false alarm."""
    doc, data = copies
    path = data / "e4_runs.csv"
    path.write_text(
        path.read_text(encoding="utf-8").replace(",43,", ",44,"), encoding="utf-8"
    )
    replace_once(
        doc,
        'of which downloading 43 libraries (uv\'s "Prepared")',
        'of which downloading 44 libraries (uv\'s "Prepared")',
    )
    assert new_problems(doc, data) == []


# ---- The README and the write-up (Slice 18a, D131) --------------------------


# Made-up values, so these tests check the rules, not today's measurements:
# rerunning a benchmark or changing a default must not break them.
NUMBERS = {"slow_s": 252, "fast_ms": 7.0, "share": 0.5, "split": None, "flag": True}
DEFAULTS = {"max_texts": 1000, "beat_s": 5.0, "model": "gemma4:e4b"}


def story(tmp_path: Path, text: str) -> list[str]:
    """The problems `check_story` finds in a small made-up document."""
    doc = tmp_path / "README.md"
    doc.write_text(text, encoding="utf-8")
    return check_numbers.check_story([doc], numbers=NUMBERS, defaults=DEFAULTS)[1]


def test_the_real_readme_and_write_up_match_the_data():
    """Every labelled number is right and no number is left unlabelled."""
    checked, problems = check_numbers.check_story()
    assert problems == []
    assert checked > 50


def test_main_checks_the_readme_and_write_up_too():
    """The command-line check (and so CI's test of it) includes both files."""
    checked_all, _ = check(web=check_numbers.WEB, story=check_numbers.STORY_DOCS)
    checked_without, _ = check(web=check_numbers.WEB)
    assert checked_all > checked_without


def test_a_right_labelled_number_passes(tmp_path):
    """Measured numbers and setting defaults, thousands written with commas."""
    assert story(tmp_path, "It took 252<!--n:slow_s--> seconds.") == []
    assert story(tmp_path, "It took 7<!--n:fast_ms--> ms and 0.5<!--n:share-->.") == []
    assert story(tmp_path, "Up to 1,000<!--s:max_texts--> texts.") == []
    assert story(tmp_path, "Every 5<!--s:beat_s--> seconds.") == []


def test_a_wrong_labelled_number_is_named(tmp_path):
    """The document's number, the label and the data's number are all given."""
    assert story(tmp_path, "It took 250<!--n:slow_s--> seconds.") == [
        "README.md: says 250 for slow_s, the data give 252"
    ]
    assert story(tmp_path, "Up to 1000<!--s:max_texts--> texts.") == [
        "README.md: says 1000 for max_texts, the settings give 1,000"
    ]


def test_an_unknown_label_is_reported(tmp_path):
    """A label that names nothing can't vouch for its number."""
    assert story(tmp_path, "About 7<!--n:no_such_number--> ms.") == [
        "README.md: 7 is labelled no_such_number, which the data don't have"
    ]


def test_runs_that_disagree_are_reported_as_such(tmp_path):
    """A value the runs don't agree on is named as that, not as a typo."""
    assert story(tmp_path, "Done 4<!--n:split--> times.") == [
        "README.md: 4 is labelled split, but the runs disagree on it, "
        "so the data give no single value"
    ]


def test_a_word_or_true_false_is_not_a_number(tmp_path):
    """Labelling text or a yes/no value is reported, never a crash or a pass."""
    assert story(tmp_path, "It was 1<!--n:flag--> and 4<!--s:model-->.") == [
        "README.md: 1 is labelled flag, which is not a number",
        "README.md: 4 is labelled model, which is not a number",
    ]


def test_a_number_without_a_label_is_reported(tmp_path):
    """Like a typed number on the pages: it would go stale unnoticed."""
    assert story(tmp_path, "It took 252 seconds.") == [
        "README.md: number without a label: 252"
    ]


def test_code_links_names_and_steps_are_not_numbers_to_check(tmp_path):
    """Commands, addresses, link targets, names and step numbers are skipped."""
    text = (
        "### 1. Install Docker\n"
        "1. Open `http://localhost:8080` and see `202`.\n"
        "```\ndocker compose up --wait 5\n```\n"
        "Read [D29](docs/decision-log.md#d29--x) about E1 to E4, "
        "Gemma 4 E4B, Python 3.12, Postgres 16 and WSL 2. Go back to step 3.\n"
        "<!-- a note: anything 123 -->\n"
        '<img src="a.png" width="640">\n'
        "See https://example.com/page2.\n"
        "~~~\nsleep 9\n~~~\n"
        "See [the entry](docs/decision-log.md#d1--a-b(c2)).\n"
    )
    assert story(tmp_path, text) == []


def test_a_less_than_sign_does_not_hide_numbers(tmp_path):
    """Only real HTML tags are skipped, not text between "<" and ">"."""
    assert story(tmp_path, "Waits < 5 seconds and > 3 tries.") == [
        "README.md: number without a label: 5",
        "README.md: number without a label: 3",
    ]


def test_setting_defaults_are_read_without_importing_settings(tmp_path):
    """Only class Settings counts; Field(default=...) and Field(5, ...) are read."""
    path = tmp_path / "settings.py"
    path.write_text(
        "class Settings:\n"
        "    a: int = 3\n"
        "    b: int = Field(default=200_000, ge=1)\n"
        "    c: int = 25 * 1024\n"
        "    d: str = Field(min_length=32)\n"
        "    e: float = Field(30.0, gt=0)\n"
        "class Helper:\n"
        "    a: int = 8\n"
        "def helper():\n"
        "    b: int = 9\n",
        encoding="utf-8",
    )
    assert check_numbers.setting_defaults(path) == {"a": 3, "b": 200_000, "e": 30.0}


def test_the_real_settings_file_gives_its_defaults():
    """The labels the README uses exist in app/settings.py."""
    defaults = check_numbers.setting_defaults()
    for name in ("max_texts_per_job", "texts_at_once", "heartbeat_seconds"):
        assert check_numbers.as_written(defaults[name]) is not None
