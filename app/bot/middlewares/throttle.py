"""Per-user rate limit: drop updates above ``max_per_second`` for the same Telegram ID.

Implemented as ``INCR throttle:<tg_id>`` with a 1-second EXPIRE on the
first hit — the cheapest possible Redis pattern. Updates without a
sender (channel posts etc.) bypass the limiter.
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

from app.bot.middlewares._util import get_from_user

logger = structlog.get_logger()


class ThrottleMiddleware(BaseMiddleware):
    """Drop the update + silent-ACK the callback if a user exceeds ``max_per_second`` ops/s."""

    def __init__(self, redis: Redis, *, max_per_second: int = 10) -> None:
        self._redis = redis
        self._max_per_second = max_per_second

    async def __call__(
        self,
        handler: Callable[[Update, dict[str, Any]], Awaitable[Any]],
        event: Update,
        data: dict[str, Any],
    ) -> Any:
        from_user = get_from_user(event)
        if from_user is None:
            return await handler(event, data)

        key = f"throttle:{from_user.id}"
        try:
            count = int(await self._redis.incr(key))
            if count == 1:
                # Only set the TTL on the first hit of a window — keeps
                # subsequent INCRs to a single round-trip each.
                await self._redis.expire(key, 1)
        except RedisError:
            # Fail OPEN. The limiter's backing store hiccuped; dropping rate
            # limiting briefly is far better than turning every user's update
            # into "Произошла ошибка" via the global error handler
            # (CODE_REVIEW H10).
            logger.warning("throttle_redis_unavailable", telegram_id=from_user.id)
            return await handler(event, data)

        if count > self._max_per_second:
            logger.info(
                "throttle_triggered",
                telegram_id=from_user.id,
                count=count,
                limit=self._max_per_second,
            )
            # ACK callbacks silently so the user's button doesn't spin
            # forever; messages just get dropped with no reply.
            if event.callback_query is not None:
                with contextlib.suppress(TelegramAPIError):
                    await event.callback_query.answer()
            return None

        return await handler(event, data)
