"""Filter: passes only when an update originates from the configured admin group."""

from __future__ import annotations

from aiogram.filters import Filter
from aiogram.types import CallbackQuery, Message


class AdminGroupOnly(Filter):
    """True iff the update's chat is the configured ``admin_group_id``.

    Bound at dispatcher-construction time with the value pulled from
    :class:`app.core.config.Settings`. PRODUCT_BLUEPRINT §14.3:
    admin-only commands are silently ignored outside the admin group
    (except DMs from registered admins, which the per-handler filters
    decide).
    """

    def __init__(self, admin_group_id: int) -> None:
        self._admin_group_id = admin_group_id

    async def __call__(self, event: Message | CallbackQuery) -> bool:
        # Duck-typed lookup so test mocks that don't perfectly imitate
        # aiogram's class hierarchy still work. Message has ``.chat``
        # directly; CallbackQuery exposes it via ``.message.chat``.
        chat = getattr(event, "chat", None)
        if chat is None:
            inner = getattr(event, "message", None)
            chat = getattr(inner, "chat", None) if inner is not None else None
        return chat is not None and chat.id == self._admin_group_id
