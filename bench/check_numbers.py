"""Check that the numbers in docs/experiments.md still match the measured data.

Slice 15 (spec 9.5, D114). The write-up of each experiment (E1–E4 and the
Turkish vs English cost) has Results tables that were typed from the numbers
the scripts printed. If a CSV file in `bench/data/` is measured again, or a
number is mistyped, the write-up and the data disagree without anyone
noticing. This script recomputes, from the CSV files only, every number in
every Results table (plus a few headline sentences right under the tables,
such as "pip-bad is 5.1× the size of uv-good") and compares each one with
what the document says.

It checks only the Results tables and those sentences. Numbers in ordinary
prose, in "Surprises" and in the "My prediction, part by part" verdict
tables are not checked: they are worded freely and judged by hand.

It also checks the web interface's story numbers (Slice 17, D120):
`web/data/findings.json` and `web/data/sample_reviews.json` must equal a
fresh computation from the CSV files (`bench/web_data.py`); the pages and
their language files must contain no number typed by hand (every number is a
{slot} filled from those files); and the few measured facts the pages say in
words (e.g. "under half a second") must still be true of the data.
A difference is reported, never fixed: after a real rerun, the write-up and
the verdicts must be updated by a person (D114).

It needs no charting library, so it runs in the normal test suite and in CI.
Run from the project folder:

    uv run python -m bench.check_numbers

It is also run by `uv run --group bench python -m bench.run_all --charts-only`.
"""

import ast
import csv
import html.parser
import json
import re
import statistics
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from bench import cost_sums, web_data

ROOT = Path(__file__).parent.parent
DOC = ROOT / "docs" / "experiments.md"
DATA = Path(__file__).parent / "data"
WEB = ROOT / "web"

MB = 1_000_000  # Docker counts sizes in decimal megabytes, as the doc does
DASH = "–"  # the doc writes spreads with an en dash: "5.5 (5.4–5.6)"


# ---- Reading the document ---------------------------------------------------


@dataclass
class Table:
    """One Markdown table: its header cells and its body rows (lists of cells)."""

    header: list[str]
    rows: list[list[str]]


@dataclass
class Cell:
    """One number the document should show, and where.

    `table` is the table's first header cells (enough to tell it apart),
    `nth` which of the tables with that header (0 = the first), `row` the
    leading label cells of its row, `column` the header of its column (or
    its position, for tables whose headers are empty).
    """

    name: str
    table: tuple[str, ...]
    row: tuple[str, ...]
    column: str | int
    value: str
    nth: int = 0


@dataclass
class Sentence:
    """A headline sentence that must appear, word for word, in the section."""

    name: str
    text: str


@dataclass
class Finding:
    """A problem found in the data itself, reported as it is.

    Used when a claim in the doc no longer holds for the data in a way no
    single number shows (e.g. "pip adds exactly N MB on either base" when
    the two bases now differ).
    """

    name: str
    text: str


Expected = Cell | Sentence | Finding


def clean(text: str) -> str:
    """A cell without Markdown emphasis (`**bold**`, `*italic*`) or edge spaces.

    The emphasis only changes how a number looks, not which number it is.
    """
    return text.replace("*", "").strip()


def flat(text: str) -> str:
    """Text without emphasis and with all line breaks and runs of spaces as
    one space, so a sentence wrapped over two lines is still found."""
    return re.sub(r"\s+", " ", text.replace("*", ""))


def sections(doc: str) -> dict[str, str]:
    """{section key: its Results part} for E1–E4 and the cost extra.

    A section starts at its `## ` heading; its Results part runs from
    `### Results` to the next `### ` heading. The key is the heading's first
    word ("E1" … "E4", "Extra").
    """
    found = {}
    parts = re.split(r"^## ", doc, flags=re.MULTILINE)
    for part in parts[1:]:
        key = part.split()[0].rstrip(":")
        results = re.search(r"^### Results\n(.*?)(?=^### |\Z)", part, re.M | re.S)
        if results:
            found[key] = results.group(1)
    return found


def tables(text: str) -> list[Table]:
    """Every Markdown table in some text, in order.

    A blank first cell in a body row means "same as the row above" (the doc
    writes `| | B50 |` under `| 0.5 s | A50 |`), so it is filled in.
    """
    found: list[Table] = []
    lines: list[str] = []
    for line in [*text.splitlines(), ""]:
        if line.startswith("|"):
            lines.append(line)
            continue
        if len(lines) >= 2:
            cells = [
                [clean(c) for c in row.strip().strip("|").split("|")] for row in lines
            ]
            body = cells[2:]  # skip the header and the |---| separator line
            for previous, row in zip(body, body[1:], strict=False):
                if row and row[0] == "":
                    row[0] = previous[0]
            found.append(Table(cells[0], body))
        lines = []
    return found


def compare(text: str, expected: list[Expected]) -> list[str]:
    """What differs between a section's Results part and the expected numbers."""
    problems = []
    all_tables = tables(text)
    for item in expected:
        if isinstance(item, Finding):
            problems.append(f"{item.name}: {item.text}")
            continue
        if isinstance(item, Sentence):
            if item.text not in flat(text):
                problems.append(f'{item.name}: sentence "{item.text}" not found')
            continue
        matches = [
            t for t in all_tables if tuple(t.header[: len(item.table)]) == item.table
        ]
        if len(matches) <= item.nth:
            problems.append(f"{item.name}: table not found")
            continue
        table = matches[item.nth]
        row = next(
            (r for r in table.rows if tuple(r[: len(item.row)]) == item.row), None
        )
        label = ", ".join(item.row)
        if row is None:
            problems.append(f"{item.name}, {label}: row not found")
            continue
        if isinstance(item.column, int):
            index = item.column
        elif item.column in table.header:
            index = table.header.index(item.column)
        else:
            problems.append(f"{item.name}: column {item.column!r} not found")
            continue
        said = row[index] if index < len(row) else ""
        if said != item.value:
            problems.append(
                f'{item.name}, {label}, {item.column or "value"}: doc says "{said}", '
                f'data gives "{item.value}"'
            )
    return problems


