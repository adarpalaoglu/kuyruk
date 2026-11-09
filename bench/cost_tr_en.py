"""Extra experiment: how many more tokens Turkish costs than English (spec 9.5).

Slice 15. A "token" is the small piece of text an AI model reads and is paid
by; a "tokenizer" is the rulebook that cuts text into tokens. Kuyruk's daily
budget counts tokens with `o200k_base` (OpenAI's rulebook, app/tokens.py),
while the real AI, Gemma, has its own. This script counts both, for the same
50 reviews in Turkish and in English.

The 50 reviews: 17 short, 17 medium and 16 long, picked at random inside each
length group of the 100 synthetic reviews in `bench/data/reviews_tr.csv`,
with a fixed seed (D110). Their English versions are in
`bench/data/reviews_tr_en.csv`, translated by Claude (an AI) and checked by
Adar; emails and phone numbers are kept exactly as in the Turkish.

Each text is counted as the AI would receive it: first `prepare_for_ai`
(hides emails and phone numbers, app/normalise.py), then:
- `o200k_base`: Kuyruk's own `count_tokens`;
- Gemma: Ollama has no "count tokens" address, so the text is sent to
  Ollama's `/api/generate` in raw mode (`raw: true`: the text exactly as given,
  with no instruction and no chat wrapper around it), asking for a 1-token
  answer; Ollama reports how many tokens it read (`prompt_eval_count`). Every
  text is counted twice, the second time in the opposite order, and the
  script stops if any count differs (D111).

As a cross-check, for a few reviews it also sends Kuyruk's real chat request
(instruction + text) and the same request with an empty text: the
difference should be close to the raw count. Printed, not saved.

Writes `bench/data/cost_tokens.csv` (one row per review) and adds a dated
section to `bench/data/setup.md`. Takes about 2 minutes. Needs Ollama running
with the pinned model. Run from the project folder:

    uv run python -m bench.cost_tr_en

`--pick` only prints which reviews the seed picks (no Ollama needed).
"""

import argparse
import csv
import random
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import httpx

from app.ai import make_ollama_ai
from app.normalise import prepare_for_ai
from app.settings import settings
from app.tokens import count_tokens
from bench.cost_sums import GROUPS, OUT_FILE, summary_lines
from bench.csv_out import CsvOut
from bench.measure_ollama import MODEL_DIGEST, check_model, command_output

DATA = Path(__file__).parent / "data"
TURKISH_FILE = DATA / "reviews_tr.csv"
ENGLISH_FILE = DATA / "reviews_tr_en.csv"
SETUP_FILE = DATA / "setup.md"

# Length groups of the 100 reviews, in characters (bench/data/README.md).
SHORT_MAX = 200
MEDIUM_MAX = 800
# How many of each group to pick: 50 in all, in proportion to the group sizes
# (34 / 33 / 33), D110.
PICK = {"short": 17, "medium": 17, "long": 16}
SEED = 15

# Reviews used for the chat cross-check: the first of each group.
CROSS_CHECK_PER_GROUP = 1
TIMEOUT_SECONDS = 120.0

COLUMNS = [
    "id",
    "group",
    "characters_tr",
    "characters_en",
    "words_tr",
    "words_en",
    "o200k_tr",
    "o200k_en",
    "gemma_tr",
    "gemma_en",
]


@dataclass
class Pair:
    """One review in both languages, as written (before `prepare_for_ai`)."""

    id: int
    group: str
    tr: str
    en: str


class CountMismatch(RuntimeError):
    """Gemma gave a different count for the same text the second time."""


def group_of(text: str) -> str:
    """The length group of a review: short, medium or long (by characters)."""
    if len(text) <= SHORT_MAX:
        return "short"
    if len(text) <= MEDIUM_MAX:
        return "medium"
    return "long"


def read_turkish(path: Path = TURKISH_FILE) -> dict[int, str]:
    """The 100 Turkish reviews, by id."""
    with path.open(encoding="utf-8", newline="") as file:
        return {int(row["id"]): row["text"] for row in csv.DictReader(file)}


