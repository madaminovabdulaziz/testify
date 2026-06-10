"""Web-panel authentication: bot-issued login codes, Redis sessions, CSRF.

Login flow: an admin sends ``/weblogin`` to the bot (private chat), gets a
6-digit single-use code (Redis, short TTL), and enters it on the panel's
login page. A successful login mints a fresh random session token —
clients can never supply their own, so session fixation is impossible.

Redis key schema (env-prefixed like the rest of the app; the shared
client returns ``bytes``, decode everywhere):

    {env}:weblogin:{code}           -> admin_id        TTL web_login_code_ttl_seconds
    {env}:websession:{token}        -> hash{admin_id, csrf}   sliding TTL
    {env}:weblogin_attempts:{ip}    -> counter         600 s window
"""

from __future__ import annotations

import hmac
import secrets
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from functools import wraps

import structlog
from aiohttp import web
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.config import Settings
from app.repositories.admin_repository import AdminRepository
from app.web.keys import KEY_CONTAINER

logger = structlog.get_logger()

SESSION_COOKIE = "panel_session"
LOGIN_PATH = "/panel/login"

_ATTEMPT_WINDOW_SECONDS = 600

# Request keys populated by @login_required for downstream handlers.
REQUEST_ADMIN = "admin"
REQUEST_SESSION = "web_session"


@dataclass(frozen=True)
class WebSession:
    """One authenticated panel session loaded from Redis."""

    token: str
    admin_id: int
    csrf: str


# ---------- login codes ----------


async def issue_login_code(redis: Redis, env: str, admin_id: int, *, ttl_seconds: int) -> str:
    """Mint a single-use 6-digit login code for ``/weblogin``.

    ``SET NX`` guards against the (one-in-a-million) collision with another
    admin's outstanding code — on collision we just roll again.
    """
    for _ in range(5):
        code = f"{secrets.randbelow(10**6):06d}"
        stored = await redis.set(f"{env}:weblogin:{code}", str(admin_id), nx=True, ex=ttl_seconds)
        if stored:
            return code
    raise RuntimeError("could not allocate a unique login code")  # pragma: no cover


async def consume_login_code(redis: Redis, env: str, code: str) -> int | None:
    """Redeem a code → admin_id, atomically deleting it (single-use via GETDEL)."""
    if not code.isdigit() or len(code) != 6:
        return None
    raw = await redis.getdel(f"{env}:weblogin:{code}")
    if raw is None:
        return None
    return int(raw.decode() if isinstance(raw, bytes) else raw)


async def check_login_rate(redis: Redis, env: str, ip: str, *, max_attempts: int) -> bool:
    """Per-IP login throttle. Returns True when the attempt is allowed.

    Fails CLOSED on Redis errors — this is an auth surface, unlike the bot's
    fail-open throttle middleware where availability wins.
    """
    key = f"{env}:weblogin_attempts:{ip}"
    try:
        count = await redis.incr(key)
        if count == 1:
            await redis.expire(key, _ATTEMPT_WINDOW_SECONDS)
        return int(count) <= max_attempts
    except RedisError:
        logger.exception("login_rate_check_failed")
        return False


# ---------- sessions ----------


async def create_session(
    redis: Redis, env: str, admin_id: int, *, ttl_seconds: int
) -> tuple[str, str]:
    """Mint a fresh session; returns ``(token, csrf)``."""
    token = secrets.token_urlsafe(32)
    csrf = secrets.token_urlsafe(32)
    key = f"{env}:websession:{token}"
    await redis.hset(key, mapping={"admin_id": str(admin_id), "csrf": csrf})
    await redis.expire(key, ttl_seconds)
    return token, csrf


async def load_session(
    redis: Redis, env: str, token: str, *, ttl_seconds: int
) -> WebSession | None:
    """Look a session up by cookie token; refreshes the sliding TTL."""
    key = f"{env}:websession:{token}"
    data = await redis.hgetall(key)
    if not data:
        return None
    decoded = {
        (k.decode() if isinstance(k, bytes) else k): (v.decode() if isinstance(v, bytes) else v)
        for k, v in data.items()
    }
    try:
        admin_id = int(decoded["admin_id"])
        csrf = decoded["csrf"]
    except (KeyError, ValueError):
        await redis.delete(key)
        return None
    await redis.expire(key, ttl_seconds)
    return WebSession(token=token, admin_id=admin_id, csrf=csrf)


async def destroy_session(redis: Redis, env: str, token: str) -> None:
    """Log out: drop the session key."""
    await redis.delete(f"{env}:websession:{token}")


# ---------- URL + request helpers ----------


def panel_base_url(settings: Settings) -> str:
    """Absolute panel URL for the /weblogin reply.

    Explicit ``panel_base_url`` wins; otherwise derive scheme+host from
    ``webhook_url``; in dev fall back to localhost.
    """
    if settings.panel_base_url is not None:
        return str(settings.panel_base_url).rstrip("/") + "/panel/"
    if settings.webhook_url is not None:
        url = settings.webhook_url
        port = f":{url.port}" if url.port not in (None, 80, 443) else ""
        return f"{url.scheme}://{url.host}{port}/panel/"
    return "http://localhost:8080/panel/"


def client_ip(request: web.Request) -> str:
    """Best-effort client IP: first X-Forwarded-For hop (Railway edge) or peer."""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.remote or "unknown"


def _wants_json(request: web.Request) -> bool:
    """AJAX requests (image upload) get JSON errors instead of redirects."""
    return request.headers.get("X-Requested-With") == "fetch" or request.path.endswith("/image")


# ---------- the auth gate ----------

Handler = Callable[[web.Request], Awaitable[web.StreamResponse]]


def login_required(handler: Handler) -> Handler:
    """Decorator gating a panel route behind a valid session (+ CSRF on POST).

    Deliberately a decorator, not app middleware — the webhook and /healthz
    routes must not pay for (or regress on) any of this.
    """

    @wraps(handler)
    async def wrapper(request: web.Request) -> web.StreamResponse:
        container = request.app[KEY_CONTAINER]
        settings = container.settings
        ttl = settings.web_session_ttl_days * 86400

        token = request.cookies.get(SESSION_COOKIE)
        session = (
            await load_session(container.redis, settings.env, token, ttl_seconds=ttl)
            if token
            else None
        )
        if session is None:
            if _wants_json(request):
                return web.json_response({"error": "Сессия истекла. Войдите заново."}, status=401)
            raise web.HTTPSeeOther(location=LOGIN_PATH)

        # Revocation check: the admin row must still exist.
        async with container.session_factory() as db:
            admin = await AdminRepository(db).get_by_id(session.admin_id)
        if admin is None:
            await destroy_session(container.redis, settings.env, session.token)
            raise web.HTTPSeeOther(location=LOGIN_PATH)

        if request.method == "POST":
            if request.content_type.startswith("multipart/"):
                # Don't consume the multipart stream here — the token rides
                # in a header for AJAX uploads.
                submitted: str | None = request.headers.get("X-CSRF-Token")
            else:
                form = await request.post()  # cached; handlers re-read freely
                raw = form.get("csrf_token")
                submitted = raw if isinstance(raw, str) else None
            if not (submitted and hmac.compare_digest(submitted, session.csrf)):
                logger.warning("panel_csrf_rejected", admin_id=session.admin_id)
                if _wants_json(request):
                    return web.json_response({"error": "CSRF-токен недействителен."}, status=403)
                raise web.HTTPForbidden(text="CSRF token invalid")

        request[REQUEST_SESSION] = session
        request[REQUEST_ADMIN] = admin
        return await handler(request)

    return wrapper
