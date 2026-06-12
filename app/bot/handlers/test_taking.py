"""Test-taking handlers: the in-flight exam UX (PRODUCT_BLUEPRINT §8.5).

Entry points:

* ``/test`` command
* the "Пройти тест" button from the new-test broadcast / from a fresh
  user start

Flow (ARCHITECTURE_SPEC §10.2):

* Pre-test screen — confirmation + structure summary.
* Per-tick: ``TestAnswerCD`` upserts an answer + advances cursor;
  ``TestNavCD`` jumps to a position; ``TestFinishCD`` opens a
  confirmation dialog; confirmed=True calls ``finish()`` and renders
  the result.
* Re-entry mid-attempt rebuilds the screen from DB state (resume after
  Redis flush or app restart).
* Re-entry after a finished attempt short-circuits to the prior-result
  screen — never a second attempt.

The single message is edited in place on every tap. Telegram's "message
is not modified" error is **not** an error — we swallow it so a no-op
re-tap doesn't surface to the user.
"""

from __future__ import annotations

import contextlib

import structlog
from aiogram import F, Router
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.filters import Command, StateFilter, or_f
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InputMediaPhoto, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.callbacks.test import TestAnswerCD, TestFinishCD, TestNavCD
from app.bot.states.test_taking import TestState
from app.bot.views import RenderedMessage
from app.bot.views.result_screen import (
    CANCEL_PRETEST_CALLBACK,
    CONFIRM_START_TEST_CALLBACK,
    START_TEST_CALLBACK,
    render_finish_confirmation,
    render_pretest_screen,
    render_prior_result_screen,
    render_result_screen,
)
from app.bot.views.test_screen import render_test_screen
from app.core.container import Container, Services
from app.core.i18n import BTN_MENU_TAKE_TEST
from app.exceptions import AttemptAlreadyExistsError, NotApprovedError
from app.models.user import User
from app.services.attempt_service import AttemptState
from app.services.scoring_service import section_scores_from_attempt

logger = structlog.get_logger()

router = Router(name="test_taking")

# Hardcoded fallbacks for the runtime-mutable strings in case the
# settings table lookup misses. The seeded defaults make these unused in
# practice, but PRODUCT_BLUEPRINT §15.2 wants graceful degradation.
_FALLBACK_NO_ACTIVE_TEST = (
    "Сейчас нет доступных тестов. Преподаватель опубликует следующий — мы вам сообщим."
)
_NOT_APPROVED = "Сначала нужно оплатить подготовку. Отправьте /start, чтобы начать."
_PRETEST_CANCELLED = "Хорошо. Вы можете начать тест позже из меню."
_USE_BUTTONS_HINT = "Используйте кнопки под сообщением, чтобы продолжить тест."


# ============================================================
# Entry points
# ============================================================


@router.message(or_f(Command("test"), F.text == BTN_MENU_TAKE_TEST))
async def cmd_test(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    user: User,
    container: Container,
) -> None:
    """``/test`` or the «▶️ Пройти тест» menu button — route to the right screen."""
    await _enter_test_flow(
        message=message,
        state=state,
        session=session,
        user=user,
        container=container,
    )


@router.callback_query(F.data == START_TEST_CALLBACK)
async def on_start_test_button(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    user: User,
    container: Container,
) -> None:
    """Inline «Пройти тест» button (could appear in a future menu)."""
    await callback.answer()
    if callback.message is None:
        return
    await _enter_test_flow(
        message=callback.message,
        state=state,
        session=session,
        user=user,
        container=container,
    )


# ============================================================
# Pre-test confirmation
# ============================================================


@router.callback_query(F.data == CONFIRM_START_TEST_CALLBACK)
async def on_confirm_start(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    user: User,
    container: Container,
) -> None:
    """User tapped «Начать» on the pre-test screen — create the attempt."""
    await callback.answer()
    services = container.services(session)
    active_test = await services.test.get_active_test()
    if active_test is None:
        text = (await services.settings.get("msg_no_active_test")) or _FALLBACK_NO_ACTIVE_TEST
        await _edit_or_answer(callback, RenderedMessage(text=text))
        return

    try:
        attempt = await services.attempt.start(user, active_test)
    except NotApprovedError:
        await _edit_or_answer(callback, RenderedMessage(text=_NOT_APPROVED))
        return
    except AttemptAlreadyExistsError as exc:
        if exc.existing_attempt_id is None:
            # Concurrent-start IntegrityError (M1): the session is mid-rollback,
            # so we can't read here. Re-raise — the middleware rolls back and
            # the global handler shows "Вы уже проходили этот тест."
            raise
        # Race: someone (the user in another window, or a scheduled job)
        # created the attempt between the pre-test render and confirm.
        await _resume_or_show_prior(
            callback,
            attempt_id=exc.existing_attempt_id,
            state=state,
            services=services,
            user=user,
        )
        return

    await state.set_state(TestState.in_progress)
    await state.update_data(attempt_id=attempt.id)

    attempt_state = await services.attempt.get_state(attempt.id, user_id=user.id)
    await _edit_or_answer(callback, render_test_screen(attempt_state))


