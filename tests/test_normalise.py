"""Tests for `app/normalise.py`: each of Adar's six cleanup rules, and their order.

Each rule is tested on its own first, then through `normalise`, the full chain.
The order is Adar's (D49): 1 NFC, 2 hide emails and phones, 3 tidy spaces,
4 Turkish lowercase, 5 keep Turkish letters, 6 keep emoji.
`pytest.mark.parametrize` runs one test once per example in its list.
"""

import unicodedata

import pytest

from app.normalise import (
    hide_emails_and_phones,
    normalise,
    tidy_spaces,
    turkish_lowercase,
    unicode_nfc,
)

# --- Rule 2: hide emails and phone numbers ---------------------------------


@pytest.mark.parametrize(
    "email",
    ["ayse@example.com", "Ayse.Yilmaz+kargo@site.com.tr", "şükrü_öz@örnek.com"],
)
def test_email_is_hidden(email):
    assert hide_emails_and_phones(f"Yaz: {email} lütfen") == "Yaz: [EMAIL] lütfen"


def test_full_stop_after_an_email_is_kept():
    assert hide_emails_and_phones("Adres ayse@site.com.") == "Adres [EMAIL]."


@pytest.mark.parametrize(
    "phone",
    [
        "+90 532 123 45 67",
        "+90-532-123-45-67",
        "+905321234567",
        "0532 123 45 67",
        "0532-123-45-67",
        "05321234567",
        "0312 123 45 67",
        "0312-123-45-67",
        "03121234567",
    ],
)
def test_turkish_phone_forms_are_hidden(phone):
    assert hide_emails_and_phones(f"Ara: {phone}, teşekkürler") == (
        "Ara: [TELEFON], teşekkürler"
    )


@pytest.mark.parametrize(
    "not_a_phone",
    [
        "Sipariş no 123456789012345",  # longer number: a piece is never hidden
        "Fiyat 1500 TL, 3 adet",  # short numbers
        "5321234567",  # no leading 0 or +90 (a known limit)
    ],
)
def test_other_numbers_are_left_alone(not_a_phone):
    assert hide_emails_and_phones(not_a_phone) == not_a_phone


def test_several_details_in_one_text_are_all_hidden():
    text = "a@b.com ve 0532 123 45 67, ya da c@d.org ve +905551112233"
    assert hide_emails_and_phones(text) == (
        "[EMAIL] ve [TELEFON], ya da [EMAIL] ve [TELEFON]"
    )


def test_email_made_of_digits_becomes_one_email_not_a_phone():
    # Emails are hidden before phones, so this is not "[TELEFON]@site.com".
    assert hide_emails_and_phones("05321234567@site.com") == "[EMAIL]"


# --- Rule 1: Unicode NFC -----------------------------------------------------


def test_decomposed_letters_become_single_characters():
    # "s" + combining cedilla, "g" + combining breve, "I" + combining dot above.
    decomposed = "ş ğ İ"
    assert unicode_nfc(decomposed) == "ş ğ İ"
    assert len(unicode_nfc(decomposed)) == 5


def test_already_composed_text_is_unchanged():
    assert unicode_nfc("çğıöşü") == "çğıöşü"


# --- Rule 3: tidy spaces -----------------------------------------------------


def test_ends_are_trimmed_and_runs_of_spaces_become_one():
    assert tidy_spaces("  çok   güzel  ") == "çok güzel"


def test_line_breaks_and_tabs_become_one_space():
    assert tidy_spaces("birinci satır\n\n\nikinci\r\n\tüçüncü") == (
        "birinci satır ikinci üçüncü"
    )


# --- Rule 4: Turkish lowercasing ---------------------------------------------


def test_isparta_icin():
    assert turkish_lowercase("ISPARTA İÇİN") == "ısparta için"


def test_every_turkish_capital_is_lowercased_correctly():
    assert turkish_lowercase("ÇĞIİÖŞÜ") == "çğıiöşü"


