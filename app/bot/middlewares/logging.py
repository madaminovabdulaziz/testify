"""Outermost middleware: bind a per-update logging context to structlog.

Every log line emitted during this update's processing automatically
carries ``request_id``, ``telegram_id`` and ``update_type`` (see
ARCHITECTURE_SPEC §15.1).
"""

from __future__ import annotations

import secrets
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import Update

from app.bot.middlewares._util import get_chat_id, get_from_user, get_update_type
from app.core.logging import bind_request_context, clear_request_context


class LoggingMiddleware(BaseMiddleware):
    """Bind request-scoped context vars; clear them after the handler returns."""

    async def __call__(
        self,
        handler: Callable[[Update, dict[str, Any]], Awaitable[Any]],
        event: Update,
        data: dict[str, Any],
    ) -> Any:
        request_id = secrets.token_urlsafe(8)
        from_user = get_from_user(event)
        bind_request_context(
            request_id=request_id,
            telegram_id=from_user.id if from_user is not None else None,
            update_type=get_update_type(event),
            chat_id=get_chat_id(event),
        )
        try:
            return await handler(event, data)
        finally:
            clear_request_context()
