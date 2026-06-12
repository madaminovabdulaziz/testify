"""Unit tests for the **bold** / __italic__ question markup."""

from __future__ import annotations

from app.utils.markup import (
    MSG_EMPTY_MARKUP,
    MSG_UNBALANCED_BOLD,
    MSG_UNBALANCED_ITALIC,
    render_markup,
    validate_markup,
    visible_length,
)

# ---------- render ----------


def test_render_bold_and_italic() -> None:
    assert render_markup("Какой **глагол** относится к __первому__ спряжению?") == (
        "Какой <b>глагол</b> относится к <i>первому</i> спряжению?"
    )


def test_render_multiple_pairs() -> None:
    assert render_markup("**а** и **б**") == "<b>а</b> и <b>б</b>"


def test_render_nested_bold_italic() -> None:
    assert render_markup("**__важно__**") == "<b><i>важно</i></b>"


def test_render_escapes_html_first() -> None:
    # Author-typed tags must never become live markup.
    assert render_markup("**<b>x</b>**") == "<b>&lt;b&gt;x&lt;/b&gt;</b>"
    assert render_markup("a < b & c") == "a &lt; b &amp; c"


def test_render_unpaired_markers_stay_verbatim() -> None:
    assert render_markup("цена **100") == "цена **100"
    assert render_markup("a __ b") == "a __ b"


def test_render_plain_text_untouched() -> None:
    assert render_markup("обычный текст") == "обычный текст"
    assert render_markup(None) == ""


# ---------- validate ----------


def test_validate_clean_text() -> None:
    assert validate_markup("Какой **глагол** и __падеж__?") == []
    assert validate_markup("без выделений") == []


def test_validate_unbalanced_bold() -> None:
    assert validate_markup("цена **100") == [MSG_UNBALANCED_BOLD]


def test_validate_unbalanced_italic() -> None:
    assert validate_markup("слово __курсив") == [MSG_UNBALANCED_ITALIC]


def test_validate_empty_pair() -> None:
    assert MSG_EMPTY_MARKUP in validate_markup("до ** ** после")
    assert MSG_EMPTY_MARKUP in validate_markup("до __ __ после")


def test_validate_adjacent_pairs_not_false_positive() -> None:
    # «**а** **б**» contains "** **" as a substring but is perfectly valid.
    assert validate_markup("**а** **б**") == []


# ---------- visible_length ----------


def test_visible_length_strips_paired_markers() -> None:
    assert visible_length("**жирный**") == len("жирный")
    assert visible_length("__и__ **ж**") == len("и ж")


def test_visible_length_counts_unpaired_markers() -> None:
    assert visible_length("цена **100") == len("цена **100")
