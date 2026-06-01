"""Unit tests for the menu-button handlers in common.py."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app.bot.handlers.common import cmd_chatid, cmd_help, cmd_history, cmd_start
from app.bot.states.test_taking import TestState


def _container_with_history(entries) -> MagicMock:
    services = MagicMock()
    services.attempt.list_finished_for_user = AsyncMock(return_value=entries)
    container = MagicMock()
    container.services = MagicMock(return_value=services)
    return container


def _approved_user() -> SimpleNamespace:
    return SimpleNamespace(id=7, telegram_id=12345, status="approved")


def _message() -> MagicMock:
    msg = MagicMock()
    msg.answer = AsyncMock()
    msg.chat = SimpleNamespace(id=-100123, type="supergroup", title="My Group")
    return msg


# ---------- cmd_start: H5 mid-test resume ----------


def _start_container() -> MagicMock:
    services = MagicMock()
    services.settings.get = AsyncMock(return_value=None)
    container = MagicMock()
    container.services = MagicMock(return_value=services)
    return container


async def test_start_resumes_test_when_fsm_in_progress() -> None:
    msg = _message()
    state = MagicMock()
    state.get_state = AsyncMock(return_value=TestState.in_progress.state)
    state.clear = AsyncMock()

    with patch("app.bot.handlers.common._enter_test_flow", new=AsyncMock()) as enter:
        await cmd_start(
            msg,
            state=state,
            session=MagicMock(),
            user=_approved_user(),
            container=_start_container(),
        )

    # Mid-test /start resumes via the test flow and does NOT clear the FSM
    # or show the menu (CODE_REVIEW H5).
    enter.assert_awaited_once()
    state.clear.assert_not_awaited()


async def test_start_shows_menu_when_not_mid_test() -> None:
    msg = _message()
    state = MagicMock()
    state.get_state = AsyncMock(return_value=None)
    state.clear = AsyncMock()

    with patch("app.bot.handlers.common._enter_test_flow", new=AsyncMock()) as enter:
        await cmd_start(
            msg,
            state=state,
            session=MagicMock(),
            user=_approved_user(),
            container=_start_container(),
        )

    enter.assert_not_awaited()
    state.clear.assert_awaited_once()
    msg.answer.assert_awaited_once()


# ---------- cmd_help ----------


async def test_help_replies_with_help_text() -> None:
    msg = _message()
    await cmd_help(msg)
    msg.answer.assert_awaited_once()
    text = msg.answer.await_args.args[0]
    assert "Пройти тест" in text
    assert "Мои результаты" in text


# ---------- cmd_history ----------


async def test_history_blocked_for_non_approved_users() -> None:
    msg = _message()
    user = SimpleNamespace(id=7, status="pending_payment")
    container = _container_with_history([])
    await cmd_history(msg, session=MagicMock(), user=user, container=container)
    msg.answer.assert_awaited_once()
    text = msg.answer.await_args.args[0]
    assert "одобрения" in text  # N8: clearer message mentions approval
    container.services.return_value.attempt.list_finished_for_user.assert_not_awaited()


async def test_history_empty_state_for_approved_user() -> None:
    msg = _message()
    container = _container_with_history([])
    await cmd_history(msg, session=MagicMock(), user=_approved_user(), container=container)
    msg.answer.assert_awaited_once()
    text = msg.answer.await_args.args[0]
    assert "пока нет завершённых попыток" in text


async def test_history_lists_attempts() -> None:
    msg = _message()
    attempt = SimpleNamespace(
        status="submitted",
        score_total_correct=42,
        finished_at=datetime(2026, 5, 24, 10, 30, tzinfo=UTC),
    )
    test = SimpleNamespace(title="Тест 1")
    container = _container_with_history([(attempt, test)])

    await cmd_history(msg, session=MagicMock(), user=_approved_user(), container=container)

    text = msg.answer.await_args.args[0]
    assert "Тест 1" in text
    assert "42/50" in text


# ---------- /chatid (untouched but lives in the same module) ----------


async def test_chatid_reports_chat_id() -> None:
    msg = _message()
    await cmd_chatid(msg)
    msg.answer.assert_awaited_once()
    text = msg.answer.await_args.args[0]
    assert "-100123" in text
    assert "supergroup" in text
