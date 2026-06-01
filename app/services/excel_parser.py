"""Excel test-upload parser.

PRODUCT_BLUEPRINT §12 defines the input contract; this module is the
gatekeeper that converts a teacher-edited ``.xlsx`` into either a
validated :class:`ParsedTest` or a list of :class:`ParseError` that the
admin can use to fix the file. **Errors are data, not exceptions** — the
caller branches on the return type (ARCHITECTURE_SPEC §8.7).

This module performs *no* DB writes. The caller persists ``ParsedTest``
through ``TestService`` / ``QuestionRepository.bulk_create``.
"""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO
from typing import Final

from openpyxl import load_workbook

# ---------- DTOs ----------


@dataclass(frozen=True)
class ParseError:
    """One validation failure pinned to an Excel row (``line == 0`` for file-level errors)."""

    line: int
    message: str


@dataclass(frozen=True)
class ParsedQuestion:
    """One validated row from the uploaded test.

    ``has_image`` is the optional ``has_image`` column: when true the question
    is expected to carry an illustration, collected in-bot after upload. The
    image id is **not** part of the Excel contract — only the intent is.
    """

    section: str
    position: int
    question_text: str
    option_a: str
    option_b: str
    option_c: str
    option_d: str
    correct_option: str
    has_image: bool = False


@dataclass(frozen=True)
class ParsedTest:
    """A full 50-question test ready for ``QuestionRepository.bulk_create``."""

    questions: tuple[ParsedQuestion, ...]


# ---------- Validation constants ----------

SHEET_NAME: Final[str] = "Questions"
EXPECTED_ROW_COUNT: Final[int] = 50
VALID_SECTIONS: Final[tuple[str, ...]] = ("rus_tili", "pedagogik", "kasbiy")
VALID_OPTIONS: Final[tuple[str, ...]] = ("A", "B", "C", "D")

# Section-to-(min_pos, max_pos, expected_count). Mirrors DATABASE_SPEC §5.5
# CHECK constraint ``ck_questions__section_position_consistent``.
SECTION_RANGES: Final[dict[str, tuple[int, int, int]]] = {
    "rus_tili": (1, 35, 35),
    "pedagogik": (36, 45, 10),
    "kasbiy": (46, 50, 5),
}

MAX_QUESTION_TEXT_LEN: Final[int] = 1000
MAX_OPTION_TEXT_LEN: Final[int] = 300

# Telegram caps a photo *caption* at 1024 chars (vs 4096 for a text message).
# An image question renders as a photo whose caption holds the dynamic header
# line + the question text + the four options. We cap the static part (text +
# options + formatting) so the assembled caption can never overflow at send
# time; ~120 chars of headroom are reserved for the timer/position/section
# header. See app/bot/views/test_screen.py for the matching layout.
MAX_IMAGE_CAPTION_BLOCK_LEN: Final[int] = 850

# Overhead of the option formatting in the caption body:
#   "{q}\n\nA. {a}\nB. {b}\nC. {c}\nD. {d}"  →  "\n\n" + 4×"X. " + 3×"\n".
_CAPTION_BODY_OVERHEAD: Final[int] = 2 + (4 * 3) + 3

# Recognised ``has_image`` tokens (case-insensitive, after stripping). Anything
# else is a parse error so a typo can't silently disable an illustration.
_HAS_IMAGE_TRUE: Final[frozenset[str]] = frozenset({"y", "yes", "true", "1", "да", "+", "✓", "x"})
_HAS_IMAGE_FALSE: Final[frozenset[str]] = frozenset({"", "n", "no", "false", "0", "нет", "-", "—"})

_REQUIRED_COLUMN_LABELS: Final[tuple[str, ...]] = (
    "section",
    "position",
    "question_text",
    "option_a",
    "option_b",
    "option_c",
    "option_d",
    "correct_option",
)

# Total columns we read positionally: the 8 required + the optional
# ``has_image`` in column I. Files authored before this column existed simply
# have it padded to None (→ falsy → text question), so they keep parsing.
_TOTAL_COLUMNS: Final[int] = 9


# ---------- Parser ----------