# ---- Reading the data and formatting numbers like the document ---------------


def read(data: Path, name: str) -> list[dict]:
    """One CSV file from the data folder as a list of {column: value} rows."""
    with (data / name).open(encoding="utf-8", newline="") as file:
        return list(csv.DictReader(file))


def values(rows: list[dict], column: str) -> list[float]:
    """The measured values of one column (blank cells are left out)."""
    return [float(r[column]) for r in rows if r.get(column, "") != ""]


def spread(numbers: list[float], number: Callable[[float], str], unit: str = "") -> str:
    """ "median unit (min–max)", every number written by `number`."""
    median = statistics.median(numbers)
    return (
        f"{number(median)}{unit} ({number(min(numbers))}{DASH}{number(max(numbers))})"
    )


def one(x: float) -> str:
    """One decimal, e.g. 5.5."""
    return f"{x:.1f}"


def two(x: float) -> str:
    """Two decimals, e.g. 0.48."""
    return f"{x:.2f}"


def whole(x: float) -> str:
    """A whole number with thousands commas, e.g. 1,331."""
    return f"{x:,.0f}"


def median(numbers: list[float]) -> float:
    """The median (middle value)."""
    return statistics.median(numbers)


# ---- E1 -----------------------------------------------------------------------

E1_WAITS = {"0.5 s": 0.5, "1 s": 1.0, "2 s": 2.0, "4 s": 4.0}
E1_SETTINGS = ["A50", "B50", "B100", "C50"]
# The ways of the real-AI check, by the start of their row label in the doc.
E1_AI_ROWS = {
    "A": "A (blocking client in `async def`)",
    "B": "B (blocking client in plain `def`)",
    "C": "C (Kuyruk's async client)",
}


def e1_time(ms: list[float], mode: str, with_spread: bool) -> str:
    """A probe time written as the doc writes it.

    Mode A's times are seconds long, so they are in seconds with one
    decimal; everything else in milliseconds: whole numbers from 100 ms up,
    one decimal below. The spread uses the median's unit and decimals.
    """
    if mode == "A":
        seconds = [m / 1000 for m in ms]
        return (
            spread(seconds, one, " s") if with_spread else f"{one(median(seconds))} s"
        )
    number = whole if median(ms) >= 100 else one
    return spread(ms, number, " ms") if with_spread else f"{number(median(ms))} ms"


def e1_cells(
    name: str, table: tuple, row: tuple, runs: list[dict], mode: str
) -> list[Cell]:
    """The four time columns of one E1 row, from its 5 runs."""
    return [
        Cell(
            name,
            table,
            row,
            "/health p50",
            e1_time(values(runs, "health_p50_ms"), mode, False),
        ),
        Cell(
            name,
            table,
            row,
            "/health p95",
            e1_time(values(runs, "health_p95_ms"), mode, True),
        ),
        Cell(
            name,
            table,
            row,
            "ping p95",
            e1_time(values(runs, "ping_p95_ms"), mode, False),
        ),
        Cell(
            name,
            table,
            row,
            "Last done",
            f"{one(median(values(runs, 'slow_last_finish_s')))} s",
        ),
    ]


def e1_expected(data: Path) -> list[Expected]:
    """Every number of E1's three Results tables."""
    runs = read(data, "e1_runs.csv")

    def pick(
        kind: str, setting: str | None = None, wait: float | None = None, mode=None
    ):
        """The runs of one kind, narrowed by setting, wait and mode if given."""
        return [
            r
            for r in runs
            if r["kind"] == kind
            and (setting is None or r["setting"] == setting)
            and (mode is None or r["mode"] == mode)
            and (wait is None or float(r["wait_seconds"]) == wait)
        ]

    expected: list[Expected] = []
    for wait_label, wait in E1_WAITS.items():
        for setting in E1_SETTINGS:
            expected += e1_cells(
                "E1 main table",
                ("Wait", "Setting"),
                (wait_label, setting),
                pick("sleep", setting, wait),
                setting[0],
            )
    for mode, label in E1_AI_ROWS.items():
        expected += e1_cells(
            "E1 real-AI table", ("Way",), (label,), pick("ai", mode=mode), mode
        )
    after = ("Setting (1 s wait)",)
    for setting in ["B50", "B100"]:
        expected += e1_cells(
            "E1 after-fix table",
            after,
            (f"{setting}, before (plain-def /health)",),
            pick("sleep", setting, 1.0),
            "B",
        )
        expected += e1_cells(
            "E1 after-fix table",
            after,
            (f"{setting}, after (async /health)",),
            pick("after_fix", setting, 1.0),
            "B",
        )
    return expected


# ---- E2 -----------------------------------------------------------------------

E2_WAITS = {"0.5 s": "0.5", "1 s": "1", "2 s": "2", "4 s": "4"}
E2_STALES = {"10 s": "10", "30 s": "30", "60 s": "60"}


def seconds(numbers: list[float]) -> str:
    """ "27.8 s": the median, one decimal."""
    return f"{one(median(numbers))} s"


def every_run(runs: list[dict], column: str) -> int | None:
    """The value of a whole-number column if it is the same in every run."""
    found = {int(float(r[column])) for r in runs}
    return found.pop() if len(found) == 1 else None


