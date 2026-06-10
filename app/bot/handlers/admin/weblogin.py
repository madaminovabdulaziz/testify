"""/weblogin — issue a one-time login code for the web admin panel.

Private-chat only: the code is a credential, and the admin group can
contain non-admin observers (same threat model as the receipts router
gating in app/bot/bot.py). In the group the command politely refuses.
"""

from __future__ import annotations

import structlog
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.filters.admin_only import AdminOnly
from app.core.container import Container
from app.models.user import User
from app.web.auth import issue_login_code, panel_base_url

logger = structlog.get_logger()

router = Router(name="admin_weblogin")
router.message.filter(AdminOnly())

_GROUP_REFUSAL = "Команда работает только в личных сообщениях с ботом."


@router.message(Command("weblogin"), F.chat.type == "private")
async def cmd_weblogin(
    message: Message,
    session: AsyncSession,
    user: User,
    container: Container,
) -> None:
    """Generate a single-use 6-digit code and reply with the panel URL."""
    services = container.services(session)
    assert message.from_user is not None  # private-chat message always has a sender
    admin = await services.admin.get_by_telegram_id(message.from_user.id)
    if admin is None:  # pragma: no cover — AdminOnly() guarantees the row
        return

    settings = container.settings
    code = await issue_login_code(
        container.redis,
        settings.env,
        admin.id,
        ttl_seconds=settings.web_login_code_ttl_seconds,
    )
    minutes = settings.web_login_code_ttl_seconds // 60
    logger.info("weblogin_code_issued", admin_id=admin.id)
    await message.answer(
        "🔑 Код для входа в веб-панель:\n\n"
        f"<code>{code}</code>\n\n"
        f"Откройте {panel_base_url(settings)} и введите код.\n"
        f"Код одноразовый и действует {minutes} минут."
    )


@router.message(Command("weblogin"))
async def cmd_weblogin_in_group(message: Message) -> None:
    """The code must never appear in the admin group — refuse there."""
    await message.answer(_GROUP_REFUSAL)
