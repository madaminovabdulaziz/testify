"""Unit tests for :class:`UserLoaderMiddleware`."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import app.bot.middlewares.user_loader as user_loader_mod
from app.bot.middlewares.user_loader import UserLoaderMiddleware


def _redis(*, set_returns: object = True) -> MagicMock:
    redis = MagicMock()
    redis.set = AsyncMock(return_value=set_returns)
    return redis


def _update_with_message_sender(*, telegram_id: int, username: str = "alice") -> MagicMock:
    """Build a minimal Update-shaped mock carrying a message with a from_user."""
    update = MagicMock()
    update.callback_query = None
    update.edited_message = None
    update.channel_post = None
    update.edited_channel_post = None
    update.inline_query = None
    update.chosen_inline_result = None
    update.shipping_query = None
    update.pre_checkout_query = None
    update.poll_answer = None
    update.my_chat_member = None
    update.chat_member = None
    update.chat_join_request = None

    message = MagicMock()
    message.from_user = SimpleNamespace(id=telegram_id, username=username)
    message.answer = AsyncMock()
    update.message = message
    return update


async def test_anonymous_update_passes_through_untouched(monkeypatch) -> None:
    mw = UserLoaderMiddleware(_redis())
    update = MagicMock()
    # All event subfields are None — get_from_user returns None.
    for attr in (
        "message",
        "callback_query",
        "edited_message",
        "channel_post",
        "edited_channel_post",
        "inline_query",
        "chosen_inline_result",
        "shipping_query",
        "pre_checkout_query",
        "poll_answer",
        "my_chat_member",
        "chat_member",
        "chat_join_request",
    ):
        setattr(update, attr, None)

    handler = AsyncMock(return_value="ok")
    data: dict = {"session": MagicMock()}

    result = await mw(handler, update, data)

    assert result == "ok"
    assert "user" not in data
    handler.assert_awaited_once()


async def test_loads_user_and_injects_into_data(monkeypatch) -> None:
    mw = UserLoaderMiddleware(_redis())
    update = _update_with_message_sender(telegram_id=100)

    # Patch UserService.get_or_create so we don't need a real session.
    fake_user = SimpleNamespace(id=5, status="approved", bot_blocked=False)
    fake_service = MagicMock()
    fake_service.get_or_create = AsyncMock(return_value=fake_user)
    monkeypatch.setattr(user_loader_mod, "UserService", MagicMock(return_value=fake_service))

    handler = AsyncMock(return_value="ok")
    data: dict = {"session": MagicMock()}

    result = await mw(handler, update, data)

    assert result == "ok"
    assert data["user"] is fake_user
    fake_service.get_or_create.assert_awaited_once_with(telegram_id=100, username="alice")


async def test_banned_user_short_circuits_handler(monkeypatch) -> None:
    mw = UserLoaderMiddleware(_redis())
    update = _update_with_message_sender(telegram_id=200, username="banned_one")

    fake_user = SimpleNamespace(id=9, status="banned")
    fake_service = MagicMock()
    fake_service.get_or_create = AsyncMock(return_value=fake_user)
    monkeypatch.setattr(user_loader_mod, "UserService", MagicMock(return_value=fake_service))

    handler = AsyncMock()
    data: dict = {"session": MagicMock()}

    result = await mw(handler, update, data)

    assert result is None
    handler.assert_not_awaited()
    # The "Доступ ограничён." reply was attempted.
    update.message.answer.assert_awaited()


async def test_returning_user_has_bot_blocked_cleared(monkeypatch) -> None:
    # CODE_REVIEW L2: an incoming update from a previously-flagged user proves
    # they can reach us again — clear bot_blocked so broadcasts re-include them.
    mw = UserLoaderMiddleware(_redis())
    update = _update_with_message_sender(telegram_id=100)

    fake_user = SimpleNamespace(id=5, status="approved", bot_blocked=True)
    fake_service = MagicMock()
    fake_service.get_or_create = AsyncMock(return_value=fake_user)
    fake_service.clear_bot_blocked = AsyncMock()
    monkeypatch.setattr(user_loader_mod, "UserService", MagicMock(return_value=fake_service))

    await mw(AsyncMock(return_value="ok"), update, {"session": MagicMock()})

    fake_service.clear_bot_blocked.assert_awaited_once_with(5)
    assert fake_user.bot_blocked is False


async def test_banned_reply_suppressed_when_rate_limited(monkeypatch) -> None:
    # CODE_REVIEW L18: a banned spammer's reply is rate-limited — when the
    # Redis NX marker already exists (set returns None) we don't send.
    mw = UserLoaderMiddleware(_redis(set_returns=None))
    update = _update_with_message_sender(telegram_id=200, username="banned_one")

    fake_user = SimpleNamespace(id=9, status="banned")
    fake_service = MagicMock()
    fake_service.get_or_create = AsyncMock(return_value=fake_user)
    monkeypatch.setattr(user_loader_mod, "UserService", MagicMock(return_value=fake_service))

    result = await mw(AsyncMock(), update, {"session": MagicMock()})

    assert result is None
    update.message.answer.assert_not_awaited()


async def test_missing_session_logs_and_passes_through(monkeypatch) -> None:
    """If DbSessionMiddleware didn't run for some reason, don't crash."""
    mw = UserLoaderMiddleware(_redis())
    update = _update_with_message_sender(telegram_id=300)

    handler = AsyncMock(return_value="ok")
    data: dict = {}  # no "session" key

    result = await mw(handler, update, data)

    assert result == "ok"
    handler.assert_awaited_once()
