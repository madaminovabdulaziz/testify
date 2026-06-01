"""Cross-table /stats aggregator.

Composes counters from the user/receipt/test/attempt services so the
admin /stats command returns one ready-to-render DTO and the handler
stays a pass-through. DATABASE_SPEC §10.16 — the per-status counts come
from per-table COUNT queries (four round-trips, called manually by an
admin, performance unconcerned).
"""

from __future__ import annotations

from dataclasses import dataclass

from app.services.attempt_service import AttemptService
from app.services.receipt_service import ReceiptService
from app.services.test_service import TestService
from app.services.user_service import UserService


@dataclass(frozen=True)
class StatsSnapshot:
    """All the numbers /stats shows, materialized once for one render."""

    total_users: int
    users_by_status: dict[str, int]
    receipts_by_status: dict[str, int]
    tests_by_status: dict[str, int]
    attempts_by_status: dict[str, int]


class StatsService:
    """Stateless. Builds a :class:`StatsSnapshot` per /stats invocation."""

    def __init__(
        self,
        user_service: UserService,
        receipt_service: ReceiptService,
        test_service: TestService,
        attempt_service: AttemptService,
    ) -> None:
        self._users = user_service
        self._receipts = receipt_service
        self._tests = test_service
        self._attempts = attempt_service

    async def snapshot(self) -> StatsSnapshot:
        """Collect all counters. Caller renders."""
        return StatsSnapshot(
            total_users=await self._users.count_total(),
            users_by_status=await self._users.count_by_status(),
            receipts_by_status=await self._receipts.count_by_status(),
            tests_by_status=await self._tests.count_by_status(),
            attempts_by_status=await self._attempts.count_by_status(),
        )
