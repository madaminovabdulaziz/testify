"""Unit tests for the hourly pending-receipt reminder sweep."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app.jobs.pending_receipt_reminder import pending_receipt_reminder_job


def _receipt(*, rid: int, age_hours: int) -> SimpleNamespace:
    return SimpleNamespace(
        id=rid,
        created_at=datetime.now(UTC) - timedelta(hours=age_hours),
    )


def _session_factory_mock(session: MagicMock) -> MagicMock:
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=None)
    return MagicMock(return_value=cm)


def _build_container(
    *,
    pending_by_threshold: dict[int, list[SimpleNamespace]] | None = None,
    redis_set_returns: bool = True,
    notification_raises: Exception | None = None,
) -> tuple[MagicMock, MagicMock, MagicMock]:
    """Return (container, services, redis) for the test to assert against."""
    pending_by_threshold = pending_by_threshold or {}

    session = MagicMock()
    session.commit = AsyncMock()

    services = MagicMock()

    async def fake_list(cutoff: datetime, *, limit: int = 100):
        # Pick by the (now - cutoff) hour count rounded.
        age_hours = round((datetime.now(UTC) - cutoff).total_seconds() / 3600)
        return pending_by_threshold.get(age_hours, [])

    services.receipt.list_pending_older_than = AsyncMock(side_effect=fake_list)
    if notification_raises is not None:
        services.notification.send_to_admin_group = AsyncMock(side_effect=notification_raises)
    else:
        services.notification.send_to_admin_group = AsyncMock()

    redis = MagicMock()
    redis.set = AsyncMock(return_value=redis_set_returns)
    redis.delete = AsyncMock()

    container = MagicMock()
    container.session_factory = _session_factory_mock(session)
    container.services = MagicMock(return_value=services)
    container.redis = redis
    return container, services, redis


async def test_reminder_sends_one_message_per_overdue_receipt_per_threshold() -> None:
    container, services, _redis = _build_container(
        pending_by_threshold={
            24: [_receipt(rid=1, age_hours=25), _receipt(rid=2, age_hours=30)],
            72: [_receipt(rid=2, age_hours=80)],
            168: [],
        }
    )

    with patch(
        "app.jobs.pending_receipt_reminder.get_runtime_container",
        return_value=container,
    ):
        await pending_receipt_reminder_job()

    # 3 messages sent (receipt 1 at 24h, receipt 2 at 24h, receipt 2 at 72h).
    assert services.notification.send_to_admin_group.await_count == 3


async def test_reminder_dedupes_via_redis_marker() -> None:
    container, services, _redis = _build_container(
        pending_by_threshold={24: [_receipt(rid=1, age_hours=25)]},
        redis_set_returns=False,  # marker already exists
    )

    with patch(
        "app.jobs.pending_receipt_reminder.get_runtime_container",
        return_value=container,
    ):
        await pending_receipt_reminder_job()

    services.notification.send_to_admin_group.assert_not_awaited()


async def test_reminder_releases_marker_when_send_fails() -> None:
    container, _services, redis = _build_container(
        pending_by_threshold={24: [_receipt(rid=1, age_hours=25)]},
        notification_raises=RuntimeError("telegram down"),
    )

    with patch(
        "app.jobs.pending_receipt_reminder.get_runtime_container",
        return_value=container,
    ):
        await pending_receipt_reminder_job()

    redis.delete.assert_awaited()
    delete_args = redis.delete.await_args.args
    assert "receipt_reminder:1:24h" in delete_args[0]


async def test_reminder_uses_nx_and_ttl_on_marker() -> None:
    container, _services, redis = _build_container(
        pending_by_threshold={24: [_receipt(rid=1, age_hours=25)]}
    )

    with patch(
        "app.jobs.pending_receipt_reminder.get_runtime_container",
        return_value=container,
    ):
        await pending_receipt_reminder_job()

    set_kwargs = redis.set.await_args.kwargs
    assert set_kwargs.get("nx") is True
    assert set_kwargs.get("ex", 0) >= 24 * 60 * 60  # at least one day TTL