def e2_expected(data: Path) -> list[Expected]:
    """Every number of E2's four Results tables."""
    runs = [r for r in read(data, "e2_runs.csv") if not r["error"]]
    cost = read(data, "e2_cost.csv")
    code = read(data, "e2_code.csv")

    def pick(**match: str) -> list[dict]:
        """The good runs whose columns have exactly the given values."""
        return [r for r in runs if all(r[k] == v for k, v in match.items())]

    expected: list[Expected] = []
    # What is lost: the broken and fixed rows compare the same settings (the
    # main runs plus the four sensitivity waits); recovery time from the main
    # runs only (replayed times), as the row says.
    table = ("Version",)
    name = "E2 lost/redone table"
    broken = pick(kind="broken", part="main") + pick(kind="broken", part="sensitivity")
    fixed = pick(kind="fixed", part="main") + pick(kind="fixed", part="sensitivity")
    ai = pick(part="ai")
    texts = every_run(broken, "texts")
    lost = every_run(broken, "lost")
    row = ("Broken (BackgroundTasks in the API)",)
    states = {r["final_state"] for r in broken}
    # How long each broken run was watched after the restart, in whole
    # seconds (the script stops a tenth of a second or so after 300 or 60 s).
    watched = [round(float(r["watched_after_restart_s"])) for r in broken]
    expected += [
        Cell(name, table, row, "Runs", str(len(broken))),
        Cell(name, table, row, "Lost", f"{lost} of {texts} in every run"),
        Cell(name, table, row, "Redone", str(every_run(broken, "redone"))),
        Cell(
            name,
            table,
            row,
            "Job at the end",
            f'still "{states.pop() if len(states) == 1 else "mixed"}" in every run '
            f"(watched 5 min in {watched.count(300)} runs, "
            f"60 s in {watched.count(60)})",
        ),
        Cell(
            name,
            table,
            row,
            "Recovery time",
            "never" if not values(broken, "recovery_s") else "measured",
        ),
    ]
    for row, group, timed in [
        (
            ("Fixed (worker + recovery), replayed times",),
            fixed,
            pick(kind="fixed", part="main"),
        ),
        (("Fixed, real AI (12 texts, killed at 6)",), ai, ai),
    ]:
        states = {r["final_state"] for r in group}
        expected += [
            Cell(name, table, row, "Runs", str(len(group))),
            Cell(name, table, row, "Lost", str(every_run(group, "lost"))),
            Cell(
                name, table, row, "Redone", f"{every_run(group, 'redone')} in every run"
            ),
            Cell(
                name,
                table,
                row,
                "Job at the end",
                f"{states.pop() if len(states) == 1 else 'mixed'} in every run",
            ),
            Cell(
                name,
                table,
                row,
                "Recovery time",
                spread(values(timed, "recovery_s"), one, " s"),
            ),
        ]

    # Recovery time vs wait per text.
    name, table = "E2 recovery vs wait", ("Wait per text",)
    replay = [float(r["seconds"]) for r in read(data, "ollama_times_at_4.csv")]
    rows = [
        ((label,), pick(part="sensitivity", kind="fixed", wait=w))
        for label, w in E2_WAITS.items()
    ]
    rows += [
        (
            (f"replayed (median {one(median(replay))} s)",),
            pick(part="main", kind="fixed"),
        ),
        (("real AI",), ai),
    ]
    for row, group in rows:
        takeover = values(group, "takeover_s")
        first = [float(r["recovery_s"]) - float(r["takeover_s"]) for r in group]
        expected += [
            Cell(name, table, row, "Kill → takeover", seconds(takeover)),
            Cell(name, table, row, "Takeover → first text", seconds(first)),
            Cell(
                name,
                table,
                row,
                "Recovery",
                spread(values(group, "recovery_s"), one, " s"),
            ),
            Cell(
                name,
                table,
                row,
                "Job done after the kill",
                seconds(values(group, "finish_s")),
            ),
        ]

    # Recovery time vs stale time.
    name, table = "E2 recovery vs stale", ("Stuck after",)
    for label, stale in E2_STALES.items():
        group = pick(part="stale", stale_seconds=stale)
        row = (label,)
        expected += [
            Cell(
                name,
                table,
                row,
                "Kill → takeover",
                seconds(values(group, "takeover_s")),
            ),
            Cell(
                name,
                table,
                row,
                "Recovery",
                spread(values(group, "recovery_s"), one, " s"),
            ),
            Cell(name, table, row, "Redone", str(every_run(group, "redone"))),
        ]

    # Cost of the fix: a two-column table with an empty header.
    name, table = "E2 cost of the fix", ("", "")
    writes = every_run(cost, "all_writes")
    beats = every_run(cost, "heartbeat_writes")
    one_per_beat = all(r["heartbeat_writes"] == r["heartbeats_expected"] for r in cost)
    lines = {r["what"]: r["lines"] for r in code}
    worker = next(v for k, v in lines.items() if k.startswith("worker"))
    recovery = next(
        v for k, v in lines.items() if k.startswith("of which crash recovery")
    )
    expected += [
        Cell(
            name,
            table,
            (
                "Database writes for one uninterrupted 40-text job "
                f"({one(median(values(cost, 'job_s')))} s)",
            ),
            1,
            f"{writes} (same in all {len(cost)} jobs)",
        ),
        Cell(
            name,
            table,
            ("of which heartbeat",),
            1,
            f"{beats} ({beats / writes:.1%})"
            + (", exactly one per 5 s of running" if one_per_beat else ""),
        ),
        Cell(name, table, ("Worker (`app/worker.py`), lines of code",), 1, worker),
        Cell(
            name,
            table,
            (
                "of which crash recovery (takeover, heartbeat, hand back; "
                "demo-only lines left out)",
            ),
            1,
            recovery,
        ),
    ]
    return expected


# ---- E3 -----------------------------------------------------------------------

E3_MAIN = ["pip-bad", "pip-good", "uv-bad", "uv-good"]
E3_STEPS = {
    "cold": "Cold build",
    "code": "After a one-line code change",
    "package": "After adding one package",
}
# Each Dockerfile's base image (only the FROM line tells them apart).
E3_BASE = {
    "pip-bad": "base-full",
    "pip-good": "base-full",
    "pip-slim": "base-slim",
    "uv-bad": "base-slim",
    "uv-good": "base-slim",
}
E3_BASE_ROWS = {"base-full": "full base alone", "base-slim": "slim base alone"}


def e3_sizes(images: list[dict], column: str) -> dict[str, float]:
    """{image: size in MB} for one size column."""
    return {r["image"]: float(r[column]) / MB for r in images if r[column]}


