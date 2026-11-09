"""Tests for Slice 15's Turkish vs English cost count (`bench/cost_tr_en.py`).

Three kinds of test:
- the choice of reviews (seeded, 17/17/16, D110) and the translation file
  (exactly the picked reviews; same emoji, emails, phones and line breaks);
- the counting, against a pretend Ollama (never the real one): raw mode, no
  instruction, every text counted twice and a difference stops it (D111);
- the sums shared with the chart and the number check (sum ratio, D112).
"""

import csv
import json
import math
import re
import unicodedata

import httpx
import pytest

from app.normalise import prepare_for_ai
from app.tokens import count_tokens
from bench import cost_tr_en, measure_ollama
from bench.cost_sums import (
    median_ratio,
    percent_more,
    ratios,
    read_rows,
    sum_ratio,
    summary_lines,
    tokens_per_word,
)
from bench.cost_tr_en import (
    CountMismatch,
    Pair,
    count_all,
    count_gemma_twice,
    cross_check,
    gemma_request,
    group_of,
    load_pairs,
    pick,
    read_turkish,
)

# The pretend Ollama's chat wrapper: this many tokens for an empty text.
WRAPPER = 28


class PretendOllama:
    """Answers like Ollama: one "token" per word, plus the chat wrapper.

    `change_after` makes it answer one more token for every text from that
    call on, to imitate a count that isn't stable.
    """

    def __init__(self, change_after: int | None = None) -> None:
        """Start with no calls seen."""
        self.change_after = change_after
        self.requests: list[dict] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        """One request to /api/generate, /api/chat or /api/version."""
        if request.url.path == "/api/version":
            return httpx.Response(200, json={"version": "0.40.15"})
        body = json.loads(request.content)
        self.requests.append(body)
        if request.url.path == "/api/generate":
            count = len(body["prompt"].split())
        else:
            count = WRAPPER + len(body["messages"][1]["content"].split())
        if self.change_after is not None and len(self.requests) > self.change_after:
            count += 1
        return httpx.Response(200, json={"prompt_eval_count": count, "eval_count": 1})

    def client(self) -> httpx.Client:
        """A client whose requests go to this pretend Ollama."""
        return httpx.Client(
            base_url="http://ollama", transport=httpx.MockTransport(self.handle)
        )


# ---- The choice of reviews and the translation file ----------------------------


def test_groups_follow_the_character_limits():
    """Up to 200 short, up to 800 medium, longer is long."""
    assert group_of("a" * 200) == "short"
    assert group_of("a" * 201) == "medium"
    assert group_of("a" * 800) == "medium"
    assert group_of("a" * 801) == "long"


def test_the_seed_picks_17_17_16_from_the_right_groups_every_time():
    """Same reviews on every call, each from its own length group."""
    turkish = read_turkish()
    chosen = pick(turkish)
    assert {g: len(ids) for g, ids in chosen.items()} == cost_tr_en.PICK
    assert chosen == pick(dict(reversed(list(turkish.items()))))
    for group, ids in chosen.items():
        assert all(group_of(turkish[i]) == group for i in ids)


def test_the_translation_file_holds_exactly_the_picked_reviews():
    """50 pairs, matching the seed; groups as picked."""
    pairs = load_pairs()
    assert len(pairs) == 50
    assert {p.id for p in pairs} == {
        i for ids in pick(read_turkish()).values() for i in ids
    }


def emoji(text: str) -> list[str]:
    """The emoji and other symbols in a text, sorted."""
    return sorted(c for c in text if unicodedata.category(c) == "So")


PERSONAL = re.compile(r"[\w.]+@example\.com|(?:\+90 |0)?555 000 \d\d \d\d")


def test_translations_keep_emoji_emails_phones_and_line_breaks():
    """What Kuyruk hides or keeps must be the same in both languages."""
    for pair in load_pairs():
        assert emoji(pair.en) == emoji(pair.tr), pair.id
        assert PERSONAL.findall(pair.en) == PERSONAL.findall(pair.tr), pair.id
        assert pair.en.count("\n") == pair.tr.count("\n"), pair.id