class ExcelParser:
    """Stateless parser. Constructed without arguments so a single instance can be reused."""

    def parse(self, file_bytes: bytes) -> ParsedTest | list[ParseError]:
        """Validate ``file_bytes`` and return either the parsed test or a list of errors."""
        try:
            workbook = load_workbook(
                BytesIO(file_bytes),
                read_only=True,
                data_only=True,
            )
        except Exception:
            # openpyxl can raise InvalidFileException, BadZipFile, OSError,
            # KeyError, ValueError and a few others — the parser's contract
            # is "never raises", so any failure here becomes a file-level
            # ParseError the admin can act on.
            return [ParseError(line=0, message="Не удалось прочитать файл. Проверьте формат.")]

        if SHEET_NAME not in workbook.sheetnames:
            return [
                ParseError(line=0, message=f"Лист «{SHEET_NAME}» не найден в файле."),
            ]

        worksheet = workbook[SHEET_NAME]
        rows = list(worksheet.iter_rows(min_row=2, values_only=True))

        errors: list[ParseError] = []
        questions: list[ParsedQuestion] = []
        seen_positions: set[int] = set()
        section_counts: dict[str, int] = dict.fromkeys(VALID_SECTIONS, 0)

        if len(rows) != EXPECTED_ROW_COUNT:
            errors.append(
                ParseError(
                    line=0,
                    message=(f"Ожидалось {EXPECTED_ROW_COUNT} вопросов, найдено {len(rows)}."),
                )
            )

        for idx, raw_row in enumerate(rows, start=2):  # row 2 is the first data row
            # Pad/trim to the expected columns so missing trailing cells surface
            # as "пусто" errors rather than IndexError. ``has_image`` (col I) is
            # optional, so a short row pads it to None → text question.
            padded = tuple(raw_row) + (None,) * _TOTAL_COLUMNS
            cells = padded[:_TOTAL_COLUMNS]
            row_errors = _validate_row(cells, line=idx)

            if row_errors:
                errors.extend(row_errors)
                continue

            question = _build_question(cells)
            questions.append(question)

            # Duplicate-position detection happens here so we keep one error
            # per duplicate occurrence (line-referenced).
            if question.position in seen_positions:
                errors.append(
                    ParseError(
                        line=idx,
                        message=f"Позиция {question.position} уже использована выше.",
                    )
                )
            else:
                seen_positions.add(question.position)

            section_counts[question.section] += 1

            # Section ↔ position-range cross-check.
            lo, hi, _ = SECTION_RANGES[question.section]
            if not (lo <= question.position <= hi):
                errors.append(
                    ParseError(
                        line=idx,
                        message=(
                            f"Вопросы раздела «{question.section}» должны быть "
                            f"на позициях {lo}–{hi}."
                        ),
                    )
                )

        # Per-section counts (skip if file-level row count already failed —
        # the counts will obviously also be wrong).
        if len(rows) == EXPECTED_ROW_COUNT:
            for section, (_, _, expected) in SECTION_RANGES.items():
                actual = section_counts[section]
                if actual != expected:
                    errors.append(
                        ParseError(
                            line=0,
                            message=(
                                f"Ожидалось {expected} вопросов в разделе "
                                f"«{section}», найдено {actual}."
                            ),
                        )
                    )

        if errors:
            return errors
        return ParsedTest(questions=tuple(questions))


# ---------- Row-level helpers ----------