def e3_build_cells(name: str, nth: int, builds: list[dict]) -> list[Cell]:
    """Median (min–max) build time per Dockerfile and step."""
    cells = []
    for image in E3_MAIN:
        for step, column in E3_STEPS.items():
            times = [
                float(r["seconds"])
                for r in builds
                if r["dockerfile"] == image and r["step"] == step and r["ok"] == "yes"
            ]
            cells.append(
                Cell(name, ("Dockerfile",), (image,), column, spread(times, one), nth)
            )
    return cells


def e3_ratio_sentence(full: dict, packed: dict) -> str:
    """ "pip-bad is 5.1× the size of uv-good (6.3× compressed)." """
    return (
        f"pip-bad is {full['pip-bad'] / full['uv-good']:.1f}× the size of uv-good "
        f"({packed['pip-bad'] / packed['uv-good']:.1f}× compressed)."
    )


def e3_build_medians(builds: list[dict]) -> dict[tuple[str, str], float]:
    """{(Dockerfile, step): median seconds} (unrounded, for the ratios)."""
    found: dict[tuple[str, str], list[float]] = {}
    for r in builds:
        if r["ok"] == "yes":
            found.setdefault((r["dockerfile"], r["step"]), []).append(
                float(r["seconds"])
            )
    return {key: median(times) for key, times in found.items()}


def e3_expected(data: Path) -> list[Expected]:
    """Every number of E3's five Results tables and the sentences under them."""
    images = read(data, "e3_images.csv")
    images_ci = read(data, "e3_images_ci.csv")
    expected: list[Expected] = []

    # Mac sizes.
    name, table = "E3 Mac size table", ("Image", "Uncompressed")
    full = e3_sizes(images, "image_bytes")
    packed = e3_sizes(images, "compressed_bytes")
    by_image = {r["image"]: r for r in images}
    for image, base in E3_BASE.items():
        row = (image,)
        share = full[base] / full[image]
        # The doc gives the base image's share only where it is most of the
        # image (the full base); for the slim ones it gives the size alone.
        of_which = whole(full[base]) + (
            f" ({share:.0%})" if base == "base-full" else ""
        )
        expected += [
            Cell(name, table, row, "Uncompressed", whole(full[image])),
            Cell(name, table, row, "Compressed", whole(packed[image])),
            Cell(name, table, row, "Of which base image", of_which),
            Cell(name, table, row, "curl inside", by_image[image]["has_curl"]),
            Cell(name, table, row, "Dockerfile lines", by_image[image]["lines"]),
        ]
    for base, label in E3_BASE_ROWS.items():
        row = (label,)
        expected += [
            Cell(name, table, row, "Uncompressed", whole(full[base])),
            Cell(name, table, row, "Compressed", whole(packed[base])),
            Cell(name, table, row, "curl inside", by_image[base]["has_curl"]),
        ]
    expected.append(Sentence("E3 Mac ratio", e3_ratio_sentence(full, packed)))

    # Where the size difference comes from (Mac).
    name, table = "E3 size difference", ("Step",)
    gap = full["pip-good"] - full["uv-good"]
    base_part = full["pip-good"] - full["pip-slim"]
    rest = full["pip-slim"] - full["uv-good"]
    order = max(
        abs(full["pip-good"] - full["pip-bad"]), abs(full["uv-good"] - full["uv-bad"])
    )
    expected += [
        Sentence(
            name,
            "Where the size difference comes from "
            f"(pip-good → uv-good, {whole(gap)} MB",
        ),
        Cell(
            name,
            table,
            ("pip-good → pip-slim",),
            "Saves",
            f"{whole(base_part)} MB ({base_part / gap:.0%})",
        ),
        Cell(
            name,
            table,
            ("pip-slim → uv-good",),
            "Saves",
            f"{whole(rest)} MB ({rest / gap:.0%})",
        ),
        Cell(name, table, ("bad → good layer order",), "Saves", f"{whole(order)} MB"),
        Sentence(
            name,
            f"pip adds exactly {whole(full['pip-good'] - full['base-full'])} MB on "
            f"either base; uv adds {whole(full['uv-good'] - full['base-slim'])} MB.",
        ),
    ]
    # The pip addition is the same on both bases only if the numbers agree.
    if whole(full["pip-good"] - full["base-full"]) != whole(
        full["pip-slim"] - full["base-slim"]
    ):
        expected.append(
            Finding(
                name,
                "the doc says pip adds exactly the same on either base, but in the "
                f"data it adds {whole(full['pip-good'] - full['base-full'])} MB on "
                f"the full base and {whole(full['pip-slim'] - full['base-slim'])} MB "
                "on the slim one",
            )
        )

    # Mac build times, and the sentence under them.
    builds = read(data, "e3_builds.csv")
    expected += e3_build_cells("E3 Mac build time", 0, builds)
    m = e3_build_medians(builds)
    expected.append(
        Sentence(
            "E3 Mac build time",
            f"After a code change, good layer order saved "
            f"{one(m['pip-bad', 'code'] - m['pip-good', 'code'])} s with pip "
            f"({m['pip-bad', 'code'] / m['pip-good', 'code']:.0f}× faster) and "
            f"{one(m['uv-bad', 'code'] - m['uv-good', 'code'])} s with uv "
            f"({m['uv-bad', 'code'] / m['uv-good', 'code']:.0f}× faster).",
        )
    )

    # GitHub sizes: pairs with identical sizes share one row.
    name, table = "E3 GitHub size table", ("Image", "Uncompressed (MB)")
    full_ci = e3_sizes(images_ci, "image_bytes")
    packed_ci = e3_sizes(images_ci, "compressed_bytes")
    by_image_ci = {r["image"]: r for r in images_ci}
    groups = {
        "pip-bad / pip-good": ["pip-bad", "pip-good"],
        "pip-slim": ["pip-slim"],
        "uv-bad / uv-good": ["uv-bad", "uv-good"],
        "full base alone": ["base-full"],
        "slim base alone": ["base-slim"],
    }
    for label, members in groups.items():
        for column, number in [
            ("Uncompressed (MB)", lambda i: whole(full_ci[i])),
            ("Compressed (MB)", lambda i: whole(packed_ci[i])),
            ("curl inside", lambda i: by_image_ci[i]["has_curl"]),
        ]:
            found = {number(i) for i in members}
            # Images sharing a row must give the same number, or the row is wrong.
            value = (
                found.pop()
                if len(found) == 1
                else " / ".join(number(i) for i in members)
            )
            expected.append(Cell(name, table, (label,), column, value))
    gap = full_ci["pip-good"] - full_ci["uv-good"]
    base_part = full_ci["pip-good"] - full_ci["pip-slim"]
    rest = full_ci["pip-slim"] - full_ci["uv-good"]
    expected += [
        Sentence("E3 GitHub ratio", e3_ratio_sentence(full_ci, packed_ci)),
        Sentence(
            "E3 GitHub size split",
            f"Of the {whole(gap)} MB gap pip-good → uv-good, {whole(base_part)} MB "
            f"({base_part / gap:.0%}) is the base image alone and {whole(rest)} MB "
            f"({rest / gap:.0%}) is uv + two stages",
        ),
    ]

    # GitHub build times, and the layer-order sentence under them.
    builds_ci = read(data, "e3_builds_ci.csv")
    expected += e3_build_cells("E3 GitHub build time", 1, builds_ci)
    m = e3_build_medians(builds_ci)
    expected.append(
        Sentence(
            "E3 GitHub build time",
            f"Good layer order made a code change "
            f"{m['pip-bad', 'code'] / m['pip-good', 'code']:.1f}× faster with pip and "
            f"{m['uv-bad', 'code'] / m['uv-good', 'code']:.1f}× with uv.",
        )
    )
    return expected


