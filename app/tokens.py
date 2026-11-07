"""The `count_tokens` task: how many tokens a text has (spec 2.2, D45).

A *token* is the small chunk of text an AI reads (a common word, or a piece of
a longer one). AI providers charge and limit by tokens, so Kuyruk's daily
budget (Slice 6) uses this same counter.

Kuyruk uses tiktoken, a token counter library, with one fixed *encoding* (the
rulebook that says how text is chopped). tiktoken normally downloads that
rulebook from the internet. Kuyruk instead keeps the file in `app/data/` and
checks its SHA-256 checksum (a fingerprint that changes completely if even one
byte changes) before using it, so counting works offline and can't silently
change. The count approximates, but does not equal, what Gemma's own tokenizer
would count (see limits).
"""

import base64
import hashlib
from dataclasses import dataclass
from functools import cache
from pathlib import Path

import tiktoken

from app.settings import settings

# Folder holding the encoding files that are kept in the repo.
DATA_DIR = Path(__file__).parent / "data"


@dataclass(frozen=True)
class EncodingRecipe:
    """Everything needed to rebuild one tiktoken encoding without the internet.

    The pattern and special tokens are copied from tiktoken 0.14.0
    (tiktoken_ext/openai_public.py); a test checks they still match.
    """

    file_name: str
    sha256: str
    pattern: str
    special_tokens: dict[str, int]


RECIPES = {
    "o200k_base": EncodingRecipe(
        file_name="o200k_base.tiktoken",
        # The checksum tiktoken itself publishes for this file.
        sha256="446a9538cb6c348e3516120d7c08b09f57c36495e2acfffe59a5bf8b0cfb1a2d",
        # How text is first split into words and pieces before chopping.
        pattern="|".join(
            [
                r"""[^\r\n\p{L}\p{N}]?[\p{Lu}\p{Lt}\p{Lm}\p{Lo}\p{M}]*[\p{Ll}\p{Lm}\p{Lo}\p{M}]+(?i:'s|'t|'re|'ve|'m|'ll|'d)?""",
                r"""[^\r\n\p{L}\p{N}]?[\p{Lu}\p{Lt}\p{Lm}\p{Lo}\p{M}]+[\p{Ll}\p{Lm}\p{Lo}\p{M}]*(?i:'s|'t|'re|'ve|'m|'ll|'d)?""",
                r"""\p{N}{1,3}""",
                r""" ?[^\s\p{L}\p{N}]+[\r\n/]*""",
                r"""\s*[\r\n]+""",
                r"""\s+(?!\S)""",
                r"""\s+""",
            ]
        ),
        special_tokens={"<|endoftext|>": 199999, "<|endofprompt|>": 200018},
    ),
}


class EncodingFileError(RuntimeError):
    """The encoding is unknown, or its file is missing or damaged.

    Kuyruk stops with this error rather than count tokens with a wrong rulebook.
    """


def count_tokens(text: str) -> int:
    """Return how many tokens `text` has with the encoding chosen in settings."""
    # encode_ordinary treats text like "<|endoftext|>" as normal text, so a user
    # can't make a text count differently by typing a special marker.
    return len(load_encoding(settings.token_encoding).encode_ordinary(text))


@cache  # Load each encoding only once; later calls reuse it.
def load_encoding(name: str) -> tiktoken.Encoding:
    """Build the named encoding from its file in `app/data/`, never online.

    Raises EncodingFileError if the name is unknown or the file is missing or
    doesn't match its checksum.
    """
    recipe = RECIPES.get(name)
    if recipe is None:
        raise EncodingFileError(f"Unknown token encoding {name!r}.")
    path = DATA_DIR / recipe.file_name
    try:
        contents = path.read_bytes()
    except FileNotFoundError:
        raise EncodingFileError(f"Encoding file {path} is missing.") from None
    if hashlib.sha256(contents).hexdigest() != recipe.sha256:
        raise EncodingFileError(f"Encoding file {path} is damaged (wrong checksum).")
    return tiktoken.Encoding(
        name=name,
        pat_str=recipe.pattern,
        mergeable_ranks=parse_ranks(contents),
        special_tokens=recipe.special_tokens,
    )


def parse_ranks(contents: bytes) -> dict[bytes, int]:
    """Read the encoding file: one token piece and its number per line.

    Each line is "<piece written in base64> <number>" (base64 is a way to write
    any bytes using only plain letters). This is how tiktoken reads the file.
    """
    ranks = {}
    for line in contents.splitlines():
        if line:
            piece, rank = line.split()
            ranks[base64.b64decode(piece)] = int(rank)
    return ranks
