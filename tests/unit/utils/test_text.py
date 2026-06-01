"""Unit tests for text helpers, focused on the H17 safe_format guard."""

from __future__ import annotations

from app.utils.text import html_escape, normalize_phone, safe_format, truncate


def test_normalize_phone_strips_plus_and_spaces() -> None:
    assert normalize_phone("+998 90 123 45 67") == "998901234567"
    assert normalize_phone("998901234567") == "998901234567"


def test_normalize_phone_prepends_country_code_for_local_mobile() -> None:
    assert normalize_phone("901234567") == "998901234567"


def test_normalize_phone_handles_none_and_empty() -> None:
    assert normalize_phone(None) == ""
    assert normalize_phone("") == ""


def test_safe_format_substitutes_known_placeholders() -> None:
    assert safe_format("Сумма: {amount}", {"amount": "150 000"}) == "Сумма: 150 000"


def test_safe_format_leaves_unknown_placeholder_literal() -> None:
    # Admin typo'd {whoops} into the template — must not KeyError.
    out = safe_format("Карта: {card}, {whoops}", {"card": "8600"})
    assert out == "Карта: 8600, {whoops}"


def test_safe_format_falls_back_to_raw_on_malformed_template() -> None:
    # A stray single brace would make str.format raise ValueError.
    out = safe_format("Цена { не закрыта", {"x": "1"})
    assert out == "Цена { не закрыта"


def test_html_escape_handles_none_and_specials() -> None:
    assert html_escape(None) == ""
    assert html_escape("a<b>&c") == "a&lt;b&gt;&amp;c"


def test_truncate_appends_suffix() -> None:
    assert truncate("abcdef", 4) == "abc…"
    assert truncate("ab", 4) == "ab"
