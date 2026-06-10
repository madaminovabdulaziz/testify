"""Parsing + validation of the 50-question editor form.

Field naming convention (matches ``test_editor.html``): ``title``,
``csrf_token``, and per position 1..50: ``q{pos}_text``, ``q{pos}_a`` ..
``q{pos}_d``, ``q{pos}_correct`` (radio A–D), ``q{pos}_has_image``
(checkbox, ``"1"`` when checked, absent otherwise).

Sections are never typed by the admin — they derive from the fixed
position layout, which makes the 35/10/5 split unviolatable from the web.
Completely empty cards are skipped so drafts can be saved incrementally.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from app.repositories.question_repository import QuestionDraft
from app.services.question_validation import (
    EXPECTED_ROW_COUNT,
    section_for_position,
    validate_question_fields,
)

_MAX_TITLE_LEN = 200
_REQUIRED_MSG = "Заполните поле."
_NO_CORRECT_MSG = "Выберите правильный ответ."

# Form-field suffix -> validator-field name, for the four options.
_OPTION_FIELDS = (("a", "option_a"), ("b", "option_b"), ("c", "option_c"), ("d", "option_d"))


@dataclass(frozen=True)
class ParsedTestForm:
    """Outcome of parsing the editor POST body."""

    title: str
    drafts: list[QuestionDraft]
    # position -> field -> message; field "" is a question-level error.
    field_errors: dict[int, dict[str, str]]
    form_errors: list[str]
    # position -> raw submitted values, echoed back into the re-rendered form.
    raw: dict[int, dict[str, str]] = field(default_factory=dict)

    @property
    def has_errors(self) -> bool:
        return bool(self.field_errors or self.form_errors)


def parse_test_form(data: Mapping[str, str]) -> ParsedTestForm:
    """Turn the flat POST mapping into validated ``QuestionDraft``s + errors."""
    raw_title = data.get("title", "")
    title = raw_title.strip() if isinstance(raw_title, str) else ""

    form_errors: list[str] = []
    if not title:
        form_errors.append("Укажите название теста.")
    elif len(title) > _MAX_TITLE_LEN:
        form_errors.append(f"Название длиннее {_MAX_TITLE_LEN} символов.")

    drafts: list[QuestionDraft] = []
    field_errors: dict[int, dict[str, str]] = {}
    raw: dict[int, dict[str, str]] = {}

    for pos in range(1, EXPECTED_ROW_COUNT + 1):
        text = _get(data, f"q{pos}_text")
        options = {suffix: _get(data, f"q{pos}_{suffix}") for suffix, _ in _OPTION_FIELDS}
        correct = _get(data, f"q{pos}_correct").upper()
        has_image = data.get(f"q{pos}_has_image") == "1"

        raw[pos] = {
            "text": text,
            **{suffix: options[suffix] for suffix, _ in _OPTION_FIELDS},
            "correct": correct,
            "has_image": "1" if has_image else "",
        }

        filled = [text, *options.values(), correct]
        if not any(filled):
            # Completely empty card — skipped; ``has_image`` alone is noise.
            continue

        errors: dict[str, str] = {}
        if not text:
            errors["question_text"] = _REQUIRED_MSG
        for suffix, validator_field in _OPTION_FIELDS:
            if not options[suffix]:
                errors[validator_field] = _REQUIRED_MSG
        if not correct:
            errors["correct_option"] = _NO_CORRECT_MSG

        if errors:
            field_errors[pos] = errors
            continue

        section = section_for_position(pos)
        assert section is not None  # pos iterates 1..50 by construction
        semantic = validate_question_fields(
            section=section,
            position=pos,
            question_text=text,
            option_a=options["a"],
            option_b=options["b"],
            option_c=options["c"],
            option_d=options["d"],
            correct_option=correct,
            has_image=has_image,
        )
        if semantic:
            field_errors[pos] = {fe.field: fe.message for fe in semantic}
            continue

        drafts.append(
            QuestionDraft(
                section=section,
                position=pos,
                question_text=text,
                option_a=options["a"],
                option_b=options["b"],
                option_c=options["c"],
                option_d=options["d"],
                correct_option=correct,
                has_image=has_image,
            )
        )

    return ParsedTestForm(
        title=title,
        drafts=drafts,
        field_errors=field_errors,
        form_errors=form_errors,
        raw=raw,
    )


def _get(data: Mapping[str, str], key: str) -> str:
    value = data.get(key, "")
    return value.strip() if isinstance(value, str) else ""
