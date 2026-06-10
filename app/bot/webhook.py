"""aiohttp app exposing the Telegram webhook + a /healthz probe.

ARCHITECTURE_SPEC §13. We deliberately *also* verify the
``X-Telegram-Bot-Api-Secret-Token`` header inside the app even though
nginx restricts the path — belt and braces.
"""

from __future__ import annotations

import asyncio

import structlog
from aiogram import Bot, Dispatcher
from aiogram.types import Update
from aiohttp import web
from sqlalchemy import text

from app.core.container import Container
from app.web.setup import setup_web

logger = structlog.get_logger()

# nginx forwards up to 20 MB (media-group photo callbacks etc.); aiohttp
# defaults to 1 MB and would 413 large updates (CODE_REVIEW H11).
_MAX_REQUEST_BYTES = 20 * 1024 * 1024

# Strong refs to in-flight update-processing tasks so the event loop (which
# only keeps weak refs) doesn't GC them mid-flight.
_PENDING_UPDATES: set[asyncio.Task[None]] = set()

# Typed aiohttp app keys (silences aiohttp's NotAppKeyWarning).
_KEY_CONTAINER: web.AppKey[Container] = web.AppKey("container", Container)
_KEY_DISPATCHER: web.AppKey[Dispatcher] = web.AppKey("dispatcher", Dispatcher)


async def webhook_handler(request: web.Request) -> web.Response:
    """POST {webhook_path} — verifies the secret header then routes the update."""
    container: Container = request.app[_KEY_CONTAINER]
    dispatcher: Dispatcher = request.app[_KEY_DISPATCHER]
    bot: Bot = container.bot

    expected = container.settings.webhook_secret.get_secret_value()
    received = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
    if received != expected:
        logger.warning("webhook_bad_secret")
        return web.Response(status=403)

    try:
        payload = await request.json()
        update = Update.model_validate(payload, context={"bot": bot})
    except Exception:
        logger.exception("webhook_bad_payload")
        return web.Response(status=400)

    # Process the update off the request path so a slow handler (DB pool
    # contention, a chain of Telegram sends) doesn't make Telegram wait and
    # retry the same update. We ack immediately; the work runs in the
    # background (CODE_REVIEW H9). Errors are routed to aiogram's error
    # handler inside feed_update; the wrapper just guards against anything
    # that escapes so it never crashes the loop (L9).
    task = asyncio.create_task(_process_update(dispatcher, bot, update))
    _PENDING_UPDATES.add(task)
    task.add_done_callback(_PENDING_UPDATES.discard)
    return web.Response()


async def _process_update(dispatcher: Dispatcher, bot: Bot, update: Update) -> None:
    """Feed one update through the dispatcher, swallowing anything that escapes."""
    try:
        await dispatcher.feed_update(bot=bot, update=update)
    except Exception:
        logger.exception("webhook_feed_update_failed", update_id=update.update_id)


async def healthz_handler(request: web.Request) -> web.Response:
    """GET /healthz — pings DB + Redis. Returns 200 ok / 503 fail."""
    container: Container = request.app[_KEY_CONTAINER]
    try:
        async with container.session_factory() as session:
            await session.execute(text("SELECT 1"))
        await container.redis.ping()
    except Exception:
        logger.exception("healthz_failed")
        return web.json_response({"status": "fail"}, status=503)
    return web.json_response({"status": "ok"})


def make_app(container: Container, dispatcher: Dispatcher) -> web.Application:
    """Build the aiohttp app: webhook + healthz + the web admin panel."""
    app = web.Application(client_max_size=_MAX_REQUEST_BYTES)
    app[_KEY_CONTAINER] = container
    app[_KEY_DISPATCHER] = dispatcher
    # Dev (polling) mode has no webhook secret; webhook_handler would crash
    # dereferencing it, so the route must not exist there. The panel and
    # /healthz still do — _run_polling now serves this app too.
    if container.settings.webhook_secret is not None:
        app.router.add_post(container.settings.webhook_path, webhook_handler)
    app.router.add_get("/healthz", healthz_handler)
    setup_web(app, container)
    return app
