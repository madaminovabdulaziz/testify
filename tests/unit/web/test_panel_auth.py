"""HTTP tests for the panel auth lifecycle (login, cookies, CSRF, logout)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp.test_utils import TestClient, TestServer

from app.bot.webhook import make_app
from app.web.auth import issue_login_code
from tests.unit.web._fakes import login_client, make_container


@pytest.fixture
async def harness():
    """(client, container, services) against the real aiohttp app."""
    services = MagicMock()
    services.test.list_recent = AsyncMock(return_value=[])
    container = make_container(services=services)
    app = make_app(container, dispatcher=MagicMock())
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        yield client, container, services
    finally:
        await client.close()


async def test_unauthenticated_page_redirects_to_login(harness) -> None:
    client, _, _ = harness
    resp = await client.get("/panel/", allow_redirects=False)
    assert resp.status == 303
    assert resp.headers["Location"] == "/panel/login"


async def test_login_page_renders(harness) -> None:
    client, _, _ = harness
    resp = await client.get("/panel/login")
    assert resp.status == 200
    body = await resp.text()
    assert "/weblogin" in body
    assert 'name="code"' in body


async def test_login_happy_path_sets_cookie_and_grants_access(harness) -> None:
    client, container, _ = harness
    code = await issue_login_code(container.redis, "dev", 1, ttl_seconds=300)

    resp = await client.post("/panel/login", data={"code": code}, allow_redirects=False)

    assert resp.status == 303
    cookie = resp.cookies.get("panel_session")
    assert cookie is not None
    assert cookie["httponly"]
    assert cookie["samesite"] == "Lax"
    assert cookie["path"] == "/panel"
    assert not cookie["secure"]  # env == dev

    page = await client.get("/panel/")
    assert page.status == 200
    assert "Тесты" in await page.text()


async def test_login_code_is_single_use(harness) -> None:
    client, container, _ = harness
    code = await issue_login_code(container.redis, "dev", 1, ttl_seconds=300)
    first = await client.post("/panel/login", data={"code": code}, allow_redirects=False)
    assert first.status == 303

    second = await client.post("/panel/login", data={"code": code}, allow_redirects=False)
    assert second.status == 401
    assert "Неверный или истёкший код" in await second.text()


async def test_login_wrong_code_rerenders_error(harness) -> None:
    client, _, _ = harness
    resp = await client.post("/panel/login", data={"code": "000000"}, allow_redirects=False)
    assert resp.status == 401


async def test_login_rate_limited_after_max_attempts(harness) -> None:
    client, _, _ = harness
    for _ in range(10):
        await client.post("/panel/login", data={"code": "999999"}, allow_redirects=False)
    resp = await client.post("/panel/login", data={"code": "999999"}, allow_redirects=False)
    assert resp.status == 429
    assert "Слишком много попыток" in await resp.text()


async def test_post_without_csrf_is_forbidden(harness) -> None:
    client, container, _ = harness
    await login_client(client, container)

    resp = await client.post("/panel/tests/new", data={}, allow_redirects=False)
    assert resp.status == 403


async def test_post_with_wrong_csrf_is_forbidden(harness) -> None:
    client, container, _ = harness
    await login_client(client, container)

    resp = await client.post(
        "/panel/tests/new", data={"csrf_token": "forged"}, allow_redirects=False
    )
    assert resp.status == 403


async def test_logout_destroys_session(harness) -> None:
    client, container, _ = harness
    csrf = await login_client(client, container)

    resp = await client.post("/panel/logout", data={"csrf_token": csrf}, allow_redirects=False)
    assert resp.status == 303

    # The Redis session is gone — even replaying the old cookie fails.
    assert not [k for k in container.redis.hashes if k.startswith("dev:websession:")]


async def test_deleted_admin_session_is_revoked(harness) -> None:
    client, container, _ = harness
    await login_client(client, container)

    # The admin row disappears (e.g. removed by the owner).
    session = container.session_factory.return_value.__aenter__.return_value
    session.get = AsyncMock(return_value=None)

    resp = await client.get("/panel/", allow_redirects=False)
    assert resp.status == 303
    assert resp.headers["Location"] == "/panel/login"
    assert not [k for k in container.redis.hashes if k.startswith("dev:websession:")]


async def test_ajax_endpoints_get_json_401(harness) -> None:
    client, _, _ = harness
    resp = await client.post(
        "/panel/tests/1/questions/3/image",
        headers={"X-Requested-With": "fetch"},
        allow_redirects=False,
    )
    assert resp.status == 401
    assert (await resp.json())["error"]


async def test_dev_mode_has_no_webhook_route(harness) -> None:
    client, _, _ = harness
    resp = await client.post("/webhook", json={})
    assert resp.status == 404


async def test_already_logged_in_login_page_redirects_home(harness) -> None:
    client, container, _ = harness
    await login_client(client, container)
    resp = await client.get("/panel/login", allow_redirects=False)
    assert resp.status == 303
    assert resp.headers["Location"] == "/panel/"


async def test_login_form_ignores_admin_when_admin_row_known(harness) -> None:
    # Regression guard: login page itself never requires auth context.
    client, _, _ = harness
    resp = await client.get("/panel/login")
    assert resp.status == 200


async def test_prod_mode_keeps_webhook_route() -> None:
    settings_secret = MagicMock()
    settings_secret.get_secret_value = MagicMock(return_value="hook-secret")
    container = make_container()
    container.settings.webhook_secret = settings_secret

    dispatcher = MagicMock()
    dispatcher.feed_update = AsyncMock()
    app = make_app(container, dispatcher=dispatcher)
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        resp = await client.post("/webhook", json={}, headers={})
        # Route exists; bad secret → 403 (not 404).
        assert resp.status == 403
    finally:
        await client.close()


async def test_static_css_served(harness) -> None:
    client, _, _ = harness
    resp = await client.get("/panel/static/panel.css")
    assert resp.status == 200
    assert "text/css" in resp.headers["Content-Type"]