def test_hidden_parts_look_the_same_to_the_ai_in_both_languages():
    """After prepare_for_ai, both versions have the same placeholders."""
    for pair in load_pairs():
        tr, en = prepare_for_ai(pair.tr), prepare_for_ai(pair.en)
        for mark in ("[EMAIL]", "[TELEFON]"):
            assert tr.count(mark) == en.count(mark), pair.id


def write_english(path, rows):
    """A made-up translation file."""
    with path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=["id", "group", "text_en"])
        writer.writeheader()
        writer.writerows(rows)


def real_english_rows():
    """The real translation file's rows."""
    with cost_tr_en.ENGLISH_FILE.open(encoding="utf-8", newline="") as file:
        return list(csv.DictReader(file))


def test_a_translation_file_missing_a_review_is_refused(tmp_path):
    """One row short: stop, rather than count a different sample."""
    path = tmp_path / "en.csv"
    write_english(path, real_english_rows()[1:])
    with pytest.raises(ValueError, match="picked by the seed"):
        load_pairs(english_path=path)


def test_a_translation_file_with_a_wrong_group_is_refused(tmp_path):
    """A review filed under the wrong length group is refused too."""
    rows = real_english_rows()
    rows[0]["group"] = "long" if rows[0]["group"] != "long" else "short"
    path = tmp_path / "en.csv"
    write_english(path, rows)
    with pytest.raises(ValueError):
        load_pairs(english_path=path)


def test_a_translation_file_with_a_review_twice_is_refused(tmp_path):
    """A repeated row would count one review twice."""
    rows = real_english_rows()
    path = tmp_path / "en.csv"
    write_english(path, [*rows, rows[0]])
    with pytest.raises(ValueError):
        load_pairs(english_path=path)


# ---- Counting ---------------------------------------------------------------------


def test_gemma_request_is_raw_with_no_instruction():
    """Only the text is read: raw mode, no chat messages, one token of answer."""
    request = gemma_request("merhaba dünya")
    assert request["raw"] is True
    assert request["prompt"] == "merhaba dünya"
    assert "messages" not in request and "system" not in request
    assert request["options"]["num_predict"] == 1
    assert request["think"] is False and request["stream"] is False


def test_every_text_is_counted_twice_and_the_counts_are_kept():
    """Two passes, the second backwards; equal counts are returned."""
    ollama = PretendOllama()
    texts = ["bir iki", "üç", "dört beş altı"]
    with ollama.client() as client:
        assert count_gemma_twice(client, texts) == [2, 1, 3]
    prompts = [r["prompt"] for r in ollama.requests]
    assert prompts == [*texts, *reversed(texts)]


def test_a_count_that_changes_the_second_time_stops_everything():
    """Not stable means not measuring the text alone: nothing is saved."""
    ollama = PretendOllama(change_after=3)
    with ollama.client() as client, pytest.raises(CountMismatch, match="then"):
        count_gemma_twice(client, ["bir iki", "üç", "dört"])


def test_an_error_from_ollama_stops_the_count():
    """A failed reply is never counted as zero."""
    client = httpx.Client(
        base_url="http://ollama",
        transport=httpx.MockTransport(lambda request: httpx.Response(500)),
    )
    with client, pytest.raises(httpx.HTTPStatusError):
        cost_tr_en.gemma_tokens(client, "metin")


PAIRS = [
    Pair(1, "short", "Çok güzel ürün 👍", "Great product 👍"),
    Pair(
        2, "medium", "Bana ali@example.com adresinden yazın", "Write to ali@example.com"
    ),
]


def test_count_all_counts_what_the_ai_receives():
    """Emails are hidden before counting; o200k is Kuyruk's own count."""
    ollama = PretendOllama()
    with ollama.client() as client:
        rows = count_all(client, PAIRS)
    assert all("@" not in r["prompt"] for r in ollama.requests)
    second = rows[1]
    tr = prepare_for_ai(PAIRS[1].tr)
    assert second["o200k_tr"] == count_tokens(tr)
    assert second["characters_tr"] == len(tr)
    assert second["words_tr"] == len(tr.split())
    assert second["gemma_tr"] == len(tr.split())
    assert [r["id"] for r in rows] == [1, 2]
    assert set(rows[0]) == set(cost_tr_en.COLUMNS)


