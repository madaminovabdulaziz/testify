"""Unit tests for web-panel auth primitives (codes, sessions, rate limit)."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from redis.exceptions import RedisError

from app.web.auth import (
    check_login_rate,
    consume_login_code,
    create_session,
    destroy_session,
    issue_login_code,
    load_session,
    panel_base_url,
)


class _FakeRedis:
    """Dict-backed stand-in implementing the few primitives auth uses."""

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


# ---------- login codes ----------


async def test_issue_and_consume_login_code_roundtrip() -> None:
    redis = _FakeRedis()
    code = await issue_login_code(redis, "dev", 7, ttl_seconds=300)

    assert len(code) == 6 and code.isdigit()
    assert redis.expirations[f"dev:weblogin:{code}"] == 300
    assert await consume_login_code(redis, "dev", code) == 7
    # Single-use: the second redemption fails.
    assert await consume_login_code(redis, "dev", code) is None


async def test_consume_rejects_malformed_codes_without_touching_redis() -> None:
    redis = MagicMock()
    redis.getdel = AsyncMock()
    assert await consume_login_code(redis, "dev", "abc") is None
    assert await consume_login_code(redis, "dev", "12345") is None
    assert await consume_login_code(redis, "dev", "1234567") is None
    redis.getdel.assert_not_awaited()


# ---------- sessions ----------


async def test_session_lifecycle() -> None:
    redis = _FakeRedis()
    token, csrf = await create_session(redis, "dev", 7, ttl_seconds=100)

    session = await load_session(redis, "dev", token, ttl_seconds=100)
    assert session is not None
    assert session.admin_id == 7
    assert session.csrf == csrf

    await destroy_session(redis, "dev", token)
    assert await load_session(redis, "dev", token, ttl_seconds=100) is None


async def test_load_session_refreshes_sliding_ttl() -> None:
    redis = _FakeRedis()
    token, _ = await create_session(redis, "dev", 7, ttl_seconds=100)
    redis.expirations.clear()

    await load_session(redis, "dev", token, ttl_seconds=999)

    assert redis.expirations[f"dev:websession:{token}"] == 999


async def test_load_session_drops_corrupt_entries() -> None:
    redis = _FakeRedis()
    redis.hashes["dev:websession:bad"] = {"admin_id": "not-an-int"}
    assert await load_session(redis, "dev", "bad", ttl_seconds=100) is None
    assert "dev:websession:bad" not in redis.hashes


# ---------- rate limiting ----------


async def test_login_rate_allows_until_threshold() -> None:
    redis = _FakeRedis()
    for _ in range(10):
        assert await check_login_rate(redis, "dev", "1.2.3.4", max_attempts=10) is True
    assert await check_login_rate(redis, "dev", "1.2.3.4", max_attempts=10) is False


async def test_login_rate_fails_closed_on_redis_error() -> None:
    redis = MagicMock()
    redis.incr = AsyncMock(side_effect=RedisError("down"))
    assert await check_login_rate(redis, "dev", "1.2.3.4", max_attempts=10) is False


# ---------- panel_base_url ----------


def test_panel_base_url_prefers_explicit_setting() -> None:
    from pydantic import HttpUrl

    settings = SimpleNamespace(
        panel_base_url=HttpUrl("https://panel.example.com"),
        webhook_url=HttpUrl("https://bot.example.com/webhook/secret"),
    )
    assert panel_base_url(settings) == "https://panel.example.com/panel/"


def test_panel_base_url_derives_from_webhook_url() -> None:
    from pydantic import HttpUrl

    settings = SimpleNamespace(
        panel_base_url=None,
        webhook_url=HttpUrl("https://bot.example.com/webhook/secret-path"),
    )
    assert panel_base_url(settings) == "https://bot.example.com/panel/"


def test_panel_base_url_falls_back_to_localhost_in_dev() -> None:
    settings = SimpleNamespace(panel_base_url=None, webhook_url=None)
    assert panel_base_url(settings) == "http://localhost:8080/panel/"
