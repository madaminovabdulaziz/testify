"""Unit tests for :class:`app.services.excel_parser.ExcelParser`.

Tests build their own ``.xlsx`` bytes in memory via ``openpyxl`` rather
than committing fixture files — each test case is self-contained and the
suite remains fast.
"""

from __future__ import annotations

from collections.abc import Iterable
from io import BytesIO

from openpyxl import Workbook

from app.services.excel_parser import (
    ExcelParser,
    ParsedTest,
    ParseError,
)

# ---------- builders ----------


def _valid_rows() -> list[tuple[object, ...]]:
    """A canonical valid 50-row payload: 35 rus_tili + 10 pedagogik + 5 kasbiy."""
    rows: list[tuple[object, ...]] = []
    for pos in range(1, 36):
        rows.append(("rus_tili", pos, f"Вопрос {pos}", "A1", "B1", "C1", "D1", "A"))
    for pos in range(36, 46):
        rows.append(("pedagogik", pos, f"Вопрос {pos}", "A1", "B1", "C1", "D1", "B"))
    for pos in range(46, 51):
        rows.append(("kasbiy", pos, f"Вопрос {pos}", "A1", "B1", "C1", "D1", "C"))
    return rows


def _xlsx_bytes(
    rows: Iterable[tuple[object, ...]],
    *,
    sheet_name: str = "Questions",
    include_header: bool = True,
) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = sheet_name
    if include_header:
        sheet.append(
            [
                "section",
                "position",
                "question_text",
                "option_a",
                "option_b",
                "option_c",
                "option_d",
                "correct_option",
            ]
        )
    for row in rows:
        sheet.append(list(row))
    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


# ---------- happy path ----------


def test_valid_file_parses_to_50_questions() -> None:
    parser = ExcelParser()
    result = parser.parse(_xlsx_bytes(_valid_rows()))
    assert isinstance(result, ParsedTest)
    assert len(result.questions) == 50
    assert result.questions[0].section == "rus_tili"
    assert result.questions[0].position == 1
    assert result.questions[35].section == "pedagogik"
    assert result.questions[45].section == "kasbiy"


def test_correct_option_is_normalized_to_uppercase() -> None:
    rows = _valid_rows()
    rows[0] = ("rus_tili", 1, "Q", "A", "B", "C", "D", "a")  # lowercase
    parser = ExcelParser()
    result = parser.parse(_xlsx_bytes(rows))
    assert isinstance(result, ParsedTest)
    assert result.questions[0].correct_option == "A"


# ---------- file-level errors ----------


def test_unreadable_bytes_returns_single_file_level_error() -> None:
    result = ExcelParser().parse(b"this is not an xlsx file")
    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0].line == 0
    assert "формат" in result[0].message.lower()


def test_missing_questions_sheet_returns_error() -> None:
    raw = _xlsx_bytes(_valid_rows(), sheet_name="Wrong")
    result = ExcelParser().parse(raw)
    assert isinstance(result, list)
    assert any("Questions" in e.message for e in result)


def test_wrong_row_count_too_few() -> None:
    rows = _valid_rows()[:49]
    result = ExcelParser().parse(_xlsx_bytes(rows))
    assert isinstance(result, list)
    assert any("Ожидалось 50" in e.message and e.line == 0 for e in result)


def test_wrong_row_count_too_many() -> None:
    rows = _valid_rows()
    rows.append(("kasbiy", 50, "Extra", "A", "B", "C", "D", "A"))  # 51st row
    result = ExcelParser().parse(_xlsx_bytes(rows))
    assert isinstance(result, list)
    assert any("Ожидалось 50" in e.message for e in result)


# ---------- row-level errors ----------


def test_empty_required_cell_is_reported_with_line_number() -> None:
    rows = _valid_rows()
    rows[0] = ("rus_tili", 1, "", "A", "B", "C", "D", "A")  # empty question_text
    result = ExcelParser().parse(_xlsx_bytes(rows))
    assert isinstance(result, list)
    # row 1 of data == line 2 in the file (header is line 1).
    assert any(e.line == 2 and "question_text" in e.message for e in result)


def test_invalid_section_value_is_reported() -> None:
    rows = _valid_rows()
    rows[0] = ("MATH", 1, "Q", "A", "B", "C", "D", "A")
    result = ExcelParser().parse(_xlsx_bytes(rows))
    assert isinstance(result, list)
    assert any(e.line == 2 and "section" in e.message for e in result)


def test_invalid_correct_option_value_is_reported() -> None:
    rows = _valid_rows()
    rows[0] = ("rus_tili", 1, "Q", "A", "B", "C", "D", "Z")
    result = ExcelParser().parse(_xlsx_bytes(rows))
    assert isinstance(result, list)
    assert any(e.line == 2 and "correct_option" in e.message for e in result)


def test_out_of_range_position_is_reported() -> None:
    rows = _valid_rows()
    rows[0] = ("rus_tili", 99, "Q", "A", "B", "C", "D", "A")
    result = ExcelParser().parse(_xlsx_bytes(rows))
    assert isinstance(result, list)
    assert any(e.line == 2 and "99" in e.message for e in result)


def test_non_integer_position_is_reported() -> None:
    rows = _valid_rows()
    rows[0] = ("rus_tili", "first", "Q", "A", "B", "C", "D", "A")  # position is a string
    result = ExcelParser().parse(_xlsx_bytes(rows))
    assert isinstance(result, list)
    assert any(e.line == 2 and "целым числом" in e.message for e in result)


