"""Integration test for :class:`SettingsService` against real MySQL.

Redis is mocked because the cache layer is well-covered by unit tests
and we don't want to add a second testcontainer for the integration run.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.settings_repository import SettingsRepository
from app.services.settings_service import SettingsService


def _fake_redis() -> AsyncMock:
    """No-op Redis that always misses the cache and accepts writes silently."""
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    redis.set = AsyncMock()
    redis.delete = AsyncMock()
    return redis


async def test_settings_service_main_flow(session: AsyncSession) -> None:
    repo = SettingsRepository(session)
    redis = _fake_redis()
    svc = SettingsService(repo, redis)

    # absent key
    assert await svc.get("welcome_message") is None

    # write + read-through
    await svc.set("welcome_message", "Здравствуйте!", admin_id=None)
    redis.delete.assert_awaited_with("settings:welcome_message")

    assert await svc.get("welcome_message") == "Здравствуйте!"
    # The cache miss path populates the cache for future reads.
    redis.set.assert_awaited()

    # second write — cache invalidated, DB reflects the update
    await svc.set("welcome_message", "Добро пожаловать!", admin_id=None)
    assert await svc.get("welcome_message") == "Добро пожаловать!"

    # get_all snapshot
    await svc.set("payment_amount", "150000", admin_id=None)
    snapshot = await svc.get_all()
    assert snapshot == {
        "welcome_message": "Добро пожаловать!",
        "payment_amount": "150000",
    }