def _validate_row(cells: tuple[object, ...], *, line: int) -> list[ParseError]:
    """Validate one row in isolation. Returns an empty list if all cells pass."""
    section, position, qtext, oa, ob, oc, od, correct, has_image_raw = cells
    required = cells[:8]
    errors: list[ParseError] = []

    # 1. Required cells present + correct primitive types. ``has_image`` is
    #    optional and validated separately below, so it's excluded here.
    for label, value in zip(_REQUIRED_COLUMN_LABELS, required, strict=True):
        if value is None or (isinstance(value, str) and not value.strip()):
            errors.append(ParseError(line=line, message=f"Колонка «{label}» пустая."))
    if errors:
        return errors

    # 2. Section enum.
    section_str = str(section).strip()
    if section_str not in VALID_SECTIONS:
        errors.append(
            ParseError(
                line=line,
                message=(
                    f"Значение в колонке «section» должно быть одним из "
                    f"{', '.join(VALID_SECTIONS)}."
                ),
            )
        )

    # 3. Position is an int in [1, 50].
    if not isinstance(position, int) or isinstance(position, bool):
        errors.append(
            ParseError(
                line=line,
                message="Колонка «position» должна быть целым числом.",
            )
        )
    elif not 1 <= position <= EXPECTED_ROW_COUNT:
        errors.append(
            ParseError(
                line=line,
                message=f"Позиция {position} вне диапазона 1–{EXPECTED_ROW_COUNT}.",
            )
        )

    # 4. Lengths on the text columns.
    qtext_str = str(qtext)
    if len(qtext_str) > MAX_QUESTION_TEXT_LEN:
        errors.append(
            ParseError(
                line=line,
                message=(f"Текст вопроса длиннее {MAX_QUESTION_TEXT_LEN} символов."),
            )
        )
    for opt_label, opt_value in zip(
        ("option_a", "option_b", "option_c", "option_d"),
        (oa, ob, oc, od),
        strict=True,
    ):
        if len(str(opt_value)) > MAX_OPTION_TEXT_LEN:
            errors.append(
                ParseError(
                    line=line,
                    message=(f"«{opt_label}» длиннее {MAX_OPTION_TEXT_LEN} символов."),
                )
            )

    # 5. correct_option must be one of A/B/C/D (case-insensitive on input).
    correct_str = str(correct).strip().upper()
    if correct_str not in VALID_OPTIONS:
        errors.append(
            ParseError(
                line=line,
                message=("Значение в колонке «correct_option» должно быть A, B, C или D."),
            )
        )

    # 6. has_image (optional): must be a recognised token, and an image
    #    question's text + options must fit inside a Telegram photo caption.
    has_image, has_image_err = _parse_has_image(has_image_raw)
    if has_image_err is not None:
        errors.append(ParseError(line=line, message=has_image_err))
    elif has_image:
        block_len = (
            len(qtext_str.strip())
            + len(str(oa).strip())
            + len(str(ob).strip())
            + len(str(oc).strip())
            + len(str(od).strip())
            + _CAPTION_BODY_OVERHEAD
        )
        if block_len > MAX_IMAGE_CAPTION_BLOCK_LEN:
            errors.append(
                ParseError(
                    line=line,
                    message=(
                        "Вопрос с изображением: текст вопроса и варианты вместе "
                        f"не должны превышать {MAX_IMAGE_CAPTION_BLOCK_LEN} символов "
                        "(ограничение Telegram для подписи к фото)."
                    ),
                )
            )

    return errors


def _build_question(cells: tuple[object, ...]) -> ParsedQuestion:
    """Build a :class:`ParsedQuestion` from a row that already passed ``_validate_row``."""
    section, position, qtext, oa, ob, oc, od, correct, has_image_raw = cells
    # ``_validate_row`` already proved ``position`` is an int and the text
    # cells are stringable; the cast tells mypy what the runtime checks
    # already enforced.
    assert isinstance(position, int)
    has_image, _ = _parse_has_image(has_image_raw)
    return ParsedQuestion(
        section=str(section).strip(),
        position=position,
        question_text=str(qtext).strip(),
        option_a=str(oa).strip(),
        option_b=str(ob).strip(),
        option_c=str(oc).strip(),
        option_d=str(od).strip(),
        correct_option=str(correct).strip().upper(),
        has_image=has_image,
    )


def _parse_has_image(value: object) -> tuple[bool, str | None]:
    """Interpret the optional ``has_image`` cell → ``(flag, error_message)``.

    Accepts the usual yes/no spellings in Russian or English plus 1/0; an empty
    cell means "no image". An unrecognised token is an error so a typo never
    silently drops an illustration the teacher intended.
    """
    if value is None:
        return False, None
    # openpyxl hands TRUE/FALSE cells back as Python bools.
    if isinstance(value, bool):
        return value, None
    token = str(value).strip().lower()
    if token in _HAS_IMAGE_TRUE:
        return True, None
    if token in _HAS_IMAGE_FALSE:
        return False, None
    return False, "Колонка «has_image» должна быть пустой или одним из: да/нет, yes/no, 1/0."
