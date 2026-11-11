"""Style and link checks for the two documents first-time readers see.

Slice 18a (D132). README.md and docs/write-up.md are written for someone who
may not be a programmer, in plain punctuation: full stops and commas, ranges
in words ("26 to 34 seconds"), no arrows or other symbols in the text. These
tests keep it that way, and make sure every decision-log link in the README
lands on the entry it names.

An "anchor" is the part of a link after "#": GitHub turns every heading into
one (lowercase, punctuation removed, spaces turned into hyphens), so a link
to `decision-log.md#d29--...` jumps straight to the D29 heading.
"""

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent
README = ROOT / "README.md"
WRITE_UP = ROOT / "docs" / "write-up.md"
DECISION_LOG = ROOT / "docs" / "decision-log.md"

# Characters the plain style never uses, with a name for the error message.
FORBIDDEN = {
    "—": "an em dash",
    "–": "an en dash",
    ";": "a semicolon",
    "→": "an arrow",
    "←": "an arrow",
    "×": "a times sign",
    "≈": "an 'about' sign",
    "…": "an ellipsis character",
    "·": "a middle dot",
}


def outside_diagrams(text: str) -> list[tuple[int, str]]:
    """The document's lines with their numbers, leaving out Mermaid diagrams.

    A Mermaid diagram (a ```mermaid code box, which GitHub draws as a
    picture) is the one place where arrows belong.
    """
    lines = []
    in_diagram = False
    for number, line in enumerate(text.splitlines(), start=1):
        if line.strip().startswith("```mermaid"):
            in_diagram = True
        elif in_diagram and line.strip() == "```":
            in_diagram = False
        elif not in_diagram:
            lines.append((number, line))
    return lines


def style_problems(text: str) -> list[str]:
    """Every forbidden character outside diagrams, as "line N: a semicolon"."""
    return [
        f"line {number}: {name}"
        for number, line in outside_diagrams(text)
        for character, name in FORBIDDEN.items()
        if character in line
    ]


def colon_problems(text: str) -> list[str]:
    """Every colon in the middle of a sentence, as "line N: a colon mid-sentence".

    A colon is fine at the end of a line (before a list or a code box), in
    code, in a web address or a link's target, in a hidden comment, inside
    quotation marks (a label quoted as it appears), and in a diagram.
    Anywhere else, a full stop reads more simply.
    """
    problems = []
    in_code = False
    for number, line in outside_diagrams(text):
        if line.strip().startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            continue
        line = re.sub(r"`[^`]*`", "", line)  # code formatting
        line = re.sub(r"<!--.*?-->", "", line)  # hidden comments
        line = re.sub(r"\]\([^)]*\)", "]", line)  # link targets
        line = re.sub(r"https?://\S+", "", line)  # web addresses
        line = re.sub(r'"[^"]*"', "", line)  # quoted words, e.g. "Demo:"
        if re.search(r":\s*\S", line):
            problems.append(f"line {number}: a colon mid-sentence")
    return problems


def github_anchor(heading: str) -> str:
    """The anchor GitHub makes for a heading.

    Lowercase, every character that isn't a letter, digit, space, hyphen or
    underscore removed, then each space turned into a hyphen. So
    "D23 — Another user's job returns 404, not 403" becomes
    "d23--another-users-job-returns-404-not-403".
    """
    return re.sub(r"[^\w\- ]", "", heading.strip().lower()).replace(" ", "-")


def decision_anchors(log: str) -> dict[str, str]:
    """Each decision's number ("D29") and the anchor of its heading."""
    anchors = {}
    for heading in re.findall(r"(?m)^### (D\d+ .*)$", log):
        anchors[heading.split()[0]] = github_anchor(heading)
    return anchors


def decision_link_problems(readme: str, log: str) -> list[str]:
    """Every README link into the decision log that doesn't land on its entry.

    A link reads `[D29](docs/decision-log.md#d29--...)`: its text must name
    an entry that exists, and its anchor must be exactly that entry's.
    """
    anchors = decision_anchors(log)
    problems = []
    for name, anchor in re.findall(
        r"\[(D\d+)\]\(docs/decision-log\.md#([^)]*)\)", readme
    ):
        if name not in anchors:
            problems.append(f"{name}: no such entry in the decision log")
        elif anchor != anchors[name]:
            problems.append(
                f"{name}: the link goes to #{anchor}, not to #{anchors[name]}"
            )
    return problems


@pytest.mark.parametrize("document", [README, WRITE_UP], ids=lambda p: p.name)
def test_the_document_uses_plain_punctuation(document):
    """No em or en dash, semicolon, arrow or other symbol outside diagrams."""
    assert style_problems(document.read_text(encoding="utf-8")) == []


@pytest.mark.parametrize("document", [README, WRITE_UP], ids=lambda p: p.name)
def test_the_document_has_no_colon_mid_sentence(document):
    """Colons only before a list or code, never in the middle of a sentence."""
    assert colon_problems(document.read_text(encoding="utf-8")) == []


def test_colon_problems_allows_lists_code_and_addresses():
    """End-of-line colons, code, addresses and links pass; a mid-line one fails."""
    text = (
        "Pick one:\n"
        "Run `a: b` and open https://x.org/a:b or [x](http://y:1/).\n"
        "<!-- note: hidden -->\n"
        'It says "Demo: text" in front.\n'
        "```\nkey: value\n```\n"
        "The front desk: the pages.\n"
    )
    assert colon_problems(text) == ["line 8: a colon mid-sentence"]


def test_style_problems_names_the_line_and_the_character():
    """Each kind of forbidden character is found and named, with its line."""
    text = "Fine.\nA — B\nA – B\nA; B\nA → B\nFine."
    assert style_problems(text) == [
        "line 2: an em dash",
        "line 3: an en dash",
        "line 4: a semicolon",
        "line 5: an arrow",
    ]


def test_arrows_are_allowed_only_inside_a_diagram():
    """Lines inside ```mermaid are skipped; the same arrow after it is not."""
    text = "```mermaid\nA → B; C\n```\nA → B"
    assert style_problems(text) == ["line 4: an arrow"]


def test_every_decision_link_in_the_readme_lands_on_its_entry():
    """Each [Dnn](docs/decision-log.md#...) link matches a real heading."""
    readme = README.read_text(encoding="utf-8")
    log = DECISION_LOG.read_text(encoding="utf-8")
    assert re.search(r"\[D\d+\]\(docs/decision-log\.md#", readme)
    assert decision_link_problems(readme, log) == []


def test_a_wrong_or_missing_decision_link_is_reported():
    """A made-up entry and a link to the wrong heading are both caught."""
    log = "### D23 — Another user's job returns 404, not 403\n"
    readme = (
        "[D23](docs/decision-log.md#d23--another-users-job-returns-404-not-403) "
        "[D23](docs/decision-log.md#d23--wrong) "
        "[D99](docs/decision-log.md#d99--anything)"
    )
    assert decision_link_problems(readme, log) == [
        "D23: the link goes to #d23--wrong, not to "
        "#d23--another-users-job-returns-404-not-403",
        "D99: no such entry in the decision log",
    ]


def test_github_anchor_matches_githubs_rule():
    """Punctuation goes, spaces become hyphens, underscores and digits stay."""
    assert (
        github_anchor("D45 — Tokens counted with tiktoken `o200k_base`, its file")
        == "d45--tokens-counted-with-tiktoken-o200k_base-its-file"
    )
