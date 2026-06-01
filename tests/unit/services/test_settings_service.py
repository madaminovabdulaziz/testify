"""Unit tests for :class:`app.services.settings_service.SettingsService`."""

from __future__ import annotations

from unittest.mock import AsyncMock

from app.services.settings_service import CACHE_TTL_SECONDS, SettingsService


def _make_redis(cache: dict[str, bytes] | None = None) -> AsyncMock:
    """Build a minimal async Redis mock backed by an in-memory dict."""
    store = cache if cache is not None else {}
    redis = AsyncMock()

    async def fake_get(key: str) -> bytes | None:
        return store.get(key)

    async def fake_set(key: str, value: bytes, ex: int | None = None) -> None:
        store[key] = value

    async def fake_delete(key: str) -> int:
        return 1 if store.pop(key, None) is not None else 0

    redis.get.side_effect = fake_get
    redis.set.side_effect = fake_set
    redis.delete.side_effect = fake_delete
    redis._store = store  # type: ignore[attr-defined]  # tests inspect this
    return redis


async def test_get_misses_cache_falls_through_to_db_and_caches() -> None:
    cache: dict[str, bytes] = {}
    repo = AsyncMock()
    repo.get = AsyncMock(return_value="Здравствуйте!")
    redis = _make_redis(cache)

    svc = SettingsService(repo, redis)
    value = await svc.get("welcome_message")

    assert value == "Здравствуйте!"
    repo.get.assert_awaited_once_with("welcome_message")
    # Cache populated for the next caller, with the configured TTL.
    redis.set.assert_awaited_once()
    args, kwargs = redis.set.call_args
    assert args[0] == "settings:welcome_message"
    assert args[1] == "Здравствуйте!".encode()
    assert kwargs["ex"] == CACHE_TTL_SECONDS


async def test_get_hit_avoids_db() -> None:
    cache = {"settings:welcome_message": "из кэша".encode()}
    repo = AsyncMock()
    redis = _make_redis(cache)

    svc = SettingsService(repo, redis)
    value = await svc.get("welcome_message")

    assert value == "из кэша"
    repo.get.assert_not_awaited()


async def test_get_missing_key_returns_none_and_does_not_cache() -> None:
    cache: dict[str, bytes] = {}
    repo = AsyncMock()
    repo.get = AsyncMock(return_value=None)
    redis = _make_redis(cache)

    svc = SettingsService(repo, redis)
    assert await svc.get("does_not_exist") is None
    assert cache == {}
    redis.set.assert_not_awaited()


async def test_set_writes_db_and_invalidates_cache() -> None:
    cache = {"settings:welcome_message": b"old"}
    repo = AsyncMock()
    redis = _make_redis(cache)

    svc = SettingsService(repo, redis)
    await svc.set("welcome_message", "new value", admin_id=42)

    repo.set.assert_awaited_once_with("welcome_message", "new value", updated_by_admin_id=42)
    redis.delete.assert_awaited_once_with("settings:welcome_message")
    assert "settings:welcome_message" not in cache


async def test_get_all_delegates_to_repo() -> None:
    repo = AsyncMock()
    repo.get_all = AsyncMock(return_value={"a": "1", "b": "2"})
    redis = _make_redis()

    svc = SettingsService(repo, redis)
    assert await svc.get_all() == {"a": "1", "b": "2"}
    redis.get.assert_not_awaited()  # bypasses cache by design


# ---------- get_int (M23) ----------


async def test_get_int_returns_parsed_value() -> None:
    repo = AsyncMock()
    repo.get = AsyncMock(return_value="7")
    svc = SettingsService(repo, _make_redis())
    assert await svc.get_int("phash_hamming_threshold", default=5) == 7


async def test_get_int_falls_back_when_missing() -> None:
    repo = AsyncMock()
    repo.get = AsyncMock(return_value=None)
    svc = SettingsService(repo, _make_redis())
    assert await svc.get_int("phash_hamming_threshold", default=5) == 5


async def test_get_int_falls_back_when_not_an_int() -> None:
    repo = AsyncMock()
    repo.get = AsyncMock(return_value="not-a-number")
    svc = SettingsService(repo, _make_redis())
    assert await svc.get_int("phash_hamming_threshold", default=5) == 5
