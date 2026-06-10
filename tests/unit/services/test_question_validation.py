"""Unit tests for the shared question-validation rules."""

from __future__ import annotations

from types import SimpleNamespace

from app.services.question_validation import (
    CAPTION_BODY_OVERHEAD,
    EXPECTED_ROW_COUNT,
    MAX_IMAGE_CAPTION_BLOCK_LEN,
    MAX_OPTION_TEXT_LEN,
    MAX_QUESTION_TEXT_LEN,
    caption_block_length,
    section_for_position,
    validate_question_fields,
    validate_test_completeness,
)


def _fields(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "section": "rus_tili",
        "position": 1,
        "question_text": "Вопрос?",
        "option_a": "А",
        "option_b": "Б",
        "option_c": "В",
        "option_d": "Г",
        "correct_option": "A",
        "has_image": False,
    }
    base.update(overrides)
    return base


# ---------- section_for_position ----------


def test_section_for_position_maps_all_ranges() -> None:
    assert section_for_position(1) == "rus_tili"
    assert section_for_position(35) == "rus_tili"
    assert section_for_position(36) == "pedagogik"
    assert section_for_position(45) == "pedagogik"
    assert section_for_position(46) == "kasbiy"
    assert section_for_position(50) == "kasbiy"
    assert section_for_position(0) is None
    assert section_for_position(51) is None


# ---------- validate_question_fields ----------


def test_valid_question_has_no_errors() -> None:
    assert validate_question_fields(**_fields()) == []  # type: ignore[arg-type]


def test_invalid_section_flagged() -> None:
    errors = validate_question_fields(**_fields(section="russkiy"))  # type: ignore[arg-type]
    assert [e.field for e in errors] == ["section"]


def test_position_out_of_global_range() -> None:
    errors = validate_question_fields(**_fields(position=51))  # type: ignore[arg-type]
    assert [e.field for e in errors] == ["position"]


def test_section_position_cross_check() -> None:
    errors = validate_question_fields(**_fields(section="pedagogik", position=47))  # type: ignore[arg-type]
    assert [e.field for e in errors] == ["position"]
    assert "36–45" in errors[0].message


def test_question_text_length_boundary() -> None:
    ok = validate_question_fields(**_fields(question_text="x" * MAX_QUESTION_TEXT_LEN))  # type: ignore[arg-type]
    assert ok == []
    over = validate_question_fields(**_fields(question_text="x" * (MAX_QUESTION_TEXT_LEN + 1)))  # type: ignore[arg-type]
    assert [e.field for e in over] == ["question_text"]


def test_option_length_boundary() -> None:
    ok = validate_question_fields(**_fields(option_b="x" * MAX_OPTION_TEXT_LEN))  # type: ignore[arg-type]
    assert ok == []
    over = validate_question_fields(**_fields(option_b="x" * (MAX_OPTION_TEXT_LEN + 1)))  # type: ignore[arg-type]
    assert [e.field for e in over] == ["option_b"]


def test_correct_option_enum() -> None:
    errors = validate_question_fields(**_fields(correct_option="E"))  # type: ignore[arg-type]
    assert [e.field for e in errors] == ["correct_option"]


def test_caption_budget_at_boundary() -> None:
    # Build text+options that land exactly on the cap, then one over.
    body = MAX_IMAGE_CAPTION_BLOCK_LEN - CAPTION_BODY_OVERHEAD - 4  # 4 one-char options
    ok = validate_question_fields(
        **_fields(has_image=True, question_text="x" * body)  # type: ignore[arg-type]
    )
    assert ok == []
    over = validate_question_fields(
        **_fields(has_image=True, question_text="x" * (body + 1))  # type: ignore[arg-type]
    )
    assert len(over) == 1
    assert over[0].field == ""
    assert str(MAX_IMAGE_CAPTION_BLOCK_LEN) in over[0].message


def test_caption_budget_not_checked_for_text_questions() -> None:
    errors = validate_question_fields(
        **_fields(has_image=False, question_text="x" * 900)  # type: ignore[arg-type]
    )
    assert errors == []


def test_multiple_errors_reported_together() -> None:
    errors = validate_question_fields(
        **_fields(section="bad", correct_option="Z", option_a="x" * 301)  # type: ignore[arg-type]
    )
    assert {e.field for e in errors} == {"section", "option_a", "correct_option"}


# ---------- caption_block_length ----------


def test_caption_block_length_strips_and_adds_overhead() -> None:
    assert caption_block_length(" ab ", "c", "d", "e", "f") == 6 + CAPTION_BODY_OVERHEAD


# ---------- validate_test_completeness ----------


def _question_set(count: int = EXPECTED_ROW_COUNT) -> list[SimpleNamespace]:
    out = []
    for pos in range(1, count + 1):
        section = "rus_tili" if pos <= 35 else ("pedagogik" if pos <= 45 else "kasbiy")
        out.append(SimpleNamespace(section=section, position=pos))
    return out


def test_complete_test_passes() -> None:
    assert validate_test_completeness(_question_set()) == []


def test_wrong_total_count_flagged() -> None:
    errors = validate_test_completeness(_question_set(49))
    assert any("Ожидалось 50 вопросов, найдено 49" in e for e in errors)


def test_duplicate_position_flagged() -> None:
    questions = _question_set()
    questions[1] = SimpleNamespace(section="rus_tili", position=1)  # dup of pos 1
    errors = validate_test_completeness(questions)
    assert any("Позиция 1" in e for e in errors)


def test_section_count_mismatch_flagged() -> None:
    questions = _question_set()
    # Swap one rus_tili question into kasbiy (counts now 34/10/6).
    questions[0] = SimpleNamespace(section="kasbiy", position=1)
    errors = validate_test_completeness(questions)
    assert any("«rus_tili», найдено 34" in e for e in errors)
    assert any("«kasbiy», найдено 6" in e for e in errors)
