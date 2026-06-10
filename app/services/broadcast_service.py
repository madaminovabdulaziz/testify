"""Admin announcement broadcasts — durable creation + progress tracking.

The actual delivery loop lives in :mod:`app.jobs.broadcast_runner`
(it owns its own short DB sessions and the Telegram rate budget); this
service is the data contract the handler and the runner share.
"""

from __future__ import annotations

from app.models.broadcast import Broadcast
from app.repositories.broadcast_repository import BroadcastRepository
from app.repositories.user_repository import UserRepository


class BroadcastService:
    """Create / inspect / advance announcement broadcasts."""

    def __init__(
        self,
        broadcast_repository: BroadcastRepository,
        user_repository: UserRepository,
    ) -> None:
        self._broadcasts = broadcast_repository
        self._users = user_repository

    async def create(
        self,
        *,
        source_chat_id: int,
        source_message_id: int,
        created_by_admin_id: int | None,
        report_chat_id: int | None,
    ) -> Broadcast:
        """Persist a new ``in_progress`` broadcast with the current recipient count."""
        total = await self._users.count_approved_for_broadcast()
        return await self._broadcasts.create(
            source_chat_id=source_chat_id,
            source_message_id=source_message_id,
            created_by_admin_id=created_by_admin_id,
            report_chat_id=report_chat_id,
            total_recipients=total,
        )

    async def count_recipients(self) -> int:
        """How many students the announcement would reach right now."""
        return await self._users.count_approved_for_broadcast()

    async def get(self, broadcast_id: int) -> Broadcast | None:
        return await self._broadcasts.get_by_id(broadcast_id)

    async def list_in_progress(self) -> list[Broadcast]:
        """Unfinished broadcasts to resume at startup."""
        return await self._broadcasts.list_in_progress()

    async def next_recipients(
        self, cursor_user_id: int, *, limit: int = 30
    ) -> list[tuple[int, int]]:
        """The next delivery batch after the cursor: ``(user_id, telegram_id)``."""
        return await self._users.list_approved_after(cursor_user_id, limit=limit)

    async def record_progress(
        self,
        broadcast_id: int,
        *,
        sent_count: int,
        blocked_count: int,
        error_count: int,
        last_user_id: int,
    ) -> None:
        """Persist absolute counters + the resume cursor after a batch."""
        await self._broadcasts.record_progress(
            broadcast_id,
            sent_count=sent_count,
            blocked_count=blocked_count,
            error_count=error_count,
            last_user_id=last_user_id,
        )

    async def mark_completed(self, broadcast_id: int) -> bool:
        """Finish the broadcast; False when it was already completed."""
        return await self._broadcasts.mark_completed(broadcast_id) > 0
