"""Unit tests for :class:`DbSessionMiddleware` commit/rollback behavior."""

from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.bot.middlewares.db_session import DbSessionMiddleware


def _make_session_factory() -> tuple[MagicMock, MagicMock]:
    """Return a factory that yields one fresh AsyncMock session via async-with."""
    session = MagicMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()

    @asynccontextmanager
    async def cm():
        try:
            yield session
        finally:
            pass

    factory = MagicMock(side_effect=lambda: cm())
    return factory, session


async def test_commits_on_handler_success() -> None:
    factory, session = _make_session_factory()
    mw = DbSessionMiddleware(factory)

    async def handler(event, data):
        assert data["session"] is session
        return "ok"

    result = await mw(handler, MagicMock(), {})

    assert result == "ok"
    session.commit.assert_awaited_once()
    session.rollback.assert_not_awaited()


async def test_rolls_back_on_handler_exception() -> None:
    factory, session = _make_session_factory()
    mw = DbSessionMiddleware(factory)

    async def handler(event, data):
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        await mw(handler, MagicMock(), {})

    session.commit.assert_not_awaited()
    session.rollback.assert_awaited_once()


async def test_injects_session_into_data_dict() -> None:
    factory, session = _make_session_factory()
    mw = DbSessionMiddleware(factory)
    seen: dict = {}

    async def handler(event, data):
        seen["session"] = data.get("session")

    await mw(handler, MagicMock(), {})

    assert seen["session"] is session
