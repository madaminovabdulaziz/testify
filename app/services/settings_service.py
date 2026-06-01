"""Runtime-mutable settings (welcome message, payment fields, etc.) with Redis cache.

ARCHITECTURE_SPEC §8.6: reads pass through a Redis cache with a short TTL
so a render-heavy handler (the welcome message lookup fires on every
``/start``) does not hit MySQL each time. Writes invalidate the cache so
the admin sees their edit immediately rather than waiting up to a minute.
"""

from __future__ import annotations

from typing import Final

from redis.asyncio import Redis

from app.repositories.settings_repository import SettingsRepository

# 60 seconds per ARCHITECTURE_SPEC §8.6. Short enough that admin edits
# propagate quickly; long enough to absorb the hot key burst.
CACHE_TTL_SECONDS: Final[int] = 60

# Namespaced key prefix avoids collisions with aiogram's FSM keys (also
# stored in Redis DB 0).
_CACHE_PREFIX: Final[str] = "settings:"


class SettingsService:
    """Read/write the ``settings`` table with a write-through cache."""

    def __init__(self, repository: SettingsRepository, redis: Redis) -> None:
        self._repository = repository
        self._redis = redis

    async def get(self, key: str) -> str | None:
        """Return the value for ``key``; reads from Redis first, then the DB."""
        cache_key = _CACHE_PREFIX + key
        cached = await self._redis.get(cache_key)
        if cached is not None:
            # Redis client is configured with ``decode_responses=False`` —
            # decode UTF-8 here to keep the cache layer the only place that
            # cares about bytes vs str.
            return cached.decode("utf-8") if isinstance(cached, bytes) else str(cached)

        value = await self._repository.get(key)
        if value is not None:
            await self._redis.set(cache_key, value.encode("utf-8"), ex=CACHE_TTL_SECONDS)
        return value

    async def get_int(self, key: str, *, default: int) -> int:
        """Return an integer setting, falling back to ``default`` if missing/invalid.

        Lets numeric knobs live in the ``settings`` table per
        PRODUCT_BLUEPRINT §15.4 (e.g. the pHash threshold — CODE_REVIEW M23)
        without every caller re-parsing.
        """
        raw = await self.get(key)
        if raw is None:
            return default
        try:
            return int(raw.strip())
        except ValueError:
            return default

    async def set(self, key: str, value: str, admin_id: int | None) -> None:
        """Persist ``key=value`` and invalidate the cached entry."""
        await self._repository.set(key, value, updated_by_admin_id=admin_id)
        await self._redis.delete(_CACHE_PREFIX + key)

    async def get_all(self) -> dict[str, str]:
        """Snapshot every ``(key, value)`` pair straight from the DB (no caching).

        Used by ``/settings`` and ``/preview`` admin commands which already
        run in low-frequency interactive flows — paying for a fresh read
        here is the right trade-off vs cache-warming every single key.
        """
        return await self._repository.get_all()
