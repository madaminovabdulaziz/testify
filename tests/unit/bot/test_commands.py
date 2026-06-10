"""Unit tests for Telegram command-menu registration."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from aiogram.types import BotCommandScopeAllPrivateChats, BotCommandScopeChat

from app.bot.commands import ADMIN_COMMANDS, STUDENT_COMMANDS, setup_bot_commands


def _container(admins: list[SimpleNamespace]) -> MagicMock:
    container = MagicMock()
    container.bot.set_my_commands = AsyncMock()
    container.settings.admin_group_id = -1001

    session_cm = MagicMock()
    session_cm.__aenter__ = AsyncMock(return_value=MagicMock())
    session_cm.__aexit__ = AsyncMock(return_value=False)
    container.session_factory = MagicMock(return_value=session_cm)

    services = MagicMock()
    services.admin.list_all = AsyncMock(return_value=admins)
    container.services = MagicMock(return_value=services)
    return container


async def test_registers_student_admin_group_and_per_admin_menus() -> None:
    admins = [
        SimpleNamespace(id=1, telegram_id=111),
        SimpleNamespace(id=2, telegram_id=222),
    ]
    container = _container(admins)

    await setup_bot_commands(container)

    calls = container.bot.set_my_commands.await_args_list
    assert len(calls) == 4  # default + admin group + 2 admin DMs

    # Default scope: students get only the short menu.
    commands, scope = calls[0].args[0], calls[0].kwargs["scope"]
    assert commands == STUDENT_COMMANDS
    assert isinstance(scope, BotCommandScopeAllPrivateChats)

    # Admin group gets the admin commands.
    commands, scope = calls[1].args[0], calls[1].kwargs["scope"]
    assert commands == ADMIN_COMMANDS
    assert isinstance(scope, BotCommandScopeChat)
    assert scope.chat_id == -1001

    # Each admin DM gets student + admin commands.
    for call, admin in zip(calls[2:], admins, strict=True):
        commands, scope = call.args[0], call.kwargs["scope"]
        assert commands == STUDENT_COMMANDS + ADMIN_COMMANDS
        assert isinstance(scope, BotCommandScopeChat)
        assert scope.chat_id == admin.telegram_id


async def test_telegram_errors_do_not_break_startup() -> None:
    container = _container([SimpleNamespace(id=1, telegram_id=111)])
    container.bot.set_my_commands = AsyncMock(side_effect=Exception("flood"))

    await setup_bot_commands(container)  # must not raise

    # All scopes were still attempted despite every call failing.
    assert container.bot.set_my_commands.await_count == 3


async def test_cancel_command_is_in_admin_menu() -> None:
    assert any(c.command == "cancel" for c in ADMIN_COMMANDS)
    assert not any(c.command == "cancel" for c in STUDENT_COMMANDS)