# ---- E4 -----------------------------------------------------------------------

# The doc's rows of the cache table: which CI steps (by the start of their
# name) each one adds up, and how many decimals the doc gives it (two for
# the sub-second steps, where one decimal would hide the difference).
E4_STEP_ROWS = {
    "Postgres start-up": (["Initialize containers"], one),
    "setup-uv step (incl. restoring the cache)": (["Run astral-sh/setup-uv"], one),
    "Install (`uv sync`)": (["Install dependencies"], two),
    "Cache save step at the end": (["Post Run astral-sh/setup-uv"], two),
    "Tests (ruff, fast tests, Postgres tests)": (
        ["Ruff", "Fast tests", "Postgres tests"],
        one,
    ),
}
# Cells the doc writes as the median only, without the spread (all others
# have the spread).
E4_MEDIAN_ONLY = {
    ("Postgres start-up", "on"),
    ("Postgres start-up", "off"),
    ("Install (`uv sync`)", "on"),
    ("Cache save step at the end", "on"),
    ("Cache save step at the end", "off"),
}
# The secrets table: row label → (workflow, event, branch, note start).
E4_SECRET_ROWS = {
    "CI, push to main": ("ci.yml", "push", "main", ""),
    "CI, push to my own branch (`slice-9`)": ("ci.yml", "push", "slice-9", ""),
    "CI, pull request #1 from my own branch": ("ci.yml", "pull_request", "slice-9", ""),
    "CI, started by hand (the cache runs, including run 21)": (
        "ci.yml",
        "workflow_dispatch",
        "main",
        "manual run",
    ),
    "Release, after a push to main": ("release.yml", "workflow_run", "main", ""),
    "Release, after a run started by hand": (
        "release.yml",
        "workflow_run",
        "main",
        "after a manual",
    ),
}


def e4_step_seconds(
    steps: list[dict], run_ids: set[str], starts: list[str]
) -> list[float]:
    """Per run, the seconds of the steps whose names start with `starts`, added up."""
    per_run: dict[str, float] = {}
    for step in steps:
        if step["run_id"] in run_ids and any(
            step["step"].startswith(s) for s in starts
        ):
            per_run[step["run_id"]] = per_run.get(step["run_id"], 0.0) + float(
                step["log_s"]
            )
    return list(per_run.values())


def e4_expected(data: Path) -> list[Expected]:
    """Every number of E4's two Results tables."""
    runs = read(data, "e4_runs.csv")
    steps = read(data, "e4_steps.csv")
    expected: list[Expected] = []
    good = {
        cache: [
            r for r in runs if r["cache"] == cache and not r["error"] and r["job_s"]
        ]
        for cache in ["on", "off"]
    }
    columns = {cache: f"Cache {cache} ({len(good[cache])} runs)" for cache in good}
    name, table = "E4 cache table", ("",)
    # The download row is named after how many libraries uv downloaded. With
    # the cache on nothing is downloaded, so its cell sits in the row named
    # after the cache-off runs' count (whatever that count now is).
    downloaded = {
        r["prepared_packages"]
        for cache in good
        for r in good[cache]
        if r["prepared_packages"]
    }
    download_row = (
        "… of which downloading "
        f"{downloaded.pop() if len(downloaded) == 1 else '?'} libraries "
        '(uv\'s "Prepared")'
    )
    for cache, column in columns.items():
        ids = {r["run_id"] for r in good[cache]}
        expected.append(
            Cell(
                name,
                table,
                ("Job time",),
                column,
                spread(values(good[cache], "job_s"), whole, " s"),
            )
        )
        for label, (starts, number) in E4_STEP_ROWS.items():
            times = e4_step_seconds(steps, ids, starts)
            value = (
                f"{number(median(times))} s"
                if (label, cache) in E4_MEDIAN_ONLY
                else spread(times, number, " s")
            )
            expected.append(Cell(name, table, (label,), column, value))
        prepared = values(good[cache], "prepared_s")
        if prepared:
            expected.append(
                Cell(name, table, (download_row,), column, spread(prepared, two, " s"))
            )
        else:
            # Nothing downloaded with the cache on: the doc says so in that cell.
            expected.append(
                Cell(
                    name,
                    table,
                    (download_row,),
                    column,
                    "— (nothing downloaded)",
                )
            )
        expected.append(
            Cell(
                name,
                table,
                ("Waiting for a machine",),
                column,
                f"{whole(median(values(good[cache], 'queue_s')))} s",
            )
        )

    secrets = read(data, "e4_secrets.csv")
    name, table = "E4 secrets table", ("Kind of run",)
    for label, (workflow, event, branch, note) in E4_SECRET_ROWS.items():
        group = [
            r
            for r in secrets
            if (r["workflow"], r["event"], r["branch"]) == (workflow, event, branch)
            and r["note"].startswith(note)
            and (note or not r["note"])
        ]
        present = {r["secret_present"] for r in group}
        if present == {"yes"}:
            shown = "yes in all"
        elif present == {"step not run"}:
            shown = f"step never ran: publish job skipped in all {len(group)}"
        else:
            shown = "mixed: " + ", ".join(sorted(present))
        expected += [
            Cell(name, table, (label,), "Runs", str(len(group))),
            Cell(name, table, (label,), "Secret present", shown),
        ]
    return expected


