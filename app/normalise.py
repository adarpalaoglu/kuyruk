"""The `normalise` task: Adar's six cleanup rules for Turkish text (spec 2.1, D31).

The worker (Slice 5) calls `normalise` on each text of a `normalise` job. Each
rule is one small function below, and `normalise` calls them in Adar's order.
`prepare_for_ai` uses only the first two rules, for texts sent to the AI.
The order matters. Letters are first stored one way (NFC), so an email
containing a Turkish letter is always recognised; then personal details are
hidden, before any step that could change them (D49).

A "regular expression" (regex) used below is a small pattern that describes
what a piece of text looks like, e.g. "digits, a space, more digits"; Python's
`re` module finds every piece of text matching it.
"""

import re
import unicodedata

# The two placeholders that replace hidden personal details. They stay in
# capitals (D44), so it is obvious that something was hidden there.
EMAIL_PLACEHOLDER = "[EMAIL]"
PHONE_PLACEHOLDER = "[TELEFON]"

# An email: some letters, digits or . _ % + - ; then @; then a domain made of
# at least two dot-separated parts (e.g. site.com.tr). `\w` means any letter or
# digit in any language (so Turkish letters count) or an underscore. A simple
# pattern on purpose: unusual but valid emails may be missed (see limits).
EMAIL_PATTERN = re.compile(r"[\w.%+-]+@[\w-]+(?:\.[\w-]+)+")

# A Turkish phone number: +90 or 0, then 3 + 3 + 2 + 2 digits, with an optional
# space or dash between the groups. Covers +90 532 123 45 67, 05321234567,
# 0532-123-45-67 and 0312 123 45 67. `(?<![\d+])` and `(?!\d)` mean "not
# directly after a digit or +, and not directly before a digit", so a piece of a
# longer number is never hidden. Numbers with brackets, dots, or without the
# leading 0/+90 are not caught (see limits).
PHONE_PATTERN = re.compile(
    r"(?<![\d+])(?:\+90|0)[ -]?\d{3}[ -]?\d{3}[ -]?\d{2}[ -]?\d{2}(?!\d)"
)

# Any run of whitespace: spaces, tabs, line breaks, and other Unicode spaces.
WHITESPACE_PATTERN = re.compile(r"\s+")

# Finds the placeholders, so lowercasing can skip them. The brackets around the
# pattern make `split` keep the placeholders in its result.
PLACEHOLDER_PATTERN = re.compile(
    "(" + re.escape(EMAIL_PLACEHOLDER) + "|" + re.escape(PHONE_PLACEHOLDER) + ")"
)


def normalise(text: str) -> str:
    """Clean one text with Adar's rules, in his order, and return the result.

    Rules 5 (keep Turkish letters) and 6 (keep emoji) need no code of their own:
    no step below removes any letter or emoji. Tests prove that.
    """
    text = unicode_nfc(text)  # rule 1
    text = hide_emails_and_phones(text)  # rule 2
    text = tidy_spaces(text)  # rule 3
    text = turkish_lowercase(text)  # rule 4
    return text


def prepare_for_ai(text: str) -> str:
    """Hide emails and phone numbers before a text is sent to the AI (D50).

    Only these two steps of `normalise` (NFC first, which the email pattern
    needs, D49); case and spacing stay as the user wrote them, so the AI sees
    the text as written, minus personal data. It lives here, not in the
    worker, because two places use it: the worker, before each AI call, and
    the daily budget (app/budget.py), which counts the tokens of exactly what
    the AI will receive (D57).
    """
    return hide_emails_and_phones(unicode_nfc(text))


def hide_emails_and_phones(text: str) -> str:
    """Rule 2: replace emails with [EMAIL] and phone numbers with [TELEFON].

    Personal data (protected by Turkey's KVKK law) should not reach the AI.
    Emails go first, so an email made of digits (05321234567@site.com) becomes
    one [EMAIL] rather than a phone number followed by "@site.com".
    """
    text = EMAIL_PATTERN.sub(EMAIL_PLACEHOLDER, text)
    return PHONE_PATTERN.sub(PHONE_PLACEHOLDER, text)


def unicode_nfc(text: str) -> str:
    """Rule 1: store every letter one way (Unicode "NFC" form).

    The same visible letter can be stored in two ways: "ş" as one character, or
    as "s" plus a separate cedilla mark (common in text pasted from a Mac or a
    PDF). NFC turns the two-part form into the one-character form, so
    identical-looking words really are identical. It runs first because the
    email pattern doesn't recognise a separate mark as part of a letter: without
    it, ali@s + mark + irket.com would not be hidden (D49). NFC never changes
    the placeholders, so hiding still happens before anything that could.
    """
    return unicodedata.normalize("NFC", text)


def tidy_spaces(text: str) -> str:
    """Rule 3: remove spaces at both ends and turn each run of spaces into one.

    Line breaks and tabs count as spaces too, so a text spread over several
    lines becomes one tidy line.
    """
    return WHITESPACE_PATTERN.sub(" ", text).strip()


def turkish_lowercase(text: str) -> str:
    """Rule 4: lowercase the Turkish way, leaving the placeholders in capitals.

    Standard lowercasing turns "I" into "i" and "İ" into "i" plus a stray dot,
    which is wrong for Turkish (ISPARTA must become "ısparta"). So the two
    Turkish capitals are changed by hand first, then the rest is lowercased.
    """
    # `split` gives the text between placeholders at even positions (0, 2, ...)
    # and the placeholders themselves at odd positions; only the even ones are
    # lowercased.
    parts = PLACEHOLDER_PATTERN.split(text)
    for i in range(0, len(parts), 2):
        parts[i] = parts[i].replace("I", "ı").replace("İ", "i").lower()
    return "".join(parts)
