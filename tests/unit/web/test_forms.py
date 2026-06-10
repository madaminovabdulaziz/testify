"""Unit tests for the editor form parser."""

from __future__ import annotations

from app.web.forms import parse_test_form


def _card(pos: int, **overrides: str) -> dict[str, str]:
    base = {
        f"q{pos}_text": f"Вопрос {pos}",
        f"q{pos}_a": "А",
        f"q{pos}_b": "Б",
        f"q{pos}_c": "В",
        f"q{pos}_d": "Г",
        f"q{pos}_correct": "A",
    }
    base.update({f"q{pos}_{k}": v for k, v in overrides.items()})
    return base


def _full_form(title: str = "Тест") -> dict[str, str]:
    data: dict[str, str] = {"title": title}
    for pos in range(1, 51):
        data.update(_card(pos))
    return data


def test_full_form_parses_50_drafts_with_derived_sections() -> None:
    parsed = parse_test_form(_full_form())

    assert not parsed.has_errors
    assert len(parsed.drafts) == 50
    by_pos = {d.position: d for d in parsed.drafts}
    assert by_pos[1].section == "rus_tili"
    assert by_pos[36].section == "pedagogik"
    assert by_pos[46].section == "kasbiy"
    assert by_pos[10].correct_option == "A"


def test_empty_cards_are_skipped() -> None:
    data = {"title": "Тест", **_card(1)}
    parsed = parse_test_form(data)

    assert not parsed.has_errors
    assert [d.position for d in parsed.drafts] == [1]


def test_partial_card_gets_required_field_errors() -> None:
    data = {"title": "Тест", f"q{7}_text": "Только текст"}
    parsed = parse_test_form(data)

    assert parsed.drafts == []
    errors = parsed.field_errors[7]
    assert errors["option_a"] == "Заполните поле."
    assert errors["correct_option"] == "Выберите правильный ответ."


def test_missing_radio_is_an_error() -> None:
    data = {"title": "Тест", **_card(3)}
    del data["q3_correct"]
    parsed = parse_test_form(data)

    assert parsed.field_errors[3]["correct_option"] == "Выберите правильный ответ."


def test_semantic_validation_applies_per_card() -> None:
    data = {"title": "Тест", **_card(2, a="x" * 301)}
    parsed = parse_test_form(data)

    assert "длиннее 300" in parsed.field_errors[2]["option_a"]


def test_caption_budget_checked_when_has_image() -> None:
    data = {"title": "Тест", **_card(4, text="x" * 840), "q4_has_image": "1"}
    parsed = parse_test_form(data)

    assert "" in parsed.field_errors[4]  # question-level error


def test_values_are_stripped() -> None:
    data = {"title": "  Тест  ", **_card(1, text="  Вопрос  ")}
    parsed = parse_test_form(data)

    assert parsed.title == "Тест"
    assert parsed.drafts[0].question_text == "Вопрос"


def test_title_required_and_capped() -> None:
    assert "Укажите название теста." in parse_test_form({"title": "  "}).form_errors
    assert any("200" in e for e in parse_test_form({"title": "x" * 201}).form_errors)


def test_has_image_flag_carried_into_draft() -> None:
    data = {"title": "Тест", **_card(1), "q1_has_image": "1"}
    parsed = parse_test_form(data)
    assert parsed.drafts[0].has_image is True


def test_has_image_alone_does_not_make_card_partial() -> None:
    data = {"title": "Тест", "q9_has_image": "1"}
    parsed = parse_test_form(data)
    assert 9 not in parsed.field_errors
    assert parsed.drafts == []


def test_raw_echo_contains_submitted_values() -> None:
    data = {"title": "Тест", **_card(1, a="x" * 301)}
    parsed = parse_test_form(data)
    assert parsed.raw[1]["a"] == "x" * 301