def pick(turkish: dict[int, str]) -> dict[str, list[int]]:
    """The seeded choice of reviews per group (D110), each list sorted by id.

    The ids are sorted before drawing, so the choice depends only on the
    seed and the reviews, not on the order of the file.
    """
    chooser = random.Random(SEED)
    chosen = {}
    for group in GROUPS:
        ids = sorted(i for i, text in turkish.items() if group_of(text) == group)
        chosen[group] = sorted(chooser.sample(ids, PICK[group]))
    return chosen


def load_pairs(
    turkish_path: Path = TURKISH_FILE, english_path: Path = ENGLISH_FILE
) -> list[Pair]:
    """The 50 reviews in both languages, checked against the seeded choice.

    Stops with a clear message if the English file doesn't hold exactly the
    picked reviews with their right groups, so a wrong or edited file can't
    be counted by mistake.
    """
    turkish = read_turkish(turkish_path)
    with english_path.open(encoding="utf-8", newline="") as file:
        rows = list(csv.DictReader(file))
    found = {int(row["id"]): row["group"] for row in rows}
    expected = {i: group for group, ids in pick(turkish).items() for i in ids}
    if found != expected or len(rows) != len(expected):
        raise ValueError(
            f"{english_path.name} does not hold exactly the {len(expected)} "
            "reviews picked by the seed (D110)."
        )
    return [
        Pair(int(row["id"]), row["group"], turkish[int(row["id"])], row["text_en"])
        for row in rows
    ]


def gemma_request(text: str) -> dict:
    """Ollama's raw-mode request that reads `text` and nothing else (D111).

    `raw: true` sends the text exactly as given: no instruction, no chat
    wrapper. One token of answer is the least Ollama allows; the count we use
    is of what it read (`prompt_eval_count`), not of what it wrote.
    """
    return {
        "model": settings.ollama_model,
        "prompt": text,
        "raw": True,
        "stream": False,
        "think": False,
        "options": {"num_predict": 1, "temperature": 0},
    }


def gemma_tokens(client: httpx.Client, text: str) -> int:
    """How many Gemma tokens `text` is, as Ollama counts it."""
    reply = client.post("/api/generate", json=gemma_request(text))
    reply.raise_for_status()
    return int(reply.json()["prompt_eval_count"])


def chat_tokens(client: httpx.Client, text: str) -> int:
    """Tokens Gemma reads for Kuyruk's real chat request (instruction + text)."""
    request = make_ollama_ai().chat_request(text)
    request["options"]["num_predict"] = 1  # only the reading is counted
    reply = client.post("/api/chat", json=request)
    reply.raise_for_status()
    return int(reply.json()["prompt_eval_count"])


def words(text: str) -> int:
    """Words in a text: pieces separated by spaces or line breaks."""
    return len(text.split())


def count_gemma_twice(client: httpx.Client, texts: list[str]) -> list[int]:
    """Count every text twice (second pass backwards); stop on any difference.

    A different count would mean the method isn't measuring the text alone
    (e.g. something remembered from an earlier text), so nothing is saved.
    """
    first = [gemma_tokens(client, text) for text in texts]
    second = [gemma_tokens(client, text) for text in reversed(texts)][::-1]
    for text, a, b in zip(texts, first, second, strict=True):
        if a != b:
            raise CountMismatch(
                f"Gemma counted {a}, then {b} tokens for: {text[:60]!r}"
            )
    return first


def count_all(client: httpx.Client, pairs: list[Pair]) -> list[dict]:
    """One CSV row per review: characters, words and both token counts."""
    texts = [prepare_for_ai(t) for p in pairs for t in (p.tr, p.en)]
    gemma = count_gemma_twice(client, texts)
    rows = []
    for index, pair in enumerate(pairs):
        tr, en = texts[2 * index], texts[2 * index + 1]
        rows.append(
            {
                "id": pair.id,
                "group": pair.group,
                "characters_tr": len(tr),
                "characters_en": len(en),
                "words_tr": words(tr),
                "words_en": words(en),
                "o200k_tr": count_tokens(tr),
                "o200k_en": count_tokens(en),
                "gemma_tr": gemma[2 * index],
                "gemma_en": gemma[2 * index + 1],
            }
        )
    return rows


