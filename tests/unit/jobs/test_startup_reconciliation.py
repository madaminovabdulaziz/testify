"""Unit tests for the on-boot reconciliation sweep."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app.jobs.startup_reconciliation import reconcile_attempts


def _session_factory_mock(session: MagicMock) -> MagicMock:
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=None)
    return MagicMock(return_value=cm)


def _attempt(*, attempt_id: int, expires_at: datetime) -> SimpleNamespace:
    return SimpleNamespace(id=attempt_id, expires_at=expires_at)


async def test_reconcile_re_registers_jobs_for_each_in_progress_attempt() -> None:
    session = MagicMock()
    session.commit = AsyncMock()
    services = MagicMock()
    in_flight = [
        _attempt(attempt_id=1, expires_at=datetime.now(UTC) + timedelta(minutes=30)),
        _attempt(attempt_id=2, expires_at=datetime.now(UTC) + timedelta(minutes=5)),
    ]
    services.attempt.list_in_progress = AsyncMock(return_value=in_flight)

    container = MagicMock()
    container.session_factory = _session_factory_mock(session)
    container.services = MagicMock(return_value=services)
    container.scheduler = MagicMock()

    with patch("app.jobs.startup_reconciliation.schedule_attempt_jobs") as schedule_mock:
        await reconcile_attempts(container)

    assert schedule_mock.call_count == 2
    scheduled_ids = [call.args[1].id for call in schedule_mock.call_args_list]
    assert sorted(scheduled_ids) == [1, 2]


async def test_reconcile_finalizes_overdue_attempts_immediately() -> None:
    session = MagicMock()
    session.commit = AsyncMock()
    services = MagicMock()
    services.attempt.list_in_progress = AsyncMock(
        return_value=[
            _attempt(attempt_id=99, expires_at=datetime.now(UTC) - timedelta(minutes=5)),
        ]
    )

    container = MagicMock()
    container.session_factory = _session_factory_mock(session)
    container.services = MagicMock(return_value=services)
    container.scheduler = MagicMock()

    with (
        patch("app.jobs.startup_reconciliation.schedule_attempt_jobs"),
        patch(
            "app.jobs.startup_reconciliation._finalize_overdue",
            new_callable=AsyncMock,
        ) as finalize_mock,
    ):
        await reconcile_attempts(container)

    finalize_mock.assert_awaited_once_with(99)


async def test_reconcile_handles_empty_list_cleanly() -> None:
    session = MagicMock()
    session.commit = AsyncMock()
    services = MagicMock()
    services.attempt.list_in_progress = AsyncMock(return_value=[])

    container = MagicMock()
    container.session_factory = _session_factory_mock(session)
    container.services = MagicMock(return_value=services)
    container.scheduler = MagicMock()

    with patch("app.jobs.startup_reconciliation.schedule_attempt_jobs") as schedule_mock:
        await reconcile_attempts(container)

    schedule_mock.assert_not_called()
