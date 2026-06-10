"""Typed aiohttp app keys for the web panel.

Public counterpart to the private ``_KEY_CONTAINER`` in
``app/bot/webhook.py`` — web handlers import this one so we never reach
across modules for a private name. ``setup_web`` stores the same
container instance under both keys.
"""

from __future__ import annotations

from aiohttp import web

from app.core.container import Container

KEY_CONTAINER: web.AppKey[Container] = web.AppKey("web_container", Container)
