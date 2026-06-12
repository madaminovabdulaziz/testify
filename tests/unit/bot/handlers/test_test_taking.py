"""Unit tests for the test-taking handlers.

Each test mocks the services bundle + FSM context so we exercise the
handler's branching logic without spinning up the DB, scheduler, or
aiogram event loop.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from aiogram.exceptions import TelegramBadRequest

from app.bot.callbacks.test import TestAnswerCD, TestFinishCD, TestNavCD
from app.bot.handlers.test_taking import (
    _edit_or_answer,
    _enter_test_flow,
    _pick_next_position,
    _send_rendered,
    cmd_test,
    on_confirm_start,
    on_finish_confirmed,
    on_finish_request,
    on_pretest_cancel,
    on_test_answer,
    on_test_nav,
)
from app.bot.states.test_taking import TestState
from app.bot.views import RenderedMessage
from app.exceptions import AttemptAlreadyExistsError
from app.services.attempt_service import AttemptResult, AttemptState
from app.services.scoring_service import SectionScores

# ---------- helpers ----------


def _user(status: str = "approved") -> SimpleNamespace:
    return SimpleNamespace(id=7, telegram_id=12345, username="alice", status=status)


def _question(qid: int, position: int) -> SimpleNamespace:
    section = "rus_tili" if position <= 35 else "pedagogik" if position <= 45 else "kasbiy"
    return SimpleNamespace(
        id=qid,
        position=position,
        section=section,
        question_text=f"Q{position}",
        option_a="a",
        option_b="b",
        option_c="c",
        option_d="d",
        correct_option="A",
    )


def _answer(qid: int) -> SimpleNamespace:
    return SimpleNamespace(question_id=qid, selected_option="A", is_correct=True)


def _attempt_row(**overrides) -> SimpleNamespace:
    base = {
        "id": 42,
        "user_id": 7,
        "test_id": 3,
        "status": "in_progress",
        "current_position": 1,
        "started_at": datetime(2026, 5, 24, 10, 0, tzinfo=UTC),
        "finished_at": None,
        "expires_at": datetime(2026, 5, 24, 10, 53, 20, tzinfo=UTC),
        "score_total_correct": None,
        "score_rus_tili_correct": None,
        "score_pedagogik_correct": None,
        "score_kasbiy_correct": None,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _state_dto(
    *,
    status: str = "in_progress",
    current_position: int = 1,
    answered_qids: tuple[int, ...] = (),
    attempt_id: int = 42,
    test_id: int = 3,
) -> AttemptState:
    questions = tuple(_question(qid=q, position=q) for q in range(1, 51))
    return AttemptState(
        attempt_id=attempt_id,
        user_id=7,
        test_id=test_id,
        status=status,
        current_position=current_position,
        started_at=datetime(2026, 5, 24, 10, 0, tzinfo=UTC),
        expires_at=datetime(2026, 5, 24, 10, 53, 20, tzinfo=UTC),
        time_remaining_seconds=3200,
        questions=questions,
        answers_by_question_id={qid: _answer(qid) for qid in answered_qids},  # type: ignore[arg-type]
    )


_DEFAULT_ACTIVE_TEST = SimpleNamespace(id=3, title="T", duration_seconds=3200)
_DEFAULT_TEST_SENTINEL = object()


def _services_bundle(
    *,
    active_test: object = _DEFAULT_TEST_SENTINEL,
    existing_attempt: SimpleNamespace | None = None,
    state_dto: AttemptState | None = None,
    finish_result: AttemptResult | None = None,
    start_raises: Exception | None = None,
    invite_link: str | None = None,
) -> MagicMock:
    resolved_active_test = (
        _DEFAULT_ACTIVE_TEST if active_test is _DEFAULT_TEST_SENTINEL else active_test
    )
    services = MagicMock()
    services.test.get_active_test = AsyncMock(return_value=resolved_active_test)

    services.attempt.get_user_attempt_for_test = AsyncMock(return_value=existing_attempt)
    # The entry flow now checks for an open attempt on *any* test first
    # (CODE_REVIEW C3). In these single-test fixtures the open attempt, if
    # any, is the existing one; the archived-test case overrides this mock.
    in_progress_attempt = (
        existing_attempt
        if existing_attempt is not None
        and getattr(existing_attempt, "status", None) == "in_progress"
        else None
    )
    services.attempt.get_in_progress_attempt = AsyncMock(return_value=in_progress_attempt)
    services.attempt.get_attempt = AsyncMock(return_value=existing_attempt)
    services.attempt.get_attempt_for_user = AsyncMock(return_value=existing_attempt)
    services.attempt.get_state = AsyncMock(return_value=state_dto)
    services.attempt.submit_answer = AsyncMock()
    services.attempt.set_current_position = AsyncMock()

    if start_raises is not None:
        services.attempt.start = AsyncMock(side_effect=start_raises)
    else:
        services.attempt.start = AsyncMock(return_value=_attempt_row())

    services.attempt.finish = AsyncMock(return_value=finish_result)
    services.attempt.get_question_marks = AsyncMock(return_value={})

    async def fake_setting(key: str) -> str | None:
        return {
            "group_invite_link": invite_link,
            "msg_no_active_test": None,
        }.get(key)

    services.settings.get = AsyncMock(side_effect=fake_setting)
    return services


def _container(services: MagicMock) -> MagicMock:
    container = MagicMock()
    container.services = MagicMock(return_value=services)
    return container


def _callback() -> MagicMock:
    cb = MagicMock()
    cb.from_user = SimpleNamespace(id=12345, username="alice")
    cb.answer = AsyncMock()
    cb.message = MagicMock()
    # photo=None marks the attached message as a *text* message — the transport
    # layer branches on this to decide edit-in-place vs delete+resend.
    cb.message.photo = None
    cb.message.edit_text = AsyncMock()
    cb.message.edit_media = AsyncMock()
    cb.message.answer = AsyncMock()
    cb.message.answer_photo = AsyncMock()
    cb.message.delete = AsyncMock()
    return cb


def _capturing_message() -> MagicMock:
    """A stand-in for the ``Message`` passed into the entry flow.

    The entry flow only ever sends fresh messages (text via ``answer``, photo
    via ``answer_photo``), so we capture both.
    """
    msg = MagicMock()
    msg.answer = AsyncMock()
    msg.answer_photo = AsyncMock()
    return msg


def _answer_texts(msg: MagicMock) -> list[str]:
    """The text of every ``message.answer`` call (positional or ``text=`` kwarg)."""
    texts: list[str] = []
    for call in msg.answer.await_args_list:
        if call.args:
            texts.append(call.args[0])
        elif "text" in call.kwargs:
            texts.append(call.kwargs["text"])
    return texts


# ============================================================
# pure helpers
# ============================================================


def test_pick_next_position_advances_to_next_unanswered() -> None:
    # Positions 1–4 + 6 answered; user is on 6 and just answered it. Next
    # unanswered is position 5 only after wraparound — first unanswered
    # ahead is 7.
    state = _state_dto(current_position=6, answered_qids=(1, 2, 3, 4))
    assert _pick_next_position(state, just_answered_pos=6) == 7


def test_pick_next_position_skips_already_answered_positions() -> None:
    # User on 4, just answered it. 5 + 6 are already answered (e.g. via
    # grid jumps); next unanswered ahead is 7.
    state = _state_dto(current_position=4, answered_qids=(5, 6))
    assert _pick_next_position(state, just_answered_pos=4) == 7


def test_pick_next_position_wraps_to_earlier_unanswered() -> None:
    # All later positions are answered; expect wrap to first unanswered (which is 3)
    answered = tuple(qid for qid in range(1, 51) if qid not in (3, 7))
    state = _state_dto(current_position=10, answered_qids=answered)
    assert _pick_next_position(state, just_answered_pos=10) == 3


def test_pick_next_position_stays_when_all_answered() -> None:
    state = _state_dto(current_position=25, answered_qids=tuple(range(1, 51)))
    assert _pick_next_position(state, just_answered_pos=25) == 25


# ============================================================
# /test and entry flow
# ============================================================


async def test_cmd_test_not_approved_replies_with_payment_hint() -> None:
    message = MagicMock()
    message.answer = AsyncMock()
    container = _container(_services_bundle())
    await cmd_test(
        message,
        state=MagicMock(),
        session=MagicMock(),
        user=_user(status="pending_payment"),
        container=container,
    )
    message.answer.assert_awaited_once()
    args, _ = message.answer.await_args
    assert "оплатить" in args[0]


async def test_enter_flow_no_active_test_replies_with_settings_text() -> None:
    msg = _capturing_message()
    services = _services_bundle(active_test=None)
    container = _container(services)
    await _enter_test_flow(
        message=msg,
        state=MagicMock(),
        session=MagicMock(),
        user=_user(),
        container=container,
    )
    assert msg.answer.await_count >= 1, "no reply was sent"


async def test_enter_flow_fresh_user_shows_pretest_screen() -> None:
    msg = _capturing_message()
    services = _services_bundle(existing_attempt=None)
    container = _container(services)
    await _enter_test_flow(
        message=msg,
        state=MagicMock(),
        session=MagicMock(),
        user=_user(),
        container=container,
    )
    assert any("Тест готов" in t for t in _answer_texts(msg))


async def test_enter_flow_already_finished_shows_prior_result() -> None:
    msg = _capturing_message()
    existing = _attempt_row(
        status="submitted",
        finished_at=datetime(2026, 5, 24, 10, 30, tzinfo=UTC),
        score_total_correct=42,
        score_rus_tili_correct=30,
        score_pedagogik_correct=8,
        score_kasbiy_correct=4,
    )
    state_dto = _state_dto(status="submitted")
    services = _services_bundle(existing_attempt=existing, state_dto=state_dto)
    container = _container(services)
    await _enter_test_flow(
        message=msg,
        state=MagicMock(clear=AsyncMock(), set_state=AsyncMock(), update_data=AsyncMock()),
        session=MagicMock(),
        user=_user(),
        container=container,
    )
    assert any("уже проходили" in t for t in _answer_texts(msg))


async def test_enter_flow_in_progress_resumes_with_test_screen() -> None:
    msg = _capturing_message()
    existing = _attempt_row(status="in_progress")
    state_dto = _state_dto(status="in_progress", current_position=5, answered_qids=(1, 2))
    services = _services_bundle(existing_attempt=existing, state_dto=state_dto)
    container = _container(services)
    fsm = MagicMock(clear=AsyncMock(), set_state=AsyncMock(), update_data=AsyncMock())
    await _enter_test_flow(
        message=msg,
        state=fsm,
        session=MagicMock(),
        user=_user(),
        container=container,
    )
    fsm.set_state.assert_awaited_once_with(TestState.in_progress)
    fsm.update_data.assert_awaited_once_with(attempt_id=existing.id)
    assert any("Вопрос 5/50" in t for t in _answer_texts(msg))


async def test_enter_flow_resumes_open_attempt_on_archived_test() -> None:
    # CODE_REVIEW C3: the student's open attempt is on a test that has since
    # been archived (a different test is now active and they have no attempt
    # on it). They must resume the old attempt — not see a pre-test screen
    # for the new active test, and not start a second concurrent attempt.
    msg = _capturing_message()
    archived_attempt = _attempt_row(id=99, test_id=2, status="in_progress")
    state_dto = _state_dto(
        status="in_progress",
        current_position=5,
        answered_qids=(1, 2),
        attempt_id=99,
        test_id=2,
    )
    services = _services_bundle(existing_attempt=None, state_dto=state_dto)
    # No attempt on the *active* test, but an open one on the archived test.
    services.attempt.get_user_attempt_for_test = AsyncMock(return_value=None)
    services.attempt.get_in_progress_attempt = AsyncMock(return_value=archived_attempt)
    container = _container(services)
    fsm = MagicMock(clear=AsyncMock(), set_state=AsyncMock(), update_data=AsyncMock())

    await _enter_test_flow(
        message=msg,
        state=fsm,
        session=MagicMock(),
        user=_user(),
        container=container,
    )

    fsm.set_state.assert_awaited_once_with(TestState.in_progress)
    fsm.update_data.assert_awaited_once_with(attempt_id=99)
    texts = _answer_texts(msg)
    assert any("Вопрос 5/50" in t for t in texts)
    assert not any("Тест готов" in t for t in texts)
    services.attempt.start.assert_not_called()


# ============================================================
# pretest confirm / cancel
# ============================================================


async def test_on_confirm_start_creates_attempt_and_renders_test_screen() -> None:
    callback = _callback()
    state = MagicMock(set_state=AsyncMock(), update_data=AsyncMock(), clear=AsyncMock())
    services = _services_bundle(
        existing_attempt=None,
        state_dto=_state_dto(status="in_progress"),
    )
    container = _container(services)
    await on_confirm_start(
        callback,
        state=state,
        session=MagicMock(),
        user=_user(),
        container=container,
    )
    services.attempt.start.assert_awaited_once()
    state.set_state.assert_awaited_once_with(TestState.in_progress)
    callback.message.edit_text.assert_awaited_once()


async def test_on_confirm_start_already_exists_in_progress_resumes() -> None:
    callback = _callback()
    state = MagicMock(set_state=AsyncMock(), update_data=AsyncMock(), clear=AsyncMock())
    services = _services_bundle(
        existing_attempt=_attempt_row(),
        state_dto=_state_dto(status="in_progress"),
        start_raises=AttemptAlreadyExistsError(42),
    )
    container = _container(services)
    await on_confirm_start(
        callback,
        state=state,
        session=MagicMock(),
        user=_user(),
        container=container,
    )
    state.set_state.assert_awaited_with(TestState.in_progress)
    callback.message.edit_text.assert_awaited_once()


async def test_on_pretest_cancel_clears_state_and_edits_message() -> None:
    callback = _callback()
    state = MagicMock(clear=AsyncMock())
    await on_pretest_cancel(callback, state=state)
    state.clear.assert_awaited_once()
    callback.message.edit_text.assert_awaited_once()


# ============================================================
# Answer / Nav callbacks
# ============================================================


async def test_on_test_answer_persists_answer_and_advances_cursor() -> None:
    callback = _callback()
    services = _services_bundle(state_dto=_state_dto(current_position=5))
    # First get_state call: current_position=5; second (post-advance): 6.
    after_advance = _state_dto(current_position=6, answered_qids=(5,))
    services.attempt.get_state = AsyncMock(
        side_effect=[
            _state_dto(current_position=5, answered_qids=(5,)),  # post-submit read
            after_advance,  # post-set_current_position read
        ]
    )
    container = _container(services)
    state = MagicMock()
    await on_test_answer(
        callback,
        callback_data=TestAnswerCD(attempt_id=42, question_pos=5, option="A"),
        state=state,
        session=MagicMock(),
        user=_user(),
        container=container,
    )
    services.attempt.submit_answer.assert_awaited_once_with(
        42, user_id=7, question_position=5, selected_option="A"
    )
    services.attempt.set_current_position.assert_awaited_once_with(42, user_id=7, position=6)
    callback.message.edit_text.assert_awaited_once()


async def test_on_test_answer_when_attempt_already_finished_shows_result() -> None:
    callback = _callback()
    finished_state = _state_dto(status="expired", answered_qids=(1,))
    services = _services_bundle(
        state_dto=finished_state,
        existing_attempt=_attempt_row(
            status="expired",
            finished_at=datetime(2026, 5, 24, 10, 53, tzinfo=UTC),
            score_total_correct=1,
            score_rus_tili_correct=1,
            score_pedagogik_correct=0,
            score_kasbiy_correct=0,
        ),
    )
    services.attempt.get_state = AsyncMock(return_value=finished_state)
    container = _container(services)
    state = MagicMock(clear=AsyncMock())
    await on_test_answer(
        callback,
        callback_data=TestAnswerCD(attempt_id=42, question_pos=1, option="A"),
        state=state,
        session=MagicMock(),
        user=_user(),
        container=container,
    )
    state.clear.assert_awaited_once()
    callback.message.edit_text.assert_awaited_once()


async def test_on_test_nav_persists_position_and_renders() -> None:
    callback = _callback()
    before = _state_dto(current_position=1)
    after = _state_dto(current_position=7)
    services = _services_bundle()
    services.attempt.get_state = AsyncMock(side_effect=[before, after])
    container = _container(services)
    await on_test_nav(
        callback,
        callback_data=TestNavCD(attempt_id=42, target_pos=7),
        state=MagicMock(),
        session=MagicMock(),
        user=_user(),
        container=container,
    )
    services.attempt.set_current_position.assert_awaited_once_with(42, user_id=7, position=7)


async def test_on_test_nav_ignores_target_pos_zero() -> None:
    callback = _callback()
    services = _services_bundle(state_dto=_state_dto(current_position=5))
    container = _container(services)
    await on_test_nav(
        callback,
        callback_data=TestNavCD(attempt_id=42, target_pos=0),
        state=MagicMock(),
        session=MagicMock(),
        user=_user(),
        container=container,
    )
    services.attempt.set_current_position.assert_not_awaited()


# ============================================================
# Finish confirmation
# ============================================================


async def test_on_finish_request_shows_dialog_with_counts() -> None:
    callback = _callback()
    state = MagicMock(set_state=AsyncMock(), update_data=AsyncMock())
    services = _services_bundle(
        state_dto=_state_dto(current_position=10, answered_qids=tuple(range(1, 13)))
    )
    container = _container(services)
    await on_finish_request(
        callback,
        callback_data=TestFinishCD(attempt_id=42, confirmed=False),
        state=state,
        session=MagicMock(),
        user=_user(),
        container=container,
    )
    state.set_state.assert_awaited_once_with(TestState.confirming_finish)
    # The rendered text should mention "12 из 50"
    _, kwargs = callback.message.edit_text.await_args
    assert "12 из 50" in kwargs["text"]


async def test_on_finish_confirmed_finalizes_attempt_and_renders_result() -> None:
    callback = _callback()
    state = MagicMock(clear=AsyncMock())

    attempt = _attempt_row(
        status="submitted",
        finished_at=datetime(2026, 5, 24, 10, 30, tzinfo=UTC),
        score_total_correct=37,
        score_rus_tili_correct=28,
        score_pedagogik_correct=6,
        score_kasbiy_correct=3,
    )
    scores = SectionScores(rus_tili=28, pedagogik=6, kasbiy=3, total=37)
    result = AttemptResult(attempt=attempt, scores=scores)

    services = _services_bundle(
        state_dto=_state_dto(current_position=10),
        finish_result=result,
        invite_link="https://t.me/+abc",
    )
    container = _container(services)

    await on_finish_confirmed(
        callback,
        callback_data=TestFinishCD(attempt_id=42, confirmed=True),
        state=state,
        session=MagicMock(),
        user=_user(),
        container=container,
    )
    services.attempt.finish.assert_awaited_once_with(42, reason="user")
    state.clear.assert_awaited_once()
    _, kwargs = callback.message.edit_text.await_args
    assert "37/50" in kwargs["text"]


# ============================================================
# _edit_or_answer swallow behavior
# ============================================================


async def test_edit_or_answer_swallows_message_not_modified() -> None:
    callback = _callback()
    callback.message.edit_text = AsyncMock(
        side_effect=TelegramBadRequest(
            method=MagicMock(), message="Bad Request: message is not modified"
        )
    )
    callback.message.answer = AsyncMock()
    await _edit_or_answer(callback, RenderedMessage(text="x"))
    # No fallback message should have been sent for "not modified".
    callback.message.answer.assert_not_awaited()


async def test_edit_or_answer_falls_back_to_answer_on_other_bad_request() -> None:
    callback = _callback()
    callback.message.edit_text = AsyncMock(
        side_effect=TelegramBadRequest(method=MagicMock(), message="Bad Request: chat not found")
    )
    callback.message.answer = AsyncMock()
    await _edit_or_answer(callback, RenderedMessage(text="x"))
    callback.message.answer.assert_awaited_once()


# ============================================================
# Photo / text transport (illustrated questions)
# ============================================================


async def test_send_rendered_sends_photo_when_file_id_present() -> None:
    msg = _capturing_message()
    await _send_rendered(msg, RenderedMessage(text="caption", photo_file_id="FID9"))
    msg.answer_photo.assert_awaited_once()
    _, kwargs = msg.answer_photo.await_args
    assert kwargs["photo"] == "FID9"
    assert kwargs["caption"] == "caption"
    msg.answer.assert_not_awaited()


async def test_send_rendered_sends_text_when_no_file_id() -> None:
    msg = _capturing_message()
    await _send_rendered(msg, RenderedMessage(text="hello"))
    msg.answer.assert_awaited_once()
    msg.answer_photo.assert_not_awaited()


async def test_edit_or_answer_edits_media_photo_to_photo() -> None:
    callback = _callback()
    callback.message.photo = [SimpleNamespace(file_id="x")]  # current message is a photo
    await _edit_or_answer(callback, RenderedMessage(text="cap", photo_file_id="FID9"))
    callback.message.edit_media.assert_awaited_once()
    callback.message.edit_text.assert_not_awaited()
    callback.message.delete.assert_not_awaited()


async def test_edit_or_answer_deletes_and_resends_crossing_text_to_photo() -> None:
    callback = _callback()  # current message is text (photo=None)
    await _edit_or_answer(callback, RenderedMessage(text="cap", photo_file_id="FID9"))
    # Can't convert text→photo in place: drop the old message, send a new photo.
    callback.message.delete.assert_awaited_once()
    callback.message.answer_photo.assert_awaited_once()
    callback.message.edit_text.assert_not_awaited()
    callback.message.edit_media.assert_not_awaited()


async def test_edit_or_answer_deletes_and_resends_crossing_photo_to_text() -> None:
    callback = _callback()
    callback.message.photo = [SimpleNamespace(file_id="x")]  # current message is a photo
    await _edit_or_answer(callback, RenderedMessage(text="result text"))
    callback.message.delete.assert_awaited_once()
    callback.message.answer.assert_awaited_once()
    callback.message.edit_media.assert_not_awaited()