def test_cross_check_compares_raw_with_the_chat_request():
    """Text in the chat request minus the empty request = the raw count."""
    ollama = PretendOllama()
    with ollama.client() as client:
        rows = count_all(client, PAIRS)
        lines = cross_check(client, PAIRS, rows)
    assert lines[0] == f"Chat request with an empty text: {WRAPPER} tokens."
    assert len(lines) == 1 + 2 * 2  # one per group present, in both languages
    assert all("difference +0" in line for line in lines[1:])


# ---- The sums ---------------------------------------------------------------------

ROWS = [
    {"group": "short", "o200k_tr": 6, "o200k_en": 4, "words_tr": 2, "words_en": 4},
    {"group": "long", "o200k_tr": 30, "o200k_en": 20, "words_tr": 10, "words_en": 16},
    {"group": "long", "o200k_tr": 10, "o200k_en": 10, "words_tr": 4, "words_en": 5},
]


def test_sum_ratio_is_all_turkish_over_all_english():
    """46 ÷ 34, not the average of the reviews' ratios (D112)."""
    assert sum_ratio(ROWS, "o200k") == pytest.approx(46 / 34)


def test_median_ratio_is_the_middle_review():
    """Ratios 1.5, 1.5 and 1.0: the median is 1.5."""
    assert median_ratio(ROWS, "o200k") == 1.5


def test_reviews_with_no_english_tokens_are_left_out_of_ratios():
    """Never divide by zero."""
    rows = [*ROWS, {"group": "short", "o200k_tr": 3, "o200k_en": 0}]
    assert len(ratios(rows, "o200k")) == 3


def test_empty_groups_give_not_a_number():
    """No data is shown as missing, not as zero."""
    assert math.isnan(sum_ratio([], "o200k"))
    assert math.isnan(median_ratio([], "o200k"))
    assert math.isnan(tokens_per_word([], "o200k", "tr"))


def test_tokens_per_word_and_percent():
    """46 tokens over 16 words; 1.237 is +24%."""
    assert tokens_per_word(ROWS, "o200k", "tr") == pytest.approx(46 / 16)
    assert percent_more(1.237) == "+24%"
    assert percent_more(0.9) == "-10%"


def test_saved_counts_match_kuyruks_own_counter():
    """The committed o200k columns are exactly count_tokens of what the AI gets."""
    by_id = {p.id: p for p in load_pairs()}
    rows = read_rows()
    assert len(rows) == 50
    for row in rows:
        pair = by_id[row["id"]]
        assert row["o200k_tr"] == count_tokens(prepare_for_ai(pair.tr))
        assert row["o200k_en"] == count_tokens(prepare_for_ai(pair.en))
        assert row["group"] == pair.group


def test_summary_lines_cover_each_tokenizer_and_group():
    """One line per tokenizer and group, plus tokens per word and words."""
    lines = summary_lines(read_rows())
    assert sum("o200k all (50 reviews)" in line for line in lines) == 1
    assert sum("gemma all (50 reviews)" in line for line in lines) == 1
    assert lines[-1].startswith("Cost words:")


# ---- Running it -------------------------------------------------------------------


def test_check_setup_without_ollama(monkeypatch):
    """No Ollama: one plain message."""

    def no_answer():
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(measure_ollama, "model_digest", no_answer)
    assert cost_tr_en.check_setup() == [
        "Ollama doesn't answer. Open the Ollama app and try again."
    ]


def test_check_setup_with_the_wrong_model(monkeypatch):
    """Another model under the same name is refused."""
    monkeypatch.setattr(measure_ollama, "model_digest", lambda: "other")
    assert "not the pinned one" in cost_tr_en.check_setup()[0]
    monkeypatch.setattr(measure_ollama, "model_digest", lambda: cost_tr_en.MODEL_DIGEST)
    # Neither the charger nor Ollama's parallel setting matters for counting.
    monkeypatch.setattr(measure_ollama, "plugged_in", lambda: False)
    monkeypatch.setattr(measure_ollama, "ollama_parallel_setting", lambda: 1)
    assert cost_tr_en.check_setup() == []


