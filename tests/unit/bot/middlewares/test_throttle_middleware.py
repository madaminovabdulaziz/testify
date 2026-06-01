"""Unit tests for :class:`ThrottleMiddleware`."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from redis.exceptions import ConnectionError as RedisConnectionError

from app.bot.middlewares.throttle import ThrottleMiddleware


def _msg_update(*, telegram_id: int) -> MagicMock:
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

    update.message = MagicMock()
    update.message.from_user = SimpleNamespace(id=telegram_id, username="x")
    return update


def _fake_redis(counter_state: dict[str, int]) -> MagicMock:
    redis = MagicMock()

    async def incr(key: str) -> int:
        counter_state[key] = counter_state.get(key, 0) + 1
        return counter_state[key]

    redis.incr = AsyncMock(side_effect=incr)
    redis.expire = AsyncMock()
    return redis


async def test_passes_through_when_under_limit() -> None:
    state: dict[str, int] = {}
    redis = _fake_redis(state)
    mw = ThrottleMiddleware(redis, max_per_second=10)
    update = _msg_update(telegram_id=1)
    handler = AsyncMock(return_value="ok")

    for _ in range(5):
        result = await mw(handler, update, {})
        assert result == "ok"

    assert handler.await_count == 5
    # EXPIRE called only on the first INCR (count == 1).
    redis.expire.assert_awaited_once_with("throttle:1", 1)


async def test_drops_update_when_over_limit() -> None:
    state: dict[str, int] = {"throttle:1": 9}  # next INCR returns 10 (== limit), then 11
    redis = _fake_redis(state)
    mw = ThrottleMiddleware(redis, max_per_second=10)
    update = _msg_update(telegram_id=1)
    handler = AsyncMock()

    # 10th request still goes through.
    await mw(handler, update, {})
    assert handler.await_count == 1

    # 11th is dropped.
    result = await mw(handler, update, {})
    assert result is None
    assert handler.await_count == 1


async def test_fails_open_when_redis_unavailable() -> None:
    # CODE_REVIEW H10: a Redis outage must not turn every update into an
    # error. The limiter degrades to pass-through.
    redis = MagicMock()
    redis.incr = AsyncMock(side_effect=RedisConnectionError("redis down"))
    redis.expire = AsyncMock()
    mw = ThrottleMiddleware(redis, max_per_second=10)
    update = _msg_update(telegram_id=1)
    handler = AsyncMock(return_value="ok")

    result = await mw(handler, update, {})

    assert result == "ok"
    handler.assert_awaited_once()


async def test_anonymous_update_passes_without_redis_hit() -> None:
    state: dict[str, int] = {}
    redis = _fake_redis(state)
    mw = ThrottleMiddleware(redis, max_per_second=10)

    update = MagicMock()
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
    result = await mw(handler, update, {})

    assert result == "ok"
    redis.incr.assert_not_awaited()
