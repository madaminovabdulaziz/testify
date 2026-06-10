"""Panel login/logout: redeem a bot-issued code, mint/destroy a session."""

from __future__ import annotations

import aiohttp_jinja2
import structlog
from aiohttp import web

from app.web.auth import (
    LOGIN_PATH,
    REQUEST_SESSION,
    SESSION_COOKIE,
    WebSession,
    check_login_rate,
    client_ip,
    consume_login_code,
    create_session,
    destroy_session,
    load_session,
    login_required,
)
from app.web.keys import KEY_CONTAINER

logger = structlog.get_logger()

_BAD_CODE = "Неверный или истёкший код. Отправьте боту /weblogin и получите новый."
_RATE_LIMITED = "Слишком много попыток. Подождите 10 минут."


async def login_page(request: web.Request) -> web.StreamResponse:
    """GET /panel/login — code-entry form (redirects home when already logged in)."""
    container = request.app[KEY_CONTAINER]
    settings = container.settings
    token = request.cookies.get(SESSION_COOKIE)
    if token is not None:
        session = await load_session(
            container.redis,
            settings.env,
            token,
            ttl_seconds=settings.web_session_ttl_days * 86400,
        )
        if session is not None:
            raise web.HTTPSeeOther(location="/panel/")
    return aiohttp_jinja2.render_template("login.html", request, {"error": None})


async def login_submit(request: web.Request) -> web.StreamResponse:
    """POST /panel/login — rate-limit, redeem the code, set the session cookie."""
    container = request.app[KEY_CONTAINER]
    settings = container.settings

    ip = client_ip(request)
    allowed = await check_login_rate(
        container.redis, settings.env, ip, max_attempts=settings.web_login_max_attempts
    )
    if not allowed:
        logger.warning("panel_login_rate_limited", ip=ip)
        return aiohttp_jinja2.render_template(
            "login.html", request, {"error": _RATE_LIMITED}, status=429
        )

    form = await request.post()
    raw_code = form.get("code")
    code = raw_code.strip() if isinstance(raw_code, str) else ""
    admin_id = await consume_login_code(container.redis, settings.env, code)
    if admin_id is None:
        return aiohttp_jinja2.render_template(
            "login.html", request, {"error": _BAD_CODE}, status=401
        )

    ttl = settings.web_session_ttl_days * 86400
    token, _csrf = await create_session(container.redis, settings.env, admin_id, ttl_seconds=ttl)
    logger.info("panel_login_ok", admin_id=admin_id)

    response = web.HTTPSeeOther(location="/panel/")
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=ttl,
        httponly=True,
        secure=settings.env != "dev",
        samesite="Lax",
        path="/panel",
    )
    raise response


@login_required
async def logout(request: web.Request) -> web.StreamResponse:
    """POST /panel/logout — drop the session and clear the cookie."""
    container = request.app[KEY_CONTAINER]
    session: WebSession = request[REQUEST_SESSION]
    await destroy_session(container.redis, container.settings.env, session.token)
    response = web.HTTPSeeOther(location=LOGIN_PATH)
    response.del_cookie(SESSION_COOKIE, path="/panel")
    raise response
