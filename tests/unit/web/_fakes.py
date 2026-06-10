"""Shared fakes for web-panel tests."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock


class FakeRedis:
    """Dict-backed stand-in implementing the primitives the panel uses."""

    def __init__(self) -> None:
        self.strings: dict[str, str] = {}
        self.hashes: dict[str, dict[str, str]] = {}
        self.counters: dict[str, int] = {}
        self.expirations: dict[str, int] = {}

    async def set(self, key, value, *, nx=False, ex=None):
        if nx and key in self.strings:
            return None
        self.strings[key] = str(value)
        if ex is not None:
            self.expirations[key] = ex
        return True

    async def getdel(self, key):
        value = self.strings.pop(key, None)
        return value.encode() if value is not None else None

    async def hset(self, key, mapping):
        self.hashes.setdefault(key, {}).update({k: str(v) for k, v in mapping.items()})
        return len(mapping)

    async def hgetall(self, key):
        return {k.encode(): v.encode() for k, v in self.hashes.get(key, {}).items()}

    async def expire(self, key, ttl):
        self.expirations[key] = ttl
        return True

    async def delete(self, key):
        self.strings.pop(key, None)
        self.hashes.pop(key, None)
        return 1

    async def incr(self, key):
        self.counters[key] = self.counters.get(key, 0) + 1
        return self.counters[key]


def make_settings(**overrides) -> SimpleNamespace:
    """Container.settings stand-in with panel-relevant fields."""
    base = {
        "env": "dev",
        "webhook_secret": None,
        "webhook_path": "/webhook",
        "webhook_url": None,
        "panel_base_url": None,
        "admin_group_id": -1001,
        "web_session_ttl_days": 30,
        "web_login_code_ttl_seconds": 300,
        "web_login_max_attempts": 10,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def make_container(
    *,
    redis: FakeRedis | None = None,
    services: MagicMock | None = None,
    admin: SimpleNamespace | None = None,
    settings: SimpleNamespace | None = None,
) -> MagicMock:
    """Mock Container good enough for make_app + panel handlers.

    ``admin`` feeds the login_required revocation check
    (AdminRepository.get_by_id → session.get).
    """
    container = MagicMock()
    container.settings = settings or make_settings()
    container.redis = redis or FakeRedis()

    if admin is None:
        admin = SimpleNamespace(id=1, telegram_id=111, role="owner")
    session = MagicMock()
    session.get = AsyncMock(return_value=admin)
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session_cm = MagicMock()
    session_cm.__aenter__ = AsyncMock(return_value=session)
    session_cm.__aexit__ = AsyncMock(return_value=False)
    container.session_factory = MagicMock(return_value=session_cm)

    container.services = MagicMock(return_value=services or MagicMock())
    container.bot = MagicMock()
    return container


async def login_client(client, container, *, admin_id: int = 1) -> str:
    """Drive the real login flow; returns the CSRF token for POSTs."""
    from app.web.auth import issue_login_code, load_session

    code = await issue_login_code(
        container.redis, container.settings.env, admin_id, ttl_seconds=300
    )
    resp = await client.post("/panel/login", data={"code": code}, allow_redirects=False)
    assert resp.status == 303, await resp.text()
    token = client.session.cookie_jar.filter_cookies(client.make_url("/panel/"))[
        "panel_session"
    ].value
    session = await load_session(container.redis, container.settings.env, token, ttl_seconds=300)
    assert session is not None
    return session.csrf
