"""Render the in-test screen the student sees on every button tap.

ARCHITECTURE_SPEC §9.1 + §9.2 / PRODUCT_BLUEPRINT §8.5.

Pure: takes an :class:`~app.services.attempt_service.AttemptState` and
emits one ``RenderedMessage`` with:

* the timer / question / section header line
* the question text + four lettered options
* the inline keyboard — A/B/C/D, ← →, finish, then the 5-row 50-button
  number grid

The handler edits a single message with this output on every tap (per
the spec — single message, edited in place).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.bot.callbacks.test import TestAnswerCD, TestFinishCD, TestNavCD
from app.bot.views import RenderedMessage
from app.core.i18n import (
    BTN_BACK,
    BTN_FINISH_TEST,
    BTN_FORWARD,
    SECTION_LABELS,
)
from app.models.question import Question
from app.services.attempt_service import AttemptState
from app.utils.datetime import format_duration_mm_ss
from app.utils.markup import render_markup
from app.utils.text import html_escape


@dataclass(frozen=True)
class _Layout:
    """Internal helper holding the data both message + keyboard need."""

    questions_by_position: dict[int, Question]
    answered_positions: frozenset[int]
    current_position: int


def render_test_screen(state: AttemptState) -> RenderedMessage:
    """Build the test-taking message + inline keyboard for one tick of the attempt.

    A question with an attached illustration renders as a Telegram *photo*
    message (the image carries the table / chart / diagram; the caption holds
    the header + question + options). Every other question stays a plain-text
    message exactly as before. The transport layer (handlers) edits in place
    when the message type is unchanged and resends only when crossing the
    text↔photo boundary.
    """
    layout = _Layout(
        questions_by_position={q.position: q for q in state.questions},
        answered_positions=_answered_positions(state),
        current_position=state.current_position,
    )

    current_question = layout.questions_by_position.get(state.current_position)
    keyboard = _build_keyboard(state.attempt_id, layout)

    image_file_id = getattr(current_question, "image_file_id", None)
    if current_question is not None and image_file_id:
        # Photo mode: same caption layout as text mode (header + body); the
        # parser bounds the text+options block to fit Telegram's 1024-char
        # caption cap.
        caption = _compose(_header_line(state, current_question), _question_body(current_question))
        return RenderedMessage(
            text=caption,
            reply_markup=keyboard,
            photo_file_id=image_file_id,
        )

    text = _format_message_text(state, current_question)
    return RenderedMessage(text=text, reply_markup=keyboard)


# ---------- text ----------


def _format_message_text(state: AttemptState, question: Question | None) -> str:
    # No section legend below the question — the header line already names
    # the current section and the number grid conveys the structure.
    return _compose(_header_line(state, question), _question_body(question))


def _header_line(state: AttemptState, question: Question | None) -> str:
    section_label = (
        SECTION_LABELS.get(question.section, question.section) if question is not None else "—"
    )
    return (
        f"⏱ Осталось: {format_duration_mm_ss(state.time_remaining_seconds)}  ·  "
        f"Вопрос {state.current_position}/50  ·  Раздел: {html_escape(section_label)}"
    )


def _question_body(question: Question | None) -> str:
    if question is None:
        return "<i>Вопрос не найден.</i>"
    # render_markup escapes first, then converts **жирный**/__курсив__
    # markers into Telegram <b>/<i> entities.
    return (
        f"{render_markup(question.question_text)}\n\n"
        f"A. {render_markup(question.option_a)}\n"
        f"B. {render_markup(question.option_b)}\n"
        f"C. {render_markup(question.option_c)}\n"
        f"D. {render_markup(question.option_d)}"
    )


def _compose(header: str, body: str) -> str:
    return f"{header}\n\n{body}"


# ---------- keyboard ----------


def _build_keyboard(attempt_id: int, layout: _Layout) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()

    # 1) A / B / C / D — one per row for readability (§9.1).
    for option in ("A", "B", "C", "D"):
        builder.button(
            text=option,
            callback_data=TestAnswerCD(
                attempt_id=attempt_id,
                question_pos=layout.current_position,
                option=option,
            ),
        )
    builder.adjust(1)

    # 2) ← / → navigation row.
    prev_pos = max(1, layout.current_position - 1)
    next_pos = min(50, layout.current_position + 1)
    builder.row(
        InlineKeyboardButton(
            text=BTN_BACK,
            callback_data=TestNavCD(attempt_id=attempt_id, target_pos=prev_pos).pack(),
        ),
        InlineKeyboardButton(
            text=BTN_FORWARD,
            callback_data=TestNavCD(attempt_id=attempt_id, target_pos=next_pos).pack(),
        ),
    )

    # 3) 🏁 Finish — full-width row.
    builder.row(
        InlineKeyboardButton(
            text=BTN_FINISH_TEST,
            callback_data=TestFinishCD(attempt_id=attempt_id, confirmed=False).pack(),
        )
    )

    # 4) 5-row 50-button number grid. Section labels go in the message
    # text, not the keyboard (Telegram has no non-interactive buttons).
    for chunk in _chunked(range(1, 51), 10):
        row = [
            InlineKeyboardButton(
                text=_format_grid_label(pos, layout),
                callback_data=TestNavCD(attempt_id=attempt_id, target_pos=pos).pack(),
            )
            for pos in chunk
        ]
        builder.row(*row)

    return builder.as_markup()


def _format_grid_label(position: int, layout: _Layout) -> str:
    if position == layout.current_position:
        return f"🔴{position}"
    if position in layout.answered_positions:
        return f"{position}✅"
    return str(position)


# ---------- helpers ----------


def _answered_positions(state: AttemptState) -> frozenset[int]:
    """Map the per-question-id answers back to positions for grid highlighting."""
    pos_by_qid = {q.id: q.position for q in state.questions}
    return frozenset(pos_by_qid[qid] for qid in state.answers_by_question_id if qid in pos_by_qid)


def _chunked(seq: Iterable[int], size: int) -> Iterable[list[int]]:
    """Yield consecutive ``size``-element lists from ``seq``."""
    batch: list[int] = []
    for item in seq:
        batch.append(item)
        if len(batch) == size:
            yield batch
            batch = []
    if batch:
        yield batch
