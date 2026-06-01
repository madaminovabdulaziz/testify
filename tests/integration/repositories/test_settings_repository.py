"""Integration test for ``SettingsRepository``."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.settings_repository import SettingsRepository


async def test_settings_repository_happy_path(session: AsyncSession) -> None:
    repo = SettingsRepository(session)

    # ---------- absent key reads ``None`` ----------
    assert await repo.get("welcome_message") is None

    # ---------- insert path ----------
    await repo.set("welcome_message", "Здравствуйте!", updated_by_admin_id=None)
    assert await repo.get("welcome_message") == "Здравствуйте!"

    # ---------- update path (same key) ----------
    await repo.set("welcome_message", "Добро пожаловать!", updated_by_admin_id=None)
    assert await repo.get("welcome_message") == "Добро пожаловать!"

    # ---------- second key + get_all ----------
    await repo.set("payment_amount", "150000", updated_by_admin_id=None)
    all_settings = await repo.get_all()
    assert all_settings == {
        "welcome_message": "Добро пожаловать!",
        "payment_amount": "150000",
    }
