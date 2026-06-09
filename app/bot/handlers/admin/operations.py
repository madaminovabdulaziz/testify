"""Admin operations: /stats /find /ban /unban /leaderboard /attempt.

PRODUCT_BLUEPRINT §8.9 + ARCHITECTURE_SPEC §8.1/§8.4. All gated by
:class:`AdminOnly`. Output is HTML; every user-provided string is
escaped before interpolation.

Each handler is thin:

1. parses CLI-style arguments,
2. calls *one* service method,
3. renders via the shared pure view in
   :mod:`app.bot.views.admin_ops` (also used by
   :mod:`app.bot.handlers.admin.panel` — the button-driven panel and
   the slash commands stay in lock-step).
"""

from __future__ import annotations

import structlog
from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.filters.admin_only import AdminOnly
from app.bot.views.admin_ops import (
    parse_int_arg,
    render_attempt_detail,
    render_leaderboard,
    render_stats,
    render_user_card,
)
from app.core.container import Container, Services
from app.models.user import User
from app.utils.text import html_escape

logger = structlog.get_logger()

router = Router(name="admin_operations")

# Router-level admin gate. Non-admins fall through to other routers
# (including the common-router fallback in private chats).
router.message.filter(AdminOnly())

_USAGE_FIND = "Использование: /find &lt;телефон | username | код&gt;"
_USAGE_BAN = "Использование: /ban &lt;user_id&gt;"
_USAGE_UNBAN = "Использование: /unban &lt;user_id&gt;"
_USAGE_LEADERBOARD = "Использование: /leaderboard &lt;test_id&gt;"
_USAGE_ATTEMPT = "Использование: /attempt &lt;attempt_id&gt;"
_USER_NOT_FOUND = "Пользователь не найден."
_TEST_NOT_FOUND = "Тест не найден."
_ATTEMPT_NOT_FOUND = "Попытка не найдена."


# ============================================================
# /stats
# ============================================================


@router.message(Command("stats"))
async def cmd_stats(
    message: Message,
    session: AsyncSession,
    user: User,
    container: Container,
) -> None:
    services = container.services(session)
    snapshot = await services.stats.snapshot()
    await message.answer(render_stats(snapshot), parse_mode="HTML")


# ============================================================
# /find <q>
# ============================================================


@router.message(Command("find"))
async def cmd_find(
    message: Message,
    command: CommandObject,
    session: AsyncSession,
    user: User,
    container: Container,
) -> None:
    query = (command.args or "").strip()
    if not query:
        await message.answer(_USAGE_FIND)
        return

    services = container.services(session)
    found = await services.user.find(query)
    if found is None:
        await message.answer(_USER_NOT_FOUND)
        return

    pending_count = await services.receipt.count_pending_for_user(found.id)
    await message.answer(
        render_user_card(found, pending_count=pending_count),
        parse_mode="HTML",
    )


# ============================================================
# /ban <id>  +  /unban <id>
# ============================================================


@router.message(Command("ban"))
async def cmd_ban(
    message: Message,
    command: CommandObject,
    session: AsyncSession,
    user: User,
    container: Container,
) -> None:
    target_id = parse_int_arg(command.args)
    if target_id is None:
        await message.answer(_USAGE_BAN)
        return

    services = container.services(session)
    target = await services.user.get_user(target_id)
    if target is None:
        await message.answer(_USER_NOT_FOUND)
        return

    await services.user.ban(target_id)
    # Force-expire any test the banned user had open + cancel its timer jobs
    # so they don't keep getting warning / result DMs (CODE_REVIEW H20).
    finalized = await services.attempt.finalize_in_progress_for_user(target_id)
    logger.info(
        "admin_banned_user",
        admin_telegram_id=message.from_user.id,
        user_id=target_id,
        attempts_finalized=finalized,
    )
    await message.answer(
        f"🚫 Пользователь <code>{target_id}</code> забанен.",
        parse_mode="HTML",
    )


@router.message(Command("unban"))
async def cmd_unban(
    message: Message,
    command: CommandObject,
    session: AsyncSession,
    user: User,
    container: Container,
) -> None:
    target_id = parse_int_arg(command.args)
    if target_id is None:
        await message.answer(_USAGE_UNBAN)
        return

    services = container.services(session)
    target = await services.user.get_user(target_id)
    if target is None:
        await message.answer(_USER_NOT_FOUND)
        return
    if target.status != "banned":
        await message.answer(
            f"Пользователь <code>{target_id}</code> не в бане "
            f"(статус: <code>{html_escape(target.status)}</code>).",
            parse_mode="HTML",
        )
        return

    restored = await services.user.unban(target_id)
    if not restored:
        # Never approved before the ban — don't grant access via unban (M16).
        await message.answer(
            f"Пользователь <code>{target_id}</code> не был одобрен ранее — "
            "разбан не выдаёт доступ. Проведите его через оплату заново.",
            parse_mode="HTML",
        )
        return
    logger.info("admin_unbanned_user", admin_telegram_id=message.from_user.id, user_id=target_id)
    await message.answer(
        f"✅ Пользователь <code>{target_id}</code> восстановлен (approved).",
        parse_mode="HTML",
    )


# ============================================================
# /leaderboard <test_id>
# ============================================================


@router.message(Command("leaderboard"))
async def cmd_leaderboard(
    message: Message,
    command: CommandObject,
    session: AsyncSession,
    user: User,
    container: Container,
) -> None:
    test_id = parse_int_arg(command.args)
    if test_id is None:
        await message.answer(_USAGE_LEADERBOARD)
        return

    services = container.services(session)
    test = await services.test.get_test(test_id)
    if test is None:
        await message.answer(_TEST_NOT_FOUND)
        return

    entries = await services.attempt.list_top_for_test(test_id, limit=20)
    await message.answer(
        render_leaderboard(test_title=test.title, entries=entries),
        parse_mode="HTML",
    )


# ============================================================
# /attempt <id>
# ============================================================


@router.message(Command("attempt"))
async def cmd_attempt(
    message: Message,
    command: CommandObject,
    session: AsyncSession,
    user: User,
    container: Container,
) -> None:
    attempt_id = parse_int_arg(command.args)
    if attempt_id is None:
        await message.answer(_USAGE_ATTEMPT)
        return

    services = container.services(session)
    detail = await services.attempt.get_attempt_detail(attempt_id)
    if detail is None:
        await message.answer(_ATTEMPT_NOT_FOUND)
        return

    target_user = await services.user.get_user(detail.attempt.user_id)
    test = await services.test.get_test(detail.attempt.test_id)

    await message.answer(
        render_attempt_detail(detail, owner=target_user, test_title=test.title if test else None),
        parse_mode="HTML",
    )


# Silence unused-name warning when the kwargs are passed by the middleware
# chain (handlers must accept them, even if the body doesn't read them).
_ = Services