# ---- The Turkish vs English cost --------------------------------------------

COST_TOKENIZERS = {"o200k": "o200k_base (Kuyruk)", "gemma": "Gemma (from Ollama)"}
COST_SHORT_NAMES = {"o200k": "o200k_base", "gemma": "Gemma"}


def cost_expected(data: Path) -> list[Expected]:
    """Every number of the cost section's three Results tables."""
    rows = cost_sums.read_rows(data / "cost_tokens.csv")
    expected: list[Expected] = []
    name = "Cost tokens table"
    table = ("Tokenizer", "Group")
    for what, label in COST_TOKENIZERS.items():
        for group in [*cost_sums.GROUPS, None]:
            part = cost_sums.in_group(rows, group)
            ratio = cost_sums.sum_ratio(part, what)
            row = (label, group or "all")
            expected += [
                Cell(name, table, row, "Reviews", str(len(part))),
                Cell(
                    name,
                    table,
                    row,
                    "Turkish tokens",
                    whole(sum(r[f"{what}_tr"] for r in part)),
                ),
                Cell(
                    name,
                    table,
                    row,
                    "English tokens",
                    whole(sum(r[f"{what}_en"] for r in part)),
                ),
                Cell(
                    name,
                    table,
                    row,
                    "All TR ÷ all EN",
                    f"{ratio:.2f} ({cost_sums.percent_more(ratio)})",
                ),
                Cell(
                    name,
                    table,
                    row,
                    "Median per review",
                    f"{cost_sums.median_ratio(part, what):.2f}",
                ),
            ]
    name, table = "Cost words table", ("", "Turkish", "English")
    for language, column in [("tr", "Turkish"), ("en", "English")]:
        expected.append(
            Cell(
                name,
                table,
                ("Words",),
                column,
                whole(sum(r[f"words_{language}"] for r in rows)),
            )
        )
        for what, short in COST_SHORT_NAMES.items():
            expected.append(
                Cell(
                    name,
                    table,
                    (f"{short} tokens per word",),
                    column,
                    f"{cost_sums.tokens_per_word(rows, what, language):.2f}",
                )
            )
    name, table = "Cost review-by-review table", ("Tokenizer", "Turkish more")
    for what, short in COST_SHORT_NAMES.items():
        ratios = cost_sums.ratios(rows, what)
        more = sum(1 for r in rows if r[f"{what}_tr"] > r[f"{what}_en"])
        same = sum(1 for r in rows if r[f"{what}_tr"] == r[f"{what}_en"])
        fewer = sum(1 for r in rows if r[f"{what}_tr"] < r[f"{what}_en"])
        row = (short,)
        expected += [
            Cell(name, table, row, "Turkish more", str(more)),
            Cell(name, table, row, "Same", str(same)),
            Cell(name, table, row, "Turkish fewer", str(fewer)),
            Cell(name, table, row, "Smallest ratio", f"{min(ratios):.2f}"),
            Cell(name, table, row, "Largest ratio", f"{max(ratios):.2f}"),
        ]
    return expected


# ---- Putting it together ----------------------------------------------------

# Section key in the doc → the function that builds its expected numbers.
CHECKS: dict[str, Callable[[Path], list[Expected]]] = {
    "E1": e1_expected,
    "E2": e2_expected,
    "E3": e3_expected,
    "E4": e4_expected,
    "Extra": cost_expected,
}


# ---- The web interface's story numbers (D120) --------------------------------

# Digits the pages may contain although they are not measurements: names and
# labels. Everything else with a digit must be a {slot}.
ALLOWED_DIGITS = [
    r"Experiment \d",  # "Experiment 1 · The freeze"
    r"Deney \d",
    r"Gemma 4 E4B",  # the model's name
]

# Measured facts the pages say in words, with what must hold in the data.
WORD_CLAIMS: list[tuple[str, Callable[[dict, Path], bool]]] = [
    (
        'E4 "Downloading took under half a second"',
        lambda found, data: found["e4_download_s"] < 0.5,
    ),
    (
        'E4 "half with the cache and half without"',
        lambda found, data: (
            sum(
                r["cache"] == "on" and not r["error"] and r["job_s"] != ""
                for r in read(data, "e4_runs.csv")
            )
            * 2
            == found["e4_runs"]
        ),
    ),
    ('E3 "Built five versions"', lambda found, data: found["e3_versions"] == 5),
    (
        'E3 "on two different machines"',
        lambda found, data: (
            {r["environment"] for r in read(data, "e3_builds.csv")}
            | {r["environment"] for r in read(data, "e3_builds_ci.csv")}
            == {"mac", "ci"}
        ),
    ),
    (
        'E1 "Wrote the same step three ways"',
        lambda found, data: (
            {r["mode"] for r in read(data, "e1_runs.csv") if r["kind"] == "sleep"}
            == {"A", "B", "C"}
        ),
    ),
    (
        'E2 "Never finished, the usual way"',
        lambda found, data: found["e2_broken_never_finished"] is True,
    ),
]


