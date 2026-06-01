"""Admin identity / membership service.

A thin facade over :class:`AdminRepository` so handlers go through the
service layer instead of reaching a repository directly — preserving the
``handlers → services → repositories`` boundary (ARCHITECTURE_SPEC §4.1 /
CODE_REVIEW M21). The methods are straight delegations today; the seam is
what matters (caching, auditing, RBAC can land here later without touching
handlers).
"""

from __future__ import annotations

from app.models.admin import Admin
from app.repositories.admin_repository import AdminRepository


class AdminService:
    """Read/write access to the ``admins`` table for the handler layer."""

    def __init__(self, admin_repository: AdminRepository) -> None:
        self._admins = admin_repository

    async def get_by_telegram_id(self, telegram_id: int) -> Admin | None:
        """Return the admin row for ``telegram_id``, or ``None`` if not an admin."""
        return await self._admins.get_by_telegram_id(telegram_id)

    async def get_by_id(self, admin_id: int) -> Admin | None:
        """Return the admin row for a surrogate id, or ``None``."""
        return await self._admins.get_by_id(admin_id)

    async def list_all(self) -> list[Admin]:
        """Every registered admin (for an admin-listing command)."""
        return await self._admins.list_all()
