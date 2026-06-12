"""Semantic validation rules for one test question — pure functions, no I/O.

Single source of truth for the question contract (PRODUCT_BLUEPRINT §12):
the Excel parser and the web panel's editor form both validate through
this module so the rules can never drift apart. The Excel parser keeps
its own cell coercion / presence checks (Excel-specific concerns) and
delegates everything semantic here.

All messages are admin-facing Russian, finalized — callers display them
verbatim (pinned to an Excel row or anchored to a form field).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final, Protocol

from app.utils.markup import validate_markup, visible_length

# ---------- Validation constants (moved from excel_parser) ----------

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
CAPTION_BODY_OVERHEAD: Final[int] = 2 + (4 * 3) + 3

# Russian display names for the sections, shared by parser messages and the
# web editor's section headings.
SECTION_TITLES_RU: Final[dict[str, str]] = {
    "rus_tili": "Русский язык",
    "pedagogik": "Педагогическое мастерство",
    "kasbiy": "Профессиональный стандарт",
}


# ---------- DTOs ----------


@dataclass(frozen=True)
class FieldError:
    """One validation failure tied to a question field.

    ``field`` is one of ``question_text`` / ``option_a``..``option_d`` /
    ``correct_option`` / ``section`` / ``position``, or ``""`` for a
    question-level error (e.g. the image-caption budget).
    """

    field: str
    message: str


class _HasSectionPosition(Protocol):
    """Anything with ``section`` + ``position`` — QuestionDraft, ParsedQuestion, Question."""

    @property
    def section(self) -> str: ...

    @property
    def position(self) -> int: ...


# ---------- Helpers ----------


def section_for_position(position: int) -> str | None:
    """Map a 1–50 position to its section key, or None when out of range."""
    for section, (lo, hi, _) in SECTION_RANGES.items():
        if lo <= position <= hi:
            return section
    return None


def caption_block_length(
    question_text: str,
    option_a: str,
    option_b: str,
    option_c: str,
    option_d: str,
) -> int:
    """Length of the photo-caption body for an image question (incl. formatting).

    Measures the *rendered* length — ``**``/``__`` markup markers are
    stripped because Telegram's caption cap applies to the parsed text,
    not the raw markers.
    """
    return (
        visible_length(question_text.strip())
        + visible_length(option_a.strip())
        + visible_length(option_b.strip())
        + visible_length(option_c.strip())
        + visible_length(option_d.strip())
        + CAPTION_BODY_OVERHEAD
    )


# ---------- Field-level validation ----------


def validate_question_fields(
    *,
    section: str,
    position: int,
    question_text: str,
    option_a: str,
    option_b: str,
    option_c: str,
    option_d: str,
    correct_option: str,
    has_image: bool,
) -> list[FieldError]:
    """Semantic checks on already-coerced values; returns all failures at once.

    Callers pass stripped strings, an int position, and an uppercase
    ``correct_option`` candidate (this function re-checks the enum, not the
    casing). Presence/typing of raw input is the caller's concern.
    """
    errors: list[FieldError] = []

    if section not in VALID_SECTIONS:
        errors.append(
            FieldError(
                field="section",
                message=(
                    f"Значение в колонке «section» должно быть одним из "
                    f"{', '.join(VALID_SECTIONS)}."
                ),
            )
        )

    if not 1 <= position <= EXPECTED_ROW_COUNT:
        errors.append(
            FieldError(
                field="position",
                message=f"Позиция {position} вне диапазона 1–{EXPECTED_ROW_COUNT}.",
            )
        )
    elif section in VALID_SECTIONS:
        lo, hi, _ = SECTION_RANGES[section]
        if not lo <= position <= hi:
            errors.append(
                FieldError(
                    field="position",
                    message=(f"Вопросы раздела «{section}» должны быть на позициях {lo}–{hi}."),
                )
            )

    if len(question_text) > MAX_QUESTION_TEXT_LEN:
        errors.append(
            FieldError(
                field="question_text",
                message=f"Текст вопроса длиннее {MAX_QUESTION_TEXT_LEN} символов.",
            )
        )
    for field, value in (
        ("option_a", option_a),
        ("option_b", option_b),
        ("option_c", option_c),
        ("option_d", option_d),
    ):
        if len(value) > MAX_OPTION_TEXT_LEN:
            errors.append(
                FieldError(
                    field=field,
                    message=f"«{field}» длиннее {MAX_OPTION_TEXT_LEN} символов.",
                )
            )

    if correct_option not in VALID_OPTIONS:
        errors.append(
            FieldError(
                field="correct_option",
                message="Значение в колонке «correct_option» должно быть A, B, C или D.",
            )
        )

    # **жирный** / __курсив__ markup must be well-formed wherever it appears,
    # or students would see stray markers verbatim.
    for field, value in (
        ("question_text", question_text),
        ("option_a", option_a),
        ("option_b", option_b),
        ("option_c", option_c),
        ("option_d", option_d),
    ):
        for message in validate_markup(value):
            errors.append(FieldError(field=field, message=message))

    if has_image:
        block_len = caption_block_length(question_text, option_a, option_b, option_c, option_d)
        if block_len > MAX_IMAGE_CAPTION_BLOCK_LEN:
            errors.append(
                FieldError(
                    field="",
                    message=(
                        "Вопрос с изображением: текст вопроса и варианты вместе "
                        f"не должны превышать {MAX_IMAGE_CAPTION_BLOCK_LEN} символов "
                        "(ограничение Telegram для подписи к фото)."
                    ),
                )
            )

    return errors


# ---------- Test-level validation ----------


def validate_test_completeness(questions: Sequence[_HasSectionPosition]) -> list[str]:
    """Whole-test invariants: 50 questions, unique positions, 35/10/5 per section.

    Used by the web panel's publish gate; mirrors the Excel parser's
    file-level checks with identical wording.
    """
    errors: list[str] = []

    if len(questions) != EXPECTED_ROW_COUNT:
        errors.append(f"Ожидалось {EXPECTED_ROW_COUNT} вопросов, найдено {len(questions)}.")

    seen: set[int] = set()
    for q in questions:
        if q.position in seen:
            errors.append(f"Позиция {q.position} использована несколько раз.")
        seen.add(q.position)

    if len(questions) == EXPECTED_ROW_COUNT:
        for section, (_, _, expected) in SECTION_RANGES.items():
            actual = sum(1 for q in questions if q.section == section)
            if actual != expected:
                errors.append(
                    f"Ожидалось {expected} вопросов в разделе «{section}», найдено {actual}."
                )

    return errors