# Attributes whose values people see or hear (a tooltip, a screen reader's
# label, a box's hint): a number there would go stale like visible text.
READ_ATTRIBUTES = {"alt", "title", "aria-label", "placeholder", "value", "label"}


class PageText(html.parser.HTMLParser):
    """Collect what a person reads or hears on an HTML page.

    That is the visible text plus the attributes in READ_ATTRIBUTES; tags,
    other attributes (sizes, addresses) and code are left out.
    """

    def __init__(self) -> None:
        """Start with nothing collected and outside any script or style."""
        super().__init__()
        self.parts: list[str] = []
        self.skipping = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        """Keep read attributes; note entering a script or style block."""
        self.parts += [v for k, v in attrs if k in READ_ATTRIBUTES and v]
        if tag in ("script", "style"):
            self.skipping += 1

    def handle_endtag(self, tag: str) -> None:
        """Note when leaving a script or style block."""
        if tag in ("script", "style"):
            self.skipping -= 1

    def handle_data(self, data: str) -> None:
        """Keep text outside script and style blocks."""
        if not self.skipping:
            self.parts.append(data)


def strings_in(value) -> list[str]:
    """Every text in a language file (the values, not the key names)."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [text for item in value.values() for text in strings_in(item)]
    return []


def typed_numbers(text: str) -> list[str]:
    """The digits in `text` that are neither in a {slot} nor allowed."""
    text = re.sub(r"\{\w+\}", "", text)
    for allowed in ALLOWED_DIGITS:
        text = re.sub(allowed, "", text)
    return re.findall(r"\S*\d\S*", text)


def check_interface(data: Path = DATA, web: Path = WEB) -> tuple[int, list[str]]:
    """Check the interface's numbers: (how many checks, what differs)."""
    problems: list[str] = []
    checked = 0
    fresh = web_data.findings(data)
    try:
        saved = json.loads((web / "data" / "findings.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        saved = {}
    for key in sorted(set(fresh) | set(saved)):
        checked += 1
        if saved.get(key) != fresh.get(key):
            problems.append(
                f"Interface: findings.json says {key} = {saved.get(key)}, "
                f"the data gives {fresh.get(key)} (run bench.web_data)"
            )
    checked += 1
    try:
        samples = json.loads(
            (web / "data" / "sample_reviews.json").read_text(encoding="utf-8")
        )
    # A missing or broken file is a difference to report, not a crash (D114).
    except (OSError, ValueError):
        samples = None
    if samples != web_data.sample_reviews(data):
        problems.append("Interface: sample_reviews.json doesn't match reviews_tr.csv")
    for page in sorted(web.glob("*.html")):
        parser = PageText()
        parser.feed(page.read_text(encoding="utf-8"))
        checked += 1
        for number in typed_numbers(" ".join(parser.parts)):
            problems.append(f"Interface: {page.name} has a typed number: {number}")
    for words in sorted((web / "i18n").glob("*.json")):
        texts = strings_in(json.loads(words.read_text(encoding="utf-8")))
        checked += 1
        for text in texts:
            for number in typed_numbers(text):
                problems.append(
                    f"Interface: i18n/{words.name} has a typed number: {number} "
                    f'(in "{text[:60]}")'
                )
    for claim, holds in WORD_CLAIMS:
        checked += 1
        if not holds(fresh, data):
            problems.append(f"Interface: {claim} is no longer true of the data")
    return checked, problems


# ---- The README and the write-up (Slice 18a, D131) -------------------------

# The two documents a first-time reader sees. A Markdown file can't fill a
# number in from findings.json the way the pages do, so every number in them
# is typed, and right after it sits a hidden label (an HTML comment, which
# GitHub doesn't show) naming where it comes from:
#   252<!--n:e1_bad_s--> seconds    a measured number (bench.web_data)
#   1,000<!--s:max_texts_per_job--> a setting's default (app/settings.py)
STORY_DOCS = [ROOT / "README.md", ROOT / "docs" / "write-up.md"]
SETTINGS_FILE = ROOT / "app" / "settings.py"

LABELLED = re.compile(r"(\d[\d,]*(?:\.\d+)?)<!--([ns]):(\w+)-->")

# Digits a reader sees that are names, not measurements (like ALLOWED_DIGITS
# for the pages). Anything in code formatting, a web address or a link's
# target is skipped before these are applied.
STORY_ALLOWED = [
    r"Gemma 4 E4B",  # the model's name
    r"\bE[1-4]\b",  # the experiments' names
    r"\bD\d{2,3}\b",  # decision-log entries
    r"Python 3\.12",
    r"Postgres 16",
    r"WSL 2",  # the name of Windows' Linux layer
    r"(?m)^#+ \d+\. ",  # a numbered step's heading: "### 1. Install Docker"
    r"(?m)^\s*\d+\. ",  # a numbered list item
    r"(?i)\bsteps? \d\b",  # a reference to a numbered step: "go back to step 3"
]


def setting_defaults(path: Path = SETTINGS_FILE) -> dict:
    """Each setting's default value, read from app/settings.py's text.

    Read with Python's own parser (`ast`) instead of importing the file:
    importing it builds the settings, which needs a JWT_SECRET, and the
    number check must run without one (like bench/cost_sums.py). Only the
    lines directly inside `class Settings` count, so a helper elsewhere in
    the file that reuses a name can't replace a real default.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    classes = [n for n in tree.body if isinstance(n, ast.ClassDef)]
    body = next((c.body for c in classes if c.name == "Settings"), [])
    defaults = {}
    for node in body:
        if not (isinstance(node, ast.AnnAssign) and node.value is not None):
            continue
        if not isinstance(node.target, ast.Name):
            continue
        value = node.value
        # `Field(default=200_000, ge=1)` or `Field(200_000, ge=1)`: the
        # default is the keyword, or else the first value in brackets.
        if isinstance(value, ast.Call):
            keyword = [k.value for k in value.keywords if k.arg == "default"]
            value = (keyword or value.args or [None])[0]
        try:
            defaults[node.target.id] = ast.literal_eval(value)
        # A default that isn't a plain value (e.g. `25 * 1024 * 1024`) is
        # simply not available as a label.
        except (ValueError, TypeError, SyntaxError):
            continue
    return defaults


def as_written(value: object) -> str | None:
    """How a number must be written: thousands with commas, no ".0" (5.0 → 5).

    None for anything that isn't a number (a word, or True/False), which no
    number in a document can stand for.
    """
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    return f"{value:,}"


# A link's target, allowing one level of brackets inside it, as in
# "(docs/decision-log.md#d29--foo(bar))".
LINK_TARGET = r"\]\((?:[^()]|\([^()]*\))*\)"


def unlabelled_numbers(text: str) -> list[str]:
    """Digits a reader would see that carry no label and aren't allowed names."""
    text = re.sub(r"(?s)(```|~~~).*?\1", "", text)  # code boxes, either fence
    text = re.sub(r"`[^`]*`", "", text)  # code formatting
    text = LABELLED.sub("", text)  # labelled numbers
    text = re.sub(r"(?s)<!--.*?-->", "", text)  # other hidden comments
    text = re.sub(LINK_TARGET, "]", text)  # link targets
    # HTML tags (their sizes, addresses). A tag starts with a letter or "/",
    # so a "<" meaning "less than" ("< 5 seconds") is not mistaken for one.
    text = re.sub(r"</?[A-Za-z][^<>]*>", "", text)
    text = re.sub(r"https?://\S+", "", text)  # bare web addresses
    for allowed in STORY_ALLOWED:
        text = re.sub(allowed, "", text)
    return re.findall(r"\S*\d\S*", text)


def label_problem(doc: str, written: str, key: str, source: dict, kind: str) -> str:
    """What is wrong with one labelled number, or "" if it is right."""
    where = "data" if kind == "n" else "settings"
    if key not in source:
        return f"{doc}: {written} is labelled {key}, which the {where} don't have"
    # Present but None: the runs that should agree on it don't (web_data's
    # "only one value" rule), so there is no single number to write.
    if source[key] is None:
        return (
            f"{doc}: {written} is labelled {key}, but the runs disagree on it, "
            "so the data give no single value"
        )
    expected = as_written(source[key])
    if expected is None:
        return f"{doc}: {written} is labelled {key}, which is not a number"
    if written != expected:
        return f"{doc}: says {written} for {key}, the {where} give {expected}"
    return ""


def check_story(
    docs: list[Path] = STORY_DOCS,
    data: Path = DATA,
    settings: Path = SETTINGS_FILE,
    numbers: dict | None = None,
    defaults: dict | None = None,
) -> tuple[int, list[str]]:
    """Check the README and write-up's numbers: (how many checks, what differs).

    `numbers` and `defaults` replace the ones computed from `data` and read
    from `settings`; the tests use them to check the rules on made-up values.
    """
    numbers = web_data.story_numbers(data) if numbers is None else numbers
    defaults = setting_defaults(settings) if defaults is None else defaults
    checked = 0
    problems: list[str] = []
    for doc in docs:
        text = doc.read_text(encoding="utf-8")
        for written, kind, key in LABELLED.findall(text):
            checked += 1
            source = numbers if kind == "n" else defaults
            if problem := label_problem(doc.name, written, key, source, kind):
                problems.append(problem)
        checked += 1
        for number in unlabelled_numbers(text):
            problems.append(f"{doc.name}: number without a label: {number}")
    return checked, problems


def check(
    doc: Path = DOC,
    data: Path = DATA,
    web: Path | None = None,
    story: list[Path] | None = None,
) -> tuple[int, list[str]]:
    """Compare the doc with the data: (how many numbers checked, what differs).

    With `web` (the interface's folder), the interface's numbers are checked
    too (`check_interface`); with `story` (the README and the write-up),
    theirs too (`check_story`).
    """
    found = sections(doc.read_text(encoding="utf-8"))
    checked = 0
    problems: list[str] = []
    for key, build in CHECKS.items():
        if key not in found:
            problems.append(f"{key}: no Results section found in {doc.name}")
            continue
        try:
            expected = build(data)
        # A rerun can leave a group with no usable rows (e.g. every build of
        # one kind failed): then a median, a division or a lookup has nothing
        # to work on. That is a difference to report, not a reason to crash
        # after hours of measuring.
        except (statistics.StatisticsError, ZeroDivisionError, KeyError) as error:
            problems.append(
                f"{key}: the numbers can't be recomputed from the data "
                f"({type(error).__name__}: {error}); some measurements are missing"
            )
            continue
        checked += len(expected)
        problems += compare(found[key], expected)
    if web is not None:
        more, interface_problems = check_interface(data, web)
        checked += more
        problems += interface_problems
    if story is not None:
        more, story_problems = check_story(story, data)
        checked += more
        problems += story_problems
    return checked, problems


WHERE = "docs/experiments.md, the interface, the README and the write-up"


def report() -> int:
    """Check every number and print the result: how many differ (0 = all match).

    Shared by `main` and bench/run_all.py, so both check the same files and
    say the same thing.
    """
    checked, problems = check(web=WEB, story=STORY_DOCS)
    if not problems:
        print(f"All {checked} numbers in {WHERE} match the CSV files.")
        return 0
    print(f"{len(problems)} of {checked} numbers in {WHERE} differ from the data:")
    for problem in problems:
        print(f"- {problem}")
    return len(problems)


def main() -> None:
    """Print whether every number matches; exit code 1 if any differs."""
    if report():
        sys.exit(1)


if __name__ == "__main__":
    main()