def test_duplicate_position_is_reported() -> None:
    rows = _valid_rows()
    # Row 2 reuses position 1; both rows are line 2 and line 3.
    rows[1] = ("rus_tili", 1, "Q-dup", "A", "B", "C", "D", "A")
    result = ExcelParser().parse(_xlsx_bytes(rows))
    assert isinstance(result, list)
    assert any(e.line == 3 and "уже использован" in e.message for e in result)


def test_section_position_mismatch_is_reported() -> None:
    rows = _valid_rows()
    # rus_tili at position 45 (which belongs to pedagogik) — also creates a count mismatch.
    rows[0] = ("rus_tili", 45, "Q", "A", "B", "C", "D", "A")
    result = ExcelParser().parse(_xlsx_bytes(rows))
    assert isinstance(result, list)
    assert any(e.line == 2 and "rus_tili" in e.message and "1–35" in e.message for e in result)


def test_wrong_section_count_is_reported_at_file_level() -> None:
    rows = _valid_rows()
    # Convert one pedagogik row into a kasbiy row → pedagogik=9, kasbiy=6.
    rows[35] = ("kasbiy", 36, "Q36", "A", "B", "C", "D", "B")
    result = ExcelParser().parse(_xlsx_bytes(rows))
    assert isinstance(result, list)
    assert any(e.line == 0 and "pedagogik" in e.message and "10" in e.message for e in result)


# ---------- length bounds ----------


def test_question_text_too_long_is_reported() -> None:
    rows = _valid_rows()
    rows[0] = (
        "rus_tili",
        1,
        "x" * 1001,  # 1001 chars — one over the limit
        "A",
        "B",
        "C",
        "D",
        "A",
    )
    result = ExcelParser().parse(_xlsx_bytes(rows))
    assert isinstance(result, list)
    assert any(e.line == 2 and "1000" in e.message for e in result)


def test_option_text_too_long_is_reported() -> None:
    rows = _valid_rows()
    rows[0] = ("rus_tili", 1, "Q", "x" * 301, "B", "C", "D", "A")
    result = ExcelParser().parse(_xlsx_bytes(rows))
    assert isinstance(result, list)
    assert any(e.line == 2 and "option_a" in e.message for e in result)


# ---------- error aggregation ----------


def test_multiple_errors_are_aggregated_in_one_pass() -> None:
    """Admin should see every problem at once, not fix-and-resubmit one by one."""
    rows = _valid_rows()
    rows[0] = ("rus_tili", 99, "Q", "A", "B", "C", "D", "Z")  # 2 errors on this row
    rows[1] = ("MATH", 2, "Q", "A", "B", "C", "D", "A")  # 1 error on this row
    result = ExcelParser().parse(_xlsx_bytes(rows))
    assert isinstance(result, list)
    # Expect at least 3 distinct errors above plus a section-count delta.
    assert len(result) >= 3


def test_parse_error_dataclass_is_frozen() -> None:
    import dataclasses

    import pytest

    err = ParseError(line=1, message="x")
    with pytest.raises(dataclasses.FrozenInstanceError):
        err.line = 2  # type: ignore[misc]


# ---------- has_image column (optional col I) ----------


def test_has_image_defaults_false_when_column_absent() -> None:
    # The canonical 8-column file has no has_image column at all.
    result = ExcelParser().parse(_xlsx_bytes(_valid_rows()))
    assert isinstance(result, ParsedTest)
    assert all(q.has_image is False for q in result.questions)


def test_has_image_truthy_token_flags_question() -> None:
    rows = _valid_rows()
    rows[0] = ("rus_tili", 1, "Q", "A", "B", "C", "D", "A", "да")
    result = ExcelParser().parse(_xlsx_bytes(rows))
    assert isinstance(result, ParsedTest)
    assert result.questions[0].has_image is True
    assert result.questions[1].has_image is False


def test_has_image_falsy_token_keeps_question_text() -> None:
    rows = _valid_rows()
    rows[0] = ("rus_tili", 1, "Q", "A", "B", "C", "D", "A", "нет")
    result = ExcelParser().parse(_xlsx_bytes(rows))
    assert isinstance(result, ParsedTest)
    assert result.questions[0].has_image is False


def test_has_image_invalid_token_is_reported() -> None:
    rows = _valid_rows()
    rows[0] = ("rus_tili", 1, "Q", "A", "B", "C", "D", "A", "maybe")
    result = ExcelParser().parse(_xlsx_bytes(rows))
    assert isinstance(result, list)
    assert any(e.line == 2 and "has_image" in e.message for e in result)


def test_image_question_caption_block_too_long_is_reported() -> None:
    rows = _valid_rows()
    # 900-char stem on an image question overflows the photo-caption budget.
    rows[0] = ("rus_tili", 1, "Я" * 900, "A", "B", "C", "D", "A", "да")
    result = ExcelParser().parse(_xlsx_bytes(rows))
    assert isinstance(result, list)
    assert any(e.line == 2 and "подписи к фото" in e.message for e in result)


def test_long_stem_is_fine_for_a_text_question() -> None:
    # The same 900-char stem is allowed for a plain (no-image) question —
    # the tighter caption cap only applies when has_image is set.
    rows = _valid_rows()
    rows[0] = ("rus_tili", 1, "Я" * 900, "A", "B", "C", "D", "A", "нет")
    result = ExcelParser().parse(_xlsx_bytes(rows))
    assert isinstance(result, ParsedTest)
