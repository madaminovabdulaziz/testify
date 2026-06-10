"""Web-panel wiring: jinja2 env, routes, static files.

``setup_web(app, container)`` is called from ``make_app`` so the panel
rides the same aiohttp application (and the same Railway deploy) as the
Telegram webhook.
"""

from __future__ import annotations

from pathlib import Path

import aiohttp_jinja2
import jinja2
from aiohttp import web

from app.core.container import Container
from app.web.handlers import images, login, tests
from app.web.keys import KEY_CONTAINER


def setup_web(app: web.Application, container: Container) -> None:
    """Mount the admin panel onto the shared aiohttp application."""
    app[KEY_CONTAINER] = container
    aiohttp_jinja2.setup(
        app,
        loader=jinja2.PackageLoader("app.web", "templates"),
        autoescape=True,
    )

    router = app.router
    router.add_get("/panel/login", login.login_page)
    router.add_post("/panel/login", login.login_submit)
    router.add_post("/panel/logout", login.logout)

    router.add_get("/panel/", tests.tests_list)
    router.add_get("/panel", tests.panel_root_redirect)
    router.add_post("/panel/tests/new", tests.create_draft)
    router.add_get(r"/panel/tests/{test_id:\d+}", tests.test_detail)
    router.add_post(r"/panel/tests/{test_id:\d+}", tests.save_draft)
    router.add_post(r"/panel/tests/{test_id:\d+}/publish", tests.publish)
    router.add_post(r"/panel/tests/{test_id:\d+}/delete", tests.delete_draft)
    router.add_post(r"/panel/tests/{test_id:\d+}/duplicate", tests.duplicate)
    router.add_post(
        r"/panel/tests/{test_id:\d+}/questions/{position:\d+}/image", images.upload_image
    )
    router.add_get(
        r"/panel/tests/{test_id:\d+}/questions/{position:\d+}/image", images.image_preview
    )

    router.add_static(
        "/panel/static/",
        path=Path(__file__).parent / "static",
        name="panel_static",
    )
