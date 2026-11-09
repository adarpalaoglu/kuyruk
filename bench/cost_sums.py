"""Sums for the Turkish vs English cost count, shared by three scripts.

Slice 15 (spec 9.5). `bench/cost_tr_en.py` makes `bench/data/cost_tokens.csv`
(one row per review: characters, words and tokens, in Turkish and English).
These helpers turn that file into the reported numbers: the sum ratio (all
Turkish tokens ÷ all English tokens, the number judged, D112), the median of
the reviews' own ratios, and tokens per word. The chart (`bench/charts.py`),
the number check (`bench/check_numbers.py`) and the counting script itself
use them, so all three always agree.

On purpose this file loads nothing from Kuyruk itself (`app/`): Kuyruk's
settings refuse to load without a login secret (`JWT_SECRET`), and redrawing
the charts from saved data must work on any fresh copy of the project.
"""

import csv
import statistics
from pathlib import Path

DATA = Path(__file__).parent / "data"
OUT_FILE = DATA / "cost_tokens.csv"

# The three length groups, in the order they are shown.
GROUPS = ["short", "medium", "long"]
# The two tokenizers, as named in the CSV columns.
TOKENIZERS = ["o200k", "gemma"]


def read_rows(path: Path = OUT_FILE) -> list[dict]:
    """`cost_tokens.csv` with its numbers as integers."""
    with path.open(encoding="utf-8", newline="") as file:
        return [
            {k: (v if k == "group" else int(v)) for k, v in row.items()}
            for row in csv.DictReader(file)
        ]


def in_group(rows: list[dict], group: str | None) -> list[dict]:
    """The rows of one length group, or all rows if `group` is None."""
    return [r for r in rows if group is None or r["group"] == group]


def sum_ratio(rows: list[dict], what: str) -> float:
    """All Turkish ÷ all English, e.g. `what="o200k"` (D112: the judged one)."""
    english = sum(r[f"{what}_en"] for r in rows)
    return sum(r[f"{what}_tr"] for r in rows) / english if english else float("nan")


def ratios(rows: list[dict], what: str) -> list[float]:
    """Each review's own Turkish ÷ English (reviews with 0 English are left out)."""
    return [r[f"{what}_tr"] / r[f"{what}_en"] for r in rows if r[f"{what}_en"]]


def median_ratio(rows: list[dict], what: str) -> float:
    """The median of the reviews' own ratios (shown, not judged; D112)."""
    values = ratios(rows, what)
    return statistics.median(values) if values else float("nan")


def tokens_per_word(rows: list[dict], what: str, language: str) -> float:
    """All tokens ÷ all words in one language."""
    total_words = sum(r[f"words_{language}"] for r in rows)
    tokens = sum(r[f"{what}_{language}"] for r in rows)
    return tokens / total_words if total_words else float("nan")


def percent_more(ratio: float) -> str:
    """A ratio as "+37%" (how much more Turkish needs)."""
    return f"{(ratio - 1) * 100:+.0f}%"


def summary_lines(rows: list[dict]) -> list[str]:
    """The key numbers, one line each, per tokenizer and group."""
    lines = []
    for what in TOKENIZERS:
        for group in [*GROUPS, None]:
            part = in_group(rows, group)
            if not part:
                continue
            total = sum_ratio(part, what)
            lines.append(
                f"Cost {what} {group or 'all'} ({len(part)} reviews): "
                f"TR {sum(r[f'{what}_tr'] for r in part)} / "
                f"EN {sum(r[f'{what}_en'] for r in part)} tokens; "
                f"sum ratio {total:.2f} ({percent_more(total)}), "
                f"median per review {median_ratio(part, what):.2f}"
            )
        lines.append(
            f"Cost {what} tokens per word: TR "
            f"{tokens_per_word(rows, what, 'tr'):.2f}, EN "
            f"{tokens_per_word(rows, what, 'en'):.2f}"
        )
    lines.append(
        f"Cost words: TR {sum(r['words_tr'] for r in rows)}, "
        f"EN {sum(r['words_en'] for r in rows)}"
    )
    return lines