def cross_check(client: httpx.Client, pairs: list[Pair], rows: list[dict]) -> list[str]:
    """Compare raw counts with (chat request with text) − (same, empty text).

    The chat request adds Kuyruk's instruction and Gemma's chat wrapper; the
    difference is what the review itself adds there. It should be within a
    couple of tokens of the raw count (the wrapper may join the text's edges
    differently). Returns lines to print.
    """
    empty = chat_tokens(client, "")
    by_id = {row["id"]: row for row in rows}
    lines = [f"Chat request with an empty text: {empty} tokens."]
    for group in GROUPS:
        for pair in [p for p in pairs if p.group == group][:CROSS_CHECK_PER_GROUP]:
            for language, text in (("tr", pair.tr), ("en", pair.en)):
                in_chat = chat_tokens(client, prepare_for_ai(text)) - empty
                raw = by_id[pair.id][f"gemma_{language}"]
                lines.append(
                    f"Review {pair.id} ({group}, {language}): raw {raw}, "
                    f"in the chat request {in_chat} (difference {in_chat - raw:+d})."
                )
    return lines


# --- Running it for real.


def check_setup() -> list[str]:
    """Ollama answers and has the pinned model; returns what's wrong.

    The same check as the AI measurements (`measure_ollama.check_model`).
    Counting needs neither the charger nor Ollama's parallel setting.
    """
    return check_model()


def add_setup_notes(
    cross_lines: list[str], uncommitted: bool, path: Path = SETUP_FILE
) -> None:
    """Add a dated section about this count to `setup.md` (never erases).

    `uncommitted` must be read before the run writes anything, or the run's
    own new CSV file would always count as uncommitted work.
    """
    version = httpx.get(f"{settings.ollama_url}/api/version", timeout=10).json()
    commit = command_output("git", "rev-parse", "HEAD")
    dirty = " plus uncommitted work" if uncommitted else ""
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    lines = [
        "",
        "## Turkish vs English token cost (Slice 15)",
        "",
        "Written automatically by `bench/cost_tr_en.py`.",
        "",
        "| | |",
        "|---|---|",
        f"| Date and time | {now} |",
        f"| Kuyruk commit | `{commit}`{dirty} |",
        f"| Ollama version, model | {version.get('version', '?')}, "
        f"{settings.ollama_model} (digest `{MODEL_DIGEST}`) |",
        "| Gemma count | `/api/generate`, raw mode, 1 token of answer; every "
        "text counted twice, all equal (D111) |",
        "| o200k count | Kuyruk's `count_tokens` (`o200k_base`) |",
        "| Reviews | 50 (17 short, 17 medium, 16 long; seed 15, D110) |",
        "",
        "Cross-check (raw count vs the same text inside Kuyruk's chat request):",
        "",
        *[f"- {line}" for line in cross_lines],
        "",
    ]
    with path.open("a", encoding="utf-8") as file:
        file.write("\n".join(lines))


def parse_arguments(arguments: list[str] | None = None) -> argparse.Namespace:
    """Read the command-line options."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--pick", action="store_true", help="only print the seeded choice of reviews"
    )
    return parser.parse_args(arguments)


def main(arguments: list[str] | None = None) -> None:
    """Count, save the CSV and the setup notes, print the key numbers."""
    options = parse_arguments(arguments)
    if options.pick:
        for group, ids in pick(read_turkish()).items():
            print(f"{group}: {', '.join(map(str, ids))}")
        return
    problems = check_setup()
    if problems:
        print("Not ready to count:")
        for problem in problems:
            print(f"- {problem}")
        sys.exit(1)
    # Read before this run writes its own files (see add_setup_notes).
    uncommitted = bool(command_output("git", "status", "--porcelain"))
    pairs = load_pairs()
    with httpx.Client(base_url=settings.ollama_url, timeout=TIMEOUT_SECONDS) as client:
        rows = count_all(client, pairs)
        cross_lines = cross_check(client, pairs, rows)
    out = CsvOut(OUT_FILE, COLUMNS)
    for row in rows:
        out.write(row)
    out.close()
    add_setup_notes(cross_lines, uncommitted, SETUP_FILE)
    print(f"Counted {len(rows)} reviews twice with Gemma: every count matched.")
    for line in [*cross_lines, *summary_lines(rows)]:
        print(line)
    print(f"Saved {OUT_FILE}.")


if __name__ == "__main__":
    main()
