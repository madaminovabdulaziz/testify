"""Resolve the sending user (creating one on first sight) + ban short-circuit.

Runs *after* :class:`DbSessionMiddleware` so the session is in
``data['session']``. Updates that don't carry a sender — channel posts,
poll updates, etc. — pass through untouched.
"""

from __future__ import annotations

import contextlib
from collections.abc import Awaitable, Callable
from typing import Any

import structlog
from aiogram import BaseMiddleware
from aiogram.exceptions import TelegramAPIError
from aiogram.types import Update
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.middlewares._util import get_event_obj, get_from_user
from app.repositories.user_repository import UserRepository
from app.services.user_service import UserService

logger = structlog.get_logger()

BANNED_MESSAGE = "Доступ ограничён."

# At most one "Доступ ограничён." reply per banned user per this window, so a
# banned spammer doesn't cost an outbound send on every message (CODE_REVIEW L18).
_BAN_REPLY_TTL_SECONDS = 300


class UserLoaderMiddleware(BaseMiddleware):
    """Inject the loaded ``User`` row into handler kwargs. Drop banned users early."""

    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def __call__(
        self,
        handler: Callable[[Update, dict[str, Any]], Awaitable[Any]],
        event: Update,
        data: dict[str, Any],
    ) -> Any:
        from_user = get_from_user(event)
        if from_user is None:
            # No sender — service-side events (poll close, etc.). Pass through.
            return await handler(event, data)

        session: AsyncSession | None = data.get("session")
        if session is None:
            # Middleware ordering invariant violated; bail loudly in the logs
            # but don't crash the update.
            logger.warning("user_loader_missing_session")
            return await handler(event, data)

        user_service = UserService(UserRepository(session))
        user = await user_service.get_or_create(
            telegram_id=from_user.id,
            username=from_user.username,
        )

        if user.status == "banned":
            logger.info("banned_user_blocked", user_id=user.id, telegram_id=from_user.id)
            await self._maybe_reply_banned(event, from_user.id)
            # Short-circuit: no handler invocation. DbSessionMiddleware will
            # still commit (no exception) so the get_or_create write — if
            # this is a brand-new user-then-banned race — is durable.
            return None

        # An incoming update proves the user can reach us, so a stale
        # bot_blocked flag (set when an earlier send hit Forbidden) is wrong —
        # clear it so broadcasts include them again (CODE_REVIEW L2).
        if user.bot_blocked:
            await user_service.clear_bot_blocked(user.id)
            user.bot_blocked = False

        data["user"] = user
        return await handler(event, data)

    async def _maybe_reply_banned(self, event: Update, telegram_id: int) -> None:
        """Send the ban notice at most once per ``_BAN_REPLY_TTL_SECONDS`` (L18)."""
        try:
            first = await self._redis.set(
                f"ban_reply:{telegram_id}", b"1", nx=True, ex=_BAN_REPLY_TTL_SECONDS
            )
        except RedisError:
            first = True  # Redis hiccup → fail open and reply
        if first:
            await _silently_send_banned(event)


async def _silently_send_banned(update: Update) -> None:
    """Best-effort "Доступ ограничён." reply; swallow any send errors."""
    event_obj = get_event_obj(update)
    if event_obj is None:
        return
    try:
        if hasattr(event_obj, "answer") and callable(event_obj.answer):
            # Message has .answer(text=...); CallbackQuery has .answer(text=, show_alert=).
            with contextlib.suppress(TypeError):
                await event_obj.answer(BANNED_MESSAGE, show_alert=True)
                return
            await event_obj.answer(BANNED_MESSAGE)
    except TelegramAPIError:
        # User blocked the bot or chat is gone; nothing we can do.
        return
