"""One ``AsyncSession`` per update.

Commit on success, rollback on exception, close always. Subsequent
middlewares + the handler pull the session out of ``data['session']``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import Update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


class DbSessionMiddleware(BaseMiddleware):
    """Open a session for the lifetime of one update."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def __call__(
        self,
        handler: Callable[[Update, dict[str, Any]], Awaitable[Any]],
        event: Update,
        data: dict[str, Any],
    ) -> Any:
        async with self._session_factory() as session:
            data["session"] = session
            try:
                result = await handler(event, data)
            except Exception:
                await session.rollback()
                raise
            # The commit itself can fail (deferred constraint, deadlock,
            # connection drop). Roll back explicitly so the connection is
            # returned clean rather than relying on the context manager's
            # close (CODE_REVIEW L10).
            try:
                await session.commit()
            except Exception:
                await session.rollback()
                raise
            return result
