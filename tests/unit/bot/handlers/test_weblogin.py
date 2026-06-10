"""Unit tests for the /weblogin admin command."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from app.bot.handlers.admin.weblogin import cmd_weblogin, cmd_weblogin_in_group


def _container(*, redis) -> MagicMock:
    container = MagicMock()
    container.redis = redis
    container.settings.env = "dev"
    container.settings.web_login_code_ttl_seconds = 300
    container.settings.panel_base_url = None
    container.settings.webhook_url = None
    services = MagicMock()
    services.admin.get_by_telegram_id = AsyncMock(return_value=SimpleNamespace(id=7))
    container.services = MagicMock(return_value=services)
    return container


async def test_weblogin_issues_code_and_replies_with_url() -> None:
    redis = MagicMock()
    redis.set = AsyncMock(return_value=True)
    container = _container(redis=redis)
    message = MagicMock()
    message.from_user = SimpleNamespace(id=111)
    message.answer = AsyncMock()

    await cmd_weblogin(message, session=MagicMock(), user=MagicMock(), container=container)

    # The code was stored under the env-prefixed key with the configured TTL.
    key = redis.set.await_args.args[0]
    assert key.startswith("dev:weblogin:")
    assert redis.set.await_args.kwargs == {"nx": True, "ex": 300}

    reply = message.answer.await_args.args[0]
    code = key.removeprefix("dev:weblogin:")
    assert f"<code>{code}</code>" in reply
    assert "http://localhost:8080/panel/" in reply


async def test_weblogin_refuses_in_group_chat() -> None:
    message = MagicMock()
    message.answer = AsyncMock()

    await cmd_weblogin_in_group(message)

    assert "личных сообщениях" in message.answer.await_args.args[0]
