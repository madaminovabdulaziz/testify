"""Pre-test confirmation, finish confirmation, prior-result, and final result screens.

ARCHITECTURE_SPEC §9 + §10.2; PRODUCT_BLUEPRINT §8.5 + §8.6. The four
flows that surround the test-screen itself all render one-shot messages:

* :func:`render_pretest_screen` — "are you ready?" with [Начать][Назад]
* :func:`render_finish_confirmation` — "you've answered X/50, finish?"
* :func:`render_prior_result_screen` — re-shown when the user re-enters
  a test they already finished
* :func:`render_result_screen` — the score + per-section breakdown +
  link-back-to-the-chat button shown immediately after finish/expiry

All four are pure functions of their inputs.
"""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.bot.callbacks.test import TestFinishCD
from app.bot.views import RenderedMessage
from app.core.i18n import (
    BTN_CANCEL,
    BTN_CONFIRM_FINISH,
    BTN_CONTINUE_TEST,
    BTN_GO_TO_CHAT,
    BTN_TAKE_TEST,
)
from app.models.attempt import Attempt
from app.services.scoring_service import SectionScores
from app.utils.datetime import format_duration_mm_ss

# Reserved callback_data string for the "start" button on the pre-test
# screen. Doesn't need a CallbackData factory since it carries no payload
# beyond "user confirmed".
START_TEST_CALLBACK = "start_test"
CONFIRM_START_TEST_CALLBACK = "confirm_start_test"
CANCEL_PRETEST_CALLBACK = "cancel_pretest"

# DTM section sizes — the exam is fixed at 35 + 10 + 5 = 50 questions
# (PRODUCT_BLUEPRINT §3.1). Named so the score math isn't a bare magic
# number (CODE_REVIEW N4).
_TOTAL_QUESTIONS = 50


# ---------- pre-test ----------


def render_pretest_screen() -> RenderedMessage:
    """Show the "вы готовы начать?" screen with [Начать][Назад] buttons (§8.5)."""
    text = (
        "📝 Тест готов к прохождению\n\n"
        "Структура:\n"
        "  • Русский язык: вопросы 1–35\n"
        "  • Педагогическое мастерство: вопросы 36–45\n"
        "  • Профессиональный стандарт: вопросы 46–50\n\n"
        "⏱ Время: 53 минуты 20 секунд (на весь тест)\n"
        "📊 Результат: только балл, без разбора (разбор — в чате)\n\n"
        "⚠️ Внимание: как только вы нажмёте «Начать», таймер запустится.\n"
        "Тест можно пройти только один раз."
    )

    builder = InlineKeyboardBuilder()
    builder.button(text=BTN_TAKE_TEST, callback_data=CONFIRM_START_TEST_CALLBACK)
    builder.button(text=BTN_CANCEL, callback_data=CANCEL_PRETEST_CALLBACK)
    builder.adjust(1)
    return RenderedMessage(text=text, reply_markup=builder.as_markup())


# ---------- finish confirmation ----------


def render_finish_confirmation(
    attempt_id: int,
    *,
    answered_count: int,
    total_questions: int = _TOTAL_QUESTIONS,
) -> RenderedMessage:
    """Show the «Вы ответили на X из 50» dialog (§8.5)."""
    unanswered = max(0, total_questions - answered_count)
    text = (
        f"Вы ответили на {answered_count} из {total_questions} вопросов.\n"
        f"{unanswered} вопросов остались без ответа.\n\n"
        "Завершить тест и узнать результат?"
    )

    builder = InlineKeyboardBuilder()
    builder.button(
        text=BTN_CONFIRM_FINISH,
        callback_data=TestFinishCD(attempt_id=attempt_id, confirmed=True),
    )
    builder.button(
        text=BTN_CONTINUE_TEST,
        callback_data=TestFinishCD(attempt_id=attempt_id, confirmed=False),
    )
    builder.adjust(1)
    return RenderedMessage(text=text, reply_markup=builder.as_markup())


# ---------- result ----------


def render_result_screen(
    attempt: Attempt,
    scores: SectionScores,
    *,
    group_invite_link: str | None,
) -> RenderedMessage:
    """Final score + per-section breakdown + link to the group chat (§8.6)."""
    percentage = round(scores.total / _TOTAL_QUESTIONS * 100, 1)
    duration_text = _format_attempt_duration(attempt)

    text = (
        "🏁 Тест завершён!\n\n"
        f"📊 Ваш результат: {scores.total}/{_TOTAL_QUESTIONS}  ({percentage}%)\n\n"
        "По разделам:\n"
        f"  • Русский язык: {scores.rus_tili}/35\n"
        f"  • Педагогическое мастерство: {scores.pedagogik}/10\n"
        f"  • Профессиональный стандарт: {scores.kasbiy}/5\n\n"
        f"⏱ Затрачено времени: {duration_text}\n\n"
        "Разбор вопросов — в чате студентов."
    )

    return RenderedMessage(
        text=text,
        reply_markup=_chat_link_keyboard(group_invite_link),
    )


def render_prior_result_screen(
    attempt: Attempt,
    scores: SectionScores,
    *,
    group_invite_link: str | None,
) -> RenderedMessage:
    """Short version shown when the user re-enters a test they've already finished (§8.6).

    Per PRODUCT_BLUEPRINT §11.3 the canonical message is just "Вы уже
    проходили этот тест.\\nВаш результат: X/50". We add the per-section
    breakdown + chat link too so the student gets the full result every
    time, not only on the first submit.
    """
    text = (
        "Вы уже проходили этот тест.\n\n"
        f"📊 Ваш результат: {scores.total}/{_TOTAL_QUESTIONS}\n\n"
        "По разделам:\n"
        f"  • Русский язык: {scores.rus_tili}/35\n"
        f"  • Педагогическое мастерство: {scores.pedagogik}/10\n"
        f"  • Профессиональный стандарт: {scores.kasbiy}/5"
    )
    return RenderedMessage(
        text=text,
        reply_markup=_chat_link_keyboard(group_invite_link),
    )


# ---------- helpers ----------


def _chat_link_keyboard(group_invite_link: str | None) -> InlineKeyboardMarkup | None:
    """One-row inline keyboard with the «💬 Перейти в чат» button if a link is set."""
    link = (group_invite_link or "").strip()
    if not link:
        return None
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=BTN_GO_TO_CHAT, url=link)]]
    )


def _format_attempt_duration(attempt: Attempt) -> str:
    """Format how long the attempt took, as ``MM:SS`` (fallback: «—»)."""
    if attempt.finished_at is None or attempt.started_at is None:
        return "—"
    delta_seconds = int((attempt.finished_at - attempt.started_at).total_seconds())
    return format_duration_mm_ss(delta_seconds)


__all__ = [
    "CANCEL_PRETEST_CALLBACK",
    "CONFIRM_START_TEST_CALLBACK",
    "START_TEST_CALLBACK",
    "render_finish_confirmation",
    "render_pretest_screen",
    "render_prior_result_screen",
    "render_result_screen",
]