def test_dotted_capital_i_leaves_no_stray_dot():
    # Python's own lower() would give "i" plus a combining dot here.
    assert turkish_lowercase("İ") == "i"
    assert len(turkish_lowercase("İSTANBUL")) == len("İSTANBUL")


def test_placeholders_stay_in_capitals():
    assert turkish_lowercase("ARA [TELEFON] VEYA YAZ [EMAIL] HEMEN") == (
        "ara [TELEFON] veya yaz [EMAIL] hemen"
    )


def test_text_that_only_looks_like_a_placeholder_is_lowercased():
    assert turkish_lowercase("[Email] [MAIL]") == "[email] [maıl]"


# --- Rules 5 and 6: Turkish letters and emoji are kept -----------------------


def test_turkish_letters_are_kept():
    assert normalise("Çiçek, ağaç, ılık, ölü, şeker, üzüm") == (
        "çiçek, ağaç, ılık, ölü, şeker, üzüm"
    )


def test_kir_and_kir_stay_different():
    # kır (countryside) and kir (dirt) must never become the same word.
    assert normalise("Kır") == "kır"
    assert normalise("Kir") == "kir"


def test_text_typed_without_turkish_letters_stays_as_it_is():
    assert normalise("cok guzel bir urun, tesekkurler") == (
        "cok guzel bir urun, tesekkurler"
    )


@pytest.mark.parametrize(
    "emoji",
    ["🙄", "👍🏽", "👨‍👩‍👧", "❤️", "🇹🇷"],  # simple, skin tone, family, heart, flag
)
def test_emoji_are_kept_exactly(emoji):
    assert normalise(f"Harika {emoji} ürün") == f"harika {emoji} ürün"


# --- The full chain and the order of the rules -------------------------------


def test_full_chain_on_a_messy_review():
    text = "  ÜRÜN   ÇOK İYİ 👍🏽\n\nBilgi: AYSE@SITE.COM  veya  0532 123 45 67  "
    assert normalise(text) == "ürün çok iyi 👍🏽 bilgi: [EMAIL] veya [TELEFON]"


def test_capital_email_is_hidden_before_lowercasing():
    # The email is hidden while still in capitals, so lowercasing never
    # touches it (D31's reason for the order), and [EMAIL] stays capital.
    assert normalise("İLETİŞİM: INFO@ISPARTA.COM") == "iletişim: [EMAIL]"


def test_phone_split_over_a_line_break_is_not_hidden():
    # Rule 2 runs before rule 3 tidies spaces, so a line break inside a number
    # still breaks the pattern. The order is Adar's; this shows its effect.
    assert normalise("0532\n123 45 67") == "0532 123 45 67"


def test_result_is_in_nfc_form():
    result = normalise("ŞEKER")
    assert result == "şeker"
    assert unicodedata.is_normalized("NFC", result)


def test_normalise_is_stable_when_run_twice():
    once = normalise("  ISPARTA İÇİN  a@b.com 👍🏽 ")
    assert normalise(once) == once


@pytest.mark.parametrize(
    "split_email",
    [
        "ays\u0327e@site.com",  # split "ş" before the @
        "ali@s\u0327irket.com",  # split "ş" in the domain
        "o\u0308zgu\u0308r@g\u0306mail.com",  # split "ö", "ü" and "ğ"
    ],
)
def test_email_with_split_turkish_letters_is_fully_hidden(split_email):
    # NFC runs first (D49), so a letter stored as two parts is joined before
    # the email pattern looks for it. Without that, part or all of the email
    # would reach the AI.
    assert normalise(f"Yazın: {split_email} teşekkürler") == (
        "yazın: [EMAIL] teşekkürler"
    )


def test_split_letters_around_hidden_details_are_joined_too():
    # "Ş" (S + mark) and "ö" (o + mark) in the words around an email and phone.
    text = "S\u0327IKAYET: a@b.com, to\u0308r 0532 123 45 67"
    result = normalise(text)

    assert result == "şıkayet: [EMAIL], tör [TELEFON]"
    assert unicodedata.is_normalized("NFC", result)
