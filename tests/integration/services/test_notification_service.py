"""Integration test for :class:`NotificationService.broadcast_new_test`.

Real MySQL is used because the broadcast writes ``bot_blocked=True`` for
every recipient that raised ``TelegramForbiddenError``. The aiogram
``Bot`` is mocked so the test never touches Telegram.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from aiogram.exceptions import TelegramForbiddenError
from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.user_repository import UserRepository
from app.services.notification_service import BroadcastSummary, NotificationService


def _bot_that_blocks(blocked_telegram_ids: set[int]) -> MagicMock:
    """Mock Bot whose send_message raises Forbidden for the listed Telegram IDs."""
    bot = MagicMock()

    async def fake_send_message(chat_id: int, *args: object, **kwargs: object) -> object:
        if chat_id in blocked_telegram_ids:
            raise TelegramForbiddenError(method=MagicMock(), message="bot blocked")
        return MagicMock()  # represents the sent Message

    bot.send_message = AsyncMock(side_effect=fake_send_message)
    return bot


async def test_broadcast_marks_blocked_users_and_counts_correctly(
    session: AsyncSession,
) -> None:
    users = UserRepository(session)

    # Create 50 approved users with predictable telegram_ids 1000..1049.
    recipients: list[tuple[int, int]] = []
    for tg in range(1000, 1050):
        u = await users.create(telegram_id=tg, username=f"u{tg}")
        await users.mark_approved(u.id)
        recipients.append((u.id, tg))

    # Pick the first 10 telegram_ids to "block" the bot.
    blocked_tg = {tg for (_uid, tg) in recipients[:10]}

    bot = _bot_that_blocks(blocked_tg)
    svc = NotificationService(
        bot=bot,
        user_repository=users,
        admin_group_id=-1001,
        broadcast_concurrency=20,
        # Crank the rate way up so the bucket never blocks during the test.
        broadcast_rate_per_second=10_000,
    )

    summary = await svc.broadcast_new_test("hello", recipients)

    # ---------- summary counters ----------
    assert isinstance(summary, BroadcastSummary)
    assert summary.sent == 40
    assert summary.blocked == 10
    assert summary.errors == 0
    assert summary.total == 50

    # ---------- DB writes: bot_blocked flipped on the 10 ----------
    session.expunge_all()
    blocked_user_ids = {uid for (uid, tg) in recipients if tg in blocked_tg}
    for uid in blocked_user_ids:
        row = await users.get_by_id(uid)
        assert row is not None and row.bot_blocked is True

    not_blocked_user_ids = {uid for (uid, tg) in recipients if tg not in blocked_tg}
    for uid in not_blocked_user_ids:
        row = await users.get_by_id(uid)
        assert row is not None and row.bot_blocked is False

    # send_message was attempted for every recipient (no early exits).
    assert bot.send_message.await_count == 50
