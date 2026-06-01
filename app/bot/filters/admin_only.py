"""Filter: passes only if the update's sender is a registered admin."""

from __future__ import annotations

from typing import Any

from aiogram.filters import Filter
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.admin_repository import AdminRepository


class AdminOnly(Filter):
    """True iff ``event.from_user.id`` is in the ``admins`` table.

    Reads the session injected by ``DbSessionMiddleware`` rather than
    going through a service — admin-membership lookup is a one-line
    query and pulling in a whole service layer just to check it would
    be overkill.
    """

    async def __call__(
        self,
        event: Message | CallbackQuery,
        **data: Any,
    ) -> bool:
        if event.from_user is None:
            return False
        session: AsyncSession | None = data.get("session")
        if session is None:
            # Defensive: middleware ordering should ensure a session is
            # always present. Failing closed (not-admin) is safer than
            # blowing up.
            return False
        admin = await AdminRepository(session).get_by_telegram_id(event.from_user.id)
        return admin is not None
