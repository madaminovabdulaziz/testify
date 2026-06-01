"""Unit tests for the /healthz endpoint.

Uses ``aiohttp.test_utils.TestClient`` so we never bind a real socket.
The DB and Redis pings are mocked — this test is about the aiohttp
plumbing, not the underlying drivers (those are covered by their own
integration tests).
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

from aiohttp.test_utils import TestClient, TestServer

from app.bot.webhook import make_app


def _make_container(*, db_ok: bool = True, redis_ok: bool = True) -> MagicMock:
    """Mock Container with session_factory + redis ping that succeed/fail as configured."""
    container = MagicMock()
    container.settings.webhook_path = "/webhook"
    container.settings.webhook_secret.get_secret_value.return_value = "secret"

    session = MagicMock()
    if db_ok:
        session.execute = AsyncMock()
    else:
        session.execute = AsyncMock(side_effect=RuntimeError("db down"))

    @asynccontextmanager
    async def session_cm():
        yield session

    container.session_factory = MagicMock(side_effect=lambda: session_cm())
    container.redis.ping = (
        AsyncMock() if redis_ok else AsyncMock(side_effect=RuntimeError("redis down"))
    )
    return container


async def test_healthz_returns_200_when_db_and_redis_ok() -> None:
    container = _make_container(db_ok=True, redis_ok=True)
    app = make_app(container, dispatcher=MagicMock())

    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/healthz")
        assert resp.status == 200
        body = await resp.json()
        assert body == {"status": "ok"}


async def test_healthz_returns_503_when_db_down() -> None:
    container = _make_container(db_ok=False, redis_ok=True)
    app = make_app(container, dispatcher=MagicMock())

    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/healthz")
        assert resp.status == 503
        body = await resp.json()
        assert body == {"status": "fail"}


async def test_healthz_returns_503_when_redis_down() -> None:
    container = _make_container(db_ok=True, redis_ok=False)
    app = make_app(container, dispatcher=MagicMock())

    async with TestClient(TestServer(app)) as client:
        resp = await client.get("/healthz")
        assert resp.status == 503


async def test_webhook_rejects_missing_secret_header() -> None:
    container = _make_container()
    app = make_app(container, dispatcher=MagicMock())

    async with TestClient(TestServer(app)) as client:
        resp = await client.post("/webhook", json={"update_id": 1})
        assert resp.status == 403


async def test_webhook_rejects_wrong_secret_header() -> None:
    container = _make_container()
    app = make_app(container, dispatcher=MagicMock())

    async with TestClient(TestServer(app)) as client:
        resp = await client.post(
            "/webhook",
            json={"update_id": 1},
            headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"},
        )
        assert resp.status == 403


async def test_webhook_acks_immediately_and_processes_update_in_background() -> None:
    container = _make_container()
    dispatcher = MagicMock()
    dispatcher.feed_update = AsyncMock()
    app = make_app(container, dispatcher=dispatcher)

    async with TestClient(TestServer(app)) as client:
        resp = await client.post(
            "/webhook",
            json={"update_id": 1},
            headers={"X-Telegram-Bot-Api-Secret-Token": "secret"},
        )
        # Acked immediately (CODE_REVIEW H9) ...
        assert resp.status == 200
        # ... and the update is routed on a background task. Yield the loop
        # a few times so that task gets to run.
        for _ in range(10):
            await asyncio.sleep(0)
            if dispatcher.feed_update.await_count:
                break

    dispatcher.feed_update.assert_awaited_once()
