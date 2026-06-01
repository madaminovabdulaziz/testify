"""Unit tests for NotificationService broadcast retry semantics (CODE_REVIEW H15)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from aiogram.exceptions import TelegramRetryAfter

from app.services.notification_service import NotificationService


def _retry_after(seconds: int = 1) -> TelegramRetryAfter:
    return TelegramRetryAfter(method=MagicMock(), message="Too Many Requests", retry_after=seconds)


def _service(bot: MagicMock) -> NotificationService:
    users = MagicMock()
    users.mark_bot_blocked = AsyncMock()
    return NotificationService(
        bot,
        users,
        admin_group_id=-1001,
        broadcast_concurrency=5,
        broadcast_rate_per_second=10_000,  # high so the token bucket never blocks
    )


async def test_broadcast_retries_then_succeeds_on_repeated_429() -> None:
    bot = MagicMock()
    # Two 429s, then a success — the old retry-once code would have dropped
    # this one after the first retry.
    bot.send_message = AsyncMock(side_effect=[_retry_after(), _retry_after(), MagicMock()])
    svc = _service(bot)

    with patch("app.services.notification_service.asyncio.sleep", new=AsyncMock()):
        summary = await svc.broadcast_new_test("hi", [(1, 100)])

    assert summary.sent == 1
    assert summary.errors == 0
    assert bot.send_message.await_count == 3


async def test_broadcast_counts_error_after_exhausting_retries() -> None:
    bot = MagicMock()
    bot.send_message = AsyncMock(side_effect=_retry_after())  # always throttled
    svc = _service(bot)

    with patch("app.services.notification_service.asyncio.sleep", new=AsyncMock()):
        summary = await svc.broadcast_new_test("hi", [(1, 100)])

    assert summary.errors == 1
    assert summary.sent == 0
    assert bot.send_message.await_count == 3  # _MAX_SEND_ATTEMPTS, not 1