@router.callback_query(F.data == CANCEL_PRETEST_CALLBACK)
async def on_pretest_cancel(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    """User tapped «Назад» on the pre-test screen."""
    await callback.answer()
    await state.clear()
    await _edit_or_answer(callback, RenderedMessage(text=_PRETEST_CANCELLED))


# ============================================================
# Answer / Nav / Finish callbacks
# ============================================================


@router.callback_query(TestAnswerCD.filter())
async def on_test_answer(
    callback: CallbackQuery,
    callback_data: TestAnswerCD,
    state: FSMContext,
    session: AsyncSession,
    user: User,
    container: Container,
) -> None:
    """Persist the user's pick, advance to the next unanswered question, re-render."""
    await callback.answer()
    services = container.services(session)

    await services.attempt.submit_answer(
        callback_data.attempt_id,
        user_id=user.id,
        question_position=callback_data.question_pos,
        selected_option=callback_data.option,
    )

    attempt_state = await services.attempt.get_state(callback_data.attempt_id, user_id=user.id)
    if attempt_state.status != "in_progress":
        await _show_finished(
            callback, services=services, state=state, attempt_state=attempt_state, user=user
        )
        return

    new_position = _pick_next_position(attempt_state, just_answered_pos=callback_data.question_pos)
    if new_position != attempt_state.current_position:
        await services.attempt.set_current_position(
            callback_data.attempt_id,
            user_id=user.id,
            position=new_position,
        )
        attempt_state = await services.attempt.get_state(callback_data.attempt_id, user_id=user.id)

    await _edit_or_answer(callback, render_test_screen(attempt_state))


@router.callback_query(TestNavCD.filter())
async def on_test_nav(
    callback: CallbackQuery,
    callback_data: TestNavCD,
    state: FSMContext,
    session: AsyncSession,
    user: User,
    container: Container,
) -> None:
    """Jump to ``target_pos``: persist the cursor + re-render the test screen."""
    await callback.answer()
    services = container.services(session)

    attempt_state = await services.attempt.get_state(callback_data.attempt_id, user_id=user.id)
    if attempt_state.status != "in_progress":
        await _show_finished(
            callback, services=services, state=state, attempt_state=attempt_state, user=user
        )
        return

    target = callback_data.target_pos
    if not 1 <= target <= 50:
        # Reserved 0 ("next unanswered") would land here. Ignore for now.
        return

    if target != attempt_state.current_position:
        await services.attempt.set_current_position(
            callback_data.attempt_id,
            user_id=user.id,
            position=target,
        )
        attempt_state = await services.attempt.get_state(callback_data.attempt_id, user_id=user.id)

    await _edit_or_answer(callback, render_test_screen(attempt_state))


@router.callback_query(TestFinishCD.filter(F.confirmed.is_(False)))
async def on_finish_request(
    callback: CallbackQuery,
    callback_data: TestFinishCD,
    state: FSMContext,
    session: AsyncSession,
    user: User,
    container: Container,
) -> None:
    """🏁 Завершить тест — show the answered/unanswered confirmation dialog."""
    await callback.answer()
    services = container.services(session)

    attempt_state = await services.attempt.get_state(callback_data.attempt_id, user_id=user.id)
    if attempt_state.status != "in_progress":
        await _show_finished(
            callback, services=services, state=state, attempt_state=attempt_state, user=user
        )
        return

    await state.set_state(TestState.confirming_finish)
    await state.update_data(attempt_id=callback_data.attempt_id)

    rendered = render_finish_confirmation(
        callback_data.attempt_id,
        answered_count=len(attempt_state.answers_by_question_id),
    )
    await _edit_or_answer(callback, rendered)


@router.callback_query(TestFinishCD.filter(F.confirmed.is_(True)))
async def on_finish_confirmed(
    callback: CallbackQuery,
    callback_data: TestFinishCD,
    state: FSMContext,
    session: AsyncSession,
    user: User,
    container: Container,
) -> None:
    """User confirmed — finalize the attempt and render the result."""
    await callback.answer()
    services = container.services(session)

    # Ownership check up front — get_state raises if attempt doesn't
    # belong to this user.
    attempt_state = await services.attempt.get_state(callback_data.attempt_id, user_id=user.id)

    result = await services.attempt.finish(callback_data.attempt_id, reason="user")
    await state.clear()

    link = await services.settings.get("group_invite_link")
    marks = await services.attempt.get_question_marks(callback_data.attempt_id)
    await _edit_or_answer(
        callback,
        render_result_screen(result.attempt, result.scores, group_invite_link=link, marks=marks),
    )

    logger.info(
        "attempt_user_finished",
        attempt_id=callback_data.attempt_id,
        user_id=user.id,
        score_total=result.scores.total,
        prior_status=attempt_state.status,
    )


# ============================================================
# StateFilter fallbacks (in case the user types text mid-test)
# ============================================================


@router.message(StateFilter(TestState.in_progress, TestState.confirming_finish))
async def in_test_text_reminder(message: Message) -> None:
    """Any non-button input while a test is open — gentle reminder."""
    await message.answer(_USE_BUTTONS_HINT)


# ============================================================
# Orchestration helpers
# ============================================================


async def _resume_test_screen(
    message: Message,
    state: FSMContext,
    attempt_state: AttemptState,
) -> None:
    """Set FSM to in-progress and (re)render the live test screen for an attempt."""
    await state.set_state(TestState.in_progress)
    await state.update_data(attempt_id=attempt_state.attempt_id)
    await _send_rendered(message, render_test_screen(attempt_state))


async def _enter_test_flow(
    *,
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    user: User,
    container: Container,
) -> None:
    """Shared entry routing for ``/test`` and the inline «Пройти тест» button.

    ``message`` is the message to reply against (the user's ``/test`` message,
    or the message the inline button was attached to). We send fresh messages
    here — never edit — so the resume path can deliver a photo question via
    :func:`_send_rendered` without fighting the text↔photo edit constraint.
    """
    if user.status != "approved":
        await message.answer(_NOT_APPROVED)
        return

    services = container.services(session)

    # Resume an open attempt first — even if a newer publish has since
    # archived its test, the student continues it to completion
    # (PRODUCT_BLUEPRINT §8.4/§13). Checking for an in-progress attempt on
    # *any* test (not just the active one) is what stops the archived
    # attempt from going invisible and the student from starting a second,
    # concurrent attempt on the new test (CODE_REVIEW C3).
    in_progress = await services.attempt.get_in_progress_attempt(user.id)
    if in_progress is not None:
        attempt_state = await services.attempt.get_state(in_progress.id, user_id=user.id)
        if attempt_state.status == "in_progress":
            await _resume_test_screen(message, state, attempt_state)
            return
        # Raced to a finished state between the two reads — fall through to
        # the normal active-test routing below.

    active_test = await services.test.get_active_test()
    if active_test is None:
        text = (await services.settings.get("msg_no_active_test")) or _FALLBACK_NO_ACTIVE_TEST
        await message.answer(text)
        return

    existing = await services.attempt.get_user_attempt_for_test(user.id, active_test.id)
    if existing is not None:
        # Any in-progress attempt was already handled above, so this one is
        # finished — show the prior result. (The in_progress branch is kept
        # as defensive cover for the microsecond resume race.)
        attempt_state = await services.attempt.get_state(existing.id, user_id=user.id)
        if attempt_state.status == "in_progress":
            await _resume_test_screen(message, state, attempt_state)
            return
        await state.clear()
        link = await services.settings.get("group_invite_link")
        marks = await services.attempt.get_question_marks(existing.id)
        rendered = render_prior_result_screen(
            existing,
            section_scores_from_attempt(existing),
            group_invite_link=link,
            marks=marks,
        )
        await _send_rendered(message, rendered)
        return

    # Fresh entry — show the pre-test confirmation screen.
    await _send_rendered(message, render_pretest_screen())


async def _resume_or_show_prior(
    callback: CallbackQuery,
    *,
    attempt_id: int,
    state: FSMContext,
    services: Services,
    user: User,
) -> None:
    """Handle AttemptAlreadyExistsError after the pre-test confirmation tap."""
    attempt_state = await services.attempt.get_state(attempt_id, user_id=user.id)
    if attempt_state.status == "in_progress":
        await state.set_state(TestState.in_progress)
        await state.update_data(attempt_id=attempt_id)
        rendered = render_test_screen(attempt_state)
    else:
        attempt = await services.attempt.get_attempt(attempt_id)
        if attempt is None:
            return
        await state.clear()
        link = await services.settings.get("group_invite_link")
        marks = await services.attempt.get_question_marks(attempt.id)
        rendered = render_prior_result_screen(
            attempt,
            section_scores_from_attempt(attempt),
            group_invite_link=link,
            marks=marks,
        )
    await _edit_or_answer(callback, rendered)


async def _show_finished(
    callback: CallbackQuery,
    *,
    services: Services,
    state: FSMContext,
    attempt_state: AttemptState,
    user: User,
) -> None:
    """Render the result screen when the user taps an old in-test button.

    The attempt was finalized between renders (timer expired, or the
    user finished from another window). Re-fetch the canonical row with an
    ownership check (CODE_REVIEW M4) and edit the message to the result.
    """
    attempt = await services.attempt.get_attempt_for_user(attempt_state.attempt_id, user.id)
    if attempt is None:
        return
    await state.clear()
    link = await services.settings.get("group_invite_link")
    marks = await services.attempt.get_question_marks(attempt.id)
    rendered = render_result_screen(
        attempt,
        section_scores_from_attempt(attempt),
        group_invite_link=link,
        marks=marks,
    )
    await _edit_or_answer(callback, rendered)


def _pick_next_position(state: AttemptState, *, just_answered_pos: int) -> int:
    """Advance to the next unanswered question after ``just_answered_pos``.

    Per PRODUCT_BLUEPRINT §8.5: "save answer, advance to next unanswered
    question (or next sequential if all later are answered, or stay if
    last)".
    """
    answered = _answered_positions(state) | {just_answered_pos}

    # Look forward from the just-answered position.
    for pos in range(just_answered_pos + 1, 51):
        if pos not in answered:
            return pos

    # Wrap around to find an earlier unanswered question.
    for pos in range(1, just_answered_pos):
        if pos not in answered:
            return pos

    # Everything is answered — stay where we are.
    return state.current_position


def _answered_positions(attempt_state: AttemptState) -> set[int]:
    pos_by_qid = {q.id: q.position for q in attempt_state.questions}
    return {pos_by_qid[qid] for qid in attempt_state.answers_by_question_id if qid in pos_by_qid}


# ============================================================
# Telegram-edit safety
# ============================================================


async def _edit_or_answer(callback: CallbackQuery, rendered: RenderedMessage) -> None:
    """Update the message attached to ``callback`` in place where possible.

    Telegram won't convert a text message into a photo message or vice versa,
    so the strategy depends on whether the *current* and *target* message types
    match:

    * **Same type** (text→text or photo→photo) — edit in place
      (``edit_text`` / ``edit_media``). "message is not modified" on a verbatim
      re-render (e.g. re-tapping the current question) is a no-op we swallow.
    * **Different type** (crossing the text↔photo boundary, e.g. moving from a
      plain question to an illustrated one, or to the text result screen) — we
      can't edit, so we delete the old message and send a fresh one. Deleting
      the last message in the chat and resending lands the new one in the same
      spot, so this is visually seamless in the common case.

    On any other edit failure we fall back to sending a fresh message rather
    than leaving the user staring at a stale screen.
    """
    message = callback.message
    if message is None:
        return

    target_is_photo = rendered.photo_file_id is not None
    current_is_photo = bool(message.photo)

    if target_is_photo == current_is_photo:
        try:
            if target_is_photo:
                await message.edit_media(
                    media=InputMediaPhoto(
                        media=rendered.photo_file_id,
                        caption=rendered.text,
                        parse_mode=rendered.parse_mode,
                    ),
                    reply_markup=rendered.reply_markup,
                )
            else:
                await message.edit_text(
                    text=rendered.text,
                    reply_markup=rendered.reply_markup,
                    parse_mode=rendered.parse_mode,
                )
            return
        except TelegramBadRequest as exc:
            if "message is not modified" in str(exc).lower():
                return
            logger.warning("edit_message_failed", error=str(exc))
    else:
        # Incompatible types — drop the old message before resending so we
        # don't leave a stale screen above the new one.
        with contextlib.suppress(TelegramAPIError):
            await message.delete()

    await _send_rendered(message, rendered)


async def _send_rendered(message: Message, rendered: RenderedMessage) -> None:
    """Send ``rendered`` as a fresh message — a photo when it carries a file id, else text."""
    if rendered.photo_file_id is not None:
        await message.answer_photo(
            photo=rendered.photo_file_id,
            caption=rendered.text,
            reply_markup=rendered.reply_markup,
            parse_mode=rendered.parse_mode,
        )
        return
    await message.answer(
        text=rendered.text,
        reply_markup=rendered.reply_markup,
        parse_mode=rendered.parse_mode,
    )
