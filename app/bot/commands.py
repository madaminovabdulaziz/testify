"""Telegram command-menu registration (``setMyCommands``).

Students see only the student commands in every private chat (default
scope). Registered admins additionally see the admin commands — both in
their own DM with the bot (per-chat scope on their ``telegram_id``) and
in the admin group.

Called once at startup, best-effort: a Telegram hiccup here must not
prevent the bot from serving updates. Admins added after startup get
their menu on the next restart (typing the commands works regardless).
"""

from __future__ import annotations

import structlog
from aiogram import Bot
from aiogram.types import (
    BotCommand,
    BotCommandScopeAllPrivateChats,
    BotCommandScopeChat,
)

from app.core.container import Container

logger = structlog.get_logger()

STUDENT_COMMANDS = [
    BotCommand(command="start", description="Запустить бота / регистрация"),
    BotCommand(command="test", description="Пройти тест"),
    BotCommand(command="chat", description="Ссылка на чат студентов"),
    BotCommand(command="help", description="Помощь"),
]

ADMIN_COMMANDS = [
    BotCommand(command="admin", description="Панель администратора"),
    BotCommand(command="upload_test", description="Загрузить новый тест (Excel)"),
    BotCommand(command="template", description="Получить шаблон Excel"),
    BotCommand(command="cancel", description="Прервать загрузку теста"),
    BotCommand(command="stats", description="Общая статистика"),
    BotCommand(command="tests", description="Список тестов"),
    BotCommand(command="leaderboard", description="Топ результатов теста"),
    BotCommand(command="find", description="Найти студента"),
    BotCommand(command="attempt", description="Детали попытки"),
    BotCommand(command="ban", description="Заблокировать пользователя"),
    BotCommand(command="unban", description="Разблокировать пользователя"),
    BotCommand(command="settings", description="Показать настройки"),
    BotCommand(command="set", description="Изменить настройку"),
    BotCommand(command="preview", description="Предпросмотр сообщения"),
]


async def setup_bot_commands(container: Container) -> None:
    """Register the command menus with Telegram (best-effort, never raises)."""
    bot: Bot = container.bot

    # 1. Students: every private chat gets the short student menu.
    try:
        await bot.set_my_commands(STUDENT_COMMANDS, scope=BotCommandScopeAllPrivateChats())
    except Exception:
        logger.exception("set_commands_failed", scope="all_private_chats")

    admin_menu = STUDENT_COMMANDS + ADMIN_COMMANDS

    # 2. The admin group: admin commands work there (PRODUCT_BLUEPRINT §14.3).
    try:
        await bot.set_my_commands(
            ADMIN_COMMANDS,
            scope=BotCommandScopeChat(chat_id=container.settings.admin_group_id),
        )
    except Exception:
        # Group unreachable is already reported loudly by _check_admin_group.
        logger.warning("set_commands_failed", scope="admin_group")

    # 3. Each registered admin's DM: student + admin commands combined.
    #    Per-chat scope overrides the all-private-chats scope for them.
    async with container.session_factory() as session:
        admins = await container.services(session).admin.list_all()
    for admin in admins:
        try:
            await bot.set_my_commands(
                admin_menu, scope=BotCommandScopeChat(chat_id=admin.telegram_id)
            )
        except Exception:
            # Telegram rejects the scope until the admin has /start-ed the
            # bot at least once — expected for freshly seeded admins.
            logger.warning("set_commands_failed", scope="admin_dm", admin_id=admin.id)

    logger.info("bot_commands_registered", admin_count=len(admins))