def test_pick_option_prints_the_choice(capsys):
    """`--pick` needs no Ollama and prints one line per group."""
    cost_tr_en.main(["--pick"])
    out = capsys.readouterr().out.splitlines()
    assert [line.split(":")[0] for line in out] == ["short", "medium", "long"]


def test_main_refuses_to_count_when_not_ready(monkeypatch, capsys):
    """A problem stops it before any counting, with exit code 1."""
    monkeypatch.setattr(cost_tr_en, "check_setup", lambda: ["Ollama is off."])
    with pytest.raises(SystemExit) as stopped:
        cost_tr_en.main([])
    assert stopped.value.code == 1
    assert "Ollama is off." in capsys.readouterr().out


@pytest.mark.parametrize(("status", "said"), [("", False), (" M app/main.py", True)])
def test_main_counts_saves_and_adds_setup_notes(
    monkeypatch, tmp_path, capsys, status, said
):
    """A whole run against the pretend Ollama writes the CSV and the notes.

    "Uncommitted work" is decided before the run writes its own CSV file: a
    clean start is recorded as clean even though the run then changes files.
    """
    ollama = PretendOllama()
    seen = {"status": status}

    def pretend_command(*command):
        if command[:2] == ("git", "status"):
            return seen["status"]
        return "abc123"

    monkeypatch.setattr(cost_tr_en, "command_output", pretend_command)
    real_out = cost_tr_en.CsvOut

    def out_that_dirties(*args, **kwargs):
        seen["status"] = " M bench/data/cost_tokens.csv"  # the run's own file
        return real_out(*args, **kwargs)

    monkeypatch.setattr(cost_tr_en, "CsvOut", out_that_dirties)
    real_client = httpx.Client
    monkeypatch.setattr(cost_tr_en, "check_setup", lambda: [])
    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **_: real_client(
            base_url="http://ollama", transport=httpx.MockTransport(ollama.handle)
        ),
    )
    monkeypatch.setattr(
        httpx, "get", lambda *a, **k: httpx.Response(200, json={"version": "0.40.15"})
    )
    monkeypatch.setattr(cost_tr_en, "OUT_FILE", tmp_path / "cost_tokens.csv")
    setup = tmp_path / "setup.md"
    setup.write_text("# Setup\n")
    monkeypatch.setattr(cost_tr_en, "SETUP_FILE", setup)
    cost_tr_en.main([])
    rows = read_rows(tmp_path / "cost_tokens.csv")
    assert len(rows) == 50
    notes = setup.read_text()
    assert notes.startswith("# Setup\n")  # earlier notes are kept
    assert "Turkish vs English token cost (Slice 15)" in notes
    assert "0.40.15" in notes and "difference +0" in notes
    assert "`abc123`" in notes
    assert ("plus uncommitted work" in notes) is said
    assert "every count matched" in capsys.readouterr().out


def test_the_sums_load_nothing_from_kuyruk_itself():
    """Charts and the number check must work without Kuyruk's login secret.

    Kuyruk's settings refuse to load without JWT_SECRET, so the shared sums
    and the number check must never import `app`. Checked in a fresh Python,
    away from the project's .env file, with no JWT_SECRET.
    """
    import os
    import subprocess
    import sys
    from pathlib import Path

    root = Path(__file__).parent.parent
    env = {k: v for k, v in os.environ.items() if k != "JWT_SECRET"}
    env["PYTHONPATH"] = str(root)
    code = (
        "import sys, bench.cost_sums, bench.check_numbers; "
        "assert not [m for m in sys.modules if m == 'app' or m.startswith('app.')]; "
        "print(bench.check_numbers.check()[0])"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=Path(os.environ.get("TMPDIR", "/tmp")),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert int(result.stdout) > 300
