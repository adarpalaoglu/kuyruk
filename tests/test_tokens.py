"""Tests for `app/tokens.py`: token counts, offline loading, and the checksum.

The exact counts below are fixed for ever by the encoding file kept in the repo;
if one changes, the file or tiktoken has changed. `load_encoding.cache_clear()`
forgets the already-loaded encoding, so a test really loads it from the file.
"""

import socket

import pytest
import tiktoken_ext.openai_public

from app import tokens
from app.tokens import EncodingFileError, count_tokens, load_encoding


@pytest.fixture(autouse=True)
def fresh_encoding_cache():
    """Start and end every test here with no encoding loaded yet."""
    load_encoding.cache_clear()
    yield
    load_encoding.cache_clear()


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Kargo çok hızlı geldi, ürün harika.", 10),
        ("Kitaplıklarımızdan aldığımız kitaplar güzeldi.", 12),
        ("The delivery was very fast, the product is great.", 11),
        ("The books we got from our bookshelves were nice.", 11),
        ("", 0),
    ],
)
def test_turkish_and_english_counts(text, expected):
    assert count_tokens(text) == expected


def test_special_marker_typed_by_a_user_counts_as_normal_text():
    # Not the single special token 199999, and no error.
    assert count_tokens("<|endoftext|>") == 7


def test_counting_works_with_the_network_blocked(monkeypatch):
    def no_network(*args, **kwargs):
        raise AssertionError("count_tokens tried to use the network")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(socket, "create_connection", no_network)

    assert count_tokens("Ağır kargo ama sağlam geldi.") > 0


def test_recipe_matches_tiktokens_own_definition(monkeypatch):
    # Ask tiktoken for its own o200k_base recipe, with the download replaced by
    # nothing, and check our copied pattern and special tokens are the same.
    monkeypatch.setattr(
        tiktoken_ext.openai_public, "load_tiktoken_bpe", lambda *a, **k: {}
    )
    theirs = tiktoken_ext.openai_public.o200k_base()
    ours = tokens.RECIPES["o200k_base"]

    assert ours.pattern == theirs["pat_str"]
    assert ours.special_tokens == theirs["special_tokens"]


def test_damaged_file_is_refused(monkeypatch, tmp_path):
    (tmp_path / "o200k_base.tiktoken").write_bytes(b"aGVsbG8= 0\n")
    monkeypatch.setattr(tokens, "DATA_DIR", tmp_path)

    with pytest.raises(EncodingFileError, match="wrong checksum"):
        count_tokens("merhaba")


def test_missing_file_is_refused(monkeypatch, tmp_path):
    monkeypatch.setattr(tokens, "DATA_DIR", tmp_path)

    with pytest.raises(EncodingFileError, match="missing"):
        count_tokens("merhaba")


def test_unknown_encoding_is_refused(monkeypatch):
    monkeypatch.setattr(tokens.settings, "token_encoding", "cl100k_base")

    with pytest.raises(EncodingFileError, match="Unknown token encoding"):
        count_tokens("merhaba")


def test_encoding_is_loaded_only_once():
    assert load_encoding("o200k_base") is load_encoding("o200k_base")
