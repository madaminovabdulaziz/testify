"""Filter: passes only if the loaded user is in ``status='approved'``."""

from __future__ import annotations

from typing import Any

from aiogram.filters import Filter
from aiogram.types import CallbackQuery, Message

from app.models.user import User


class ApprovedOnly(Filter):
    """Gate access to the test-taking entry point.

    Relies on ``UserLoaderMiddleware`` having injected the ``User`` row
    into ``data['user']``; if no user was loaded (anonymous update),
    the filter returns ``False``.
    """

    async def __call__(
        self,
        event: Message | CallbackQuery,
        **data: Any,
    ) -> bool:
        user: User | None = data.get("user")
        return user is not None and user.status == "approved"
