"""Unit tests for the /admin reply-keyboard panel handlers."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from app.bot.handlers.admin.panel import (
    cmd_admin_panel,
    panel_ban_run,
    panel_cancel,
    panel_close,
    panel_find_run,
    panel_find_start,
    panel_stats,
)
from app.bot.states.admin import AdminPanelState
from app.services.stats_service import StatsSnapshot


def _admin_user() -> SimpleNamespace:
    return SimpleNamespace(id=1, status="approved")


def _message(*, text: str = "", from_id: int = 900) -> MagicMock:
    msg = MagicMock()
    msg.answer = AsyncMock()
    msg.text = text
    msg.from_user = SimpleNamespace(id=from_id, username="admin")
    return msg


def _state() -> MagicMock:
    state = MagicMock()
    state.clear = AsyncMock()
    state.set_state = AsyncMock()
    return state


def _container(services: MagicMock) -> MagicMock:
    container = MagicMock()
    container.services = MagicMock(return_value=services)
    return container


# ============================================================
# /admin opens the panel + close removes it
# ============================================================


async def test_admin_command_opens_panel_with_keyboard() -> None:
    msg = _message()
    state = _state()
    await cmd_admin_panel(msg, state=state)
    state.clear.assert_awaited_once()
    msg.answer.assert_awaited_once()
    kwargs = msg.answer.await_args.kwargs
    assert kwargs.get("reply_markup") is not None  # keyboard attached
    text = msg.answer.await_args.args[0]
    assert "Админ-панель" in text


async def test_close_panel_clears_state_and_removes_keyboard() -> None:
    msg = _message()
    state = _state()
    await panel_close(msg, state=state)
    state.clear.assert_awaited_once()
    msg.answer.assert_awaited_once()
    # ReplyKeyboardRemove is the marker — verifying by the presence of
    # the reply_markup field is enough.
    assert msg.answer.await_args.kwargs.get("reply_markup") is not None


# ============================================================
# Zero-arg button (stats) goes straight through
# ============================================================


async def test_panel_stats_renders_snapshot() -> None:
    msg = _message(text="📊 Статистика")
    services = MagicMock()
    services.stats.snapshot = AsyncMock(
        return_value=StatsSnapshot(
            total_users=10,
            users_by_status={"approved": 7},
            receipts_by_status={"pending": 1},
            tests_by_status={"active": 1},
            attempts_by_status={"submitted": 3, "expired": 1},
        )
    )
    container = _container(services)

    await panel_stats(msg, session=MagicMock(), user=_admin_user(), container=container)

    services.stats.snapshot.assert_awaited_once()
    msg.answer.assert_awaited_once()
    text = msg.answer.await_args.args[0]
    assert "Статистика" in text
    assert "10" in text


# ============================================================
# Multi-step: find — start prompts + sets state, then run executes
# ============================================================


async def test_find_start_sets_waiting_state_and_prompts() -> None:
    msg = _message(text="🔍 Найти ученика")
    state = _state()
    await panel_find_start(msg, state=state)
    state.set_state.assert_awaited_once_with(AdminPanelState.waiting_for_find_query)
    msg.answer.assert_awaited_once()
    # The prompt swaps to the cancel-only keyboard.
    assert msg.answer.await_args.kwargs.get("reply_markup") is not None


async def test_find_run_renders_user_card_on_match() -> None:
    msg = _message(text="alice")
    state = _state()
    services = MagicMock()
    services.user.find = AsyncMock(
        return_value=SimpleNamespace(
            id=7,
            telegram_id=12345,
            username="alice",
            full_name="Alice Smith",
            phone="+99890",
            reference_code="ABC123",
            status="approved",
            bot_blocked=False,
        )
    )
    services.receipt.count_pending_for_user = AsyncMock(return_value=2)
    container = _container(services)

    await panel_find_run(
        msg,
        state=state,
        session=MagicMock(),
        user=_admin_user(),
        container=container,
    )

    services.user.find.assert_awaited_once_with("alice")
    state.clear.assert_awaited_once()
    text = msg.answer.await_args.args[0]
    assert "Alice Smith" in text
    # After the result, the keyboard is restored to the full admin panel.
    kb = msg.answer.await_args.kwargs.get("reply_markup")
    assert kb is not None and len(kb.keyboard) == 4


async def test_find_run_handles_missing_user() -> None:
    msg = _message(text="nobody")
    state = _state()
    services = MagicMock()
    services.user.find = AsyncMock(return_value=None)
    container = _container(services)
    await panel_find_run(
        msg,
        state=state,
        session=MagicMock(),
        user=_admin_user(),
        container=container,
    )
    state.clear.assert_awaited_once()
    assert "не найден" in msg.answer.await_args.args[0].lower()


# ============================================================
# Multi-step: ban — invalid int re-prompts, valid clears + bans
# ============================================================


async def test_ban_run_rejects_non_integer_without_clearing_state() -> None:
    msg = _message(text="not-a-number")
    state = _state()
    container = _container(MagicMock())
    await panel_ban_run(
        msg,
        state=state,
        session=MagicMock(),
        user=_admin_user(),
        container=container,
    )
    # Stay in state so the admin can retry without re-tapping the button.
    state.clear.assert_not_awaited()
    text = msg.answer.await_args.args[0]
    assert "целое число" in text


async def test_ban_run_bans_when_user_exists() -> None:
    msg = _message(text="7")
    state = _state()
    services = MagicMock()
    services.user.get_user = AsyncMock(return_value=SimpleNamespace(id=7, status="approved"))
    services.user.ban = AsyncMock()
    container = _container(services)
    await panel_ban_run(
        msg,
        state=state,
        session=MagicMock(),
        user=_admin_user(),
        container=container,
    )
    state.clear.assert_awaited_once()
    services.user.ban.assert_awaited_once_with(7)
    assert "забанен" in msg.answer.await_args.args[0]


# ============================================================
# Global cancel returns to the panel
# ============================================================


async def test_panel_cancel_clears_state_and_re_shows_panel() -> None:
    msg = _message(text="↩️ Отменить")
    state = _state()
    await panel_cancel(msg, state=state)
    state.clear.assert_awaited_once()
    text = msg.answer.await_args.args[0]
    assert "отменено" in text.lower()
    kb = msg.answer.await_args.kwargs.get("reply_markup")
    assert kb is not None and len(kb.keyboard) == 4
