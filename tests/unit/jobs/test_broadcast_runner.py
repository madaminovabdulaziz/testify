"""Unit tests for the durable announcement delivery loop."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from app.jobs.broadcast_runner import resume_unfinished_broadcasts, run_broadcast


class _BroadcastRow:
    """Mutable stand-in mirroring the ORM row across batches."""

    def __init__(self, *, last_user_id: int = 0, status: str = "in_progress") -> None:
        self.id = 1
        self.status = status
        self.source_chat_id = 555
        self.source_message_id = 777
        self.report_chat_id = 555
        self.total_recipients = 3
        self.sent_count = 0
        self.blocked_count = 0
        self.error_count = 0
        self.last_user_id = last_user_id


def _container(row: _BroadcastRow, batches: dict[int, list[tuple[int, int]]]) -> MagicMock:
    """Container whose broadcast service serves cursor-keyed recipient batches."""
    container = MagicMock()
    container.settings.broadcast_messages_per_second = 1000  # don't slow tests

    session = MagicMock()
    session.commit = AsyncMock()
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=False)
    container.session_factory = MagicMock(return_value=cm)

    services = MagicMock()
    services.broadcast.get = AsyncMock(return_value=row)

    async def next_recipients(cursor: int, *, limit: int = 25):
        return batches.get(cursor, [])

    services.broadcast.next_recipients = AsyncMock(side_effect=next_recipients)

    async def record_progress(
        broadcast_id, *, sent_count, blocked_count, error_count, last_user_id
    ):
        row.sent_count = sent_count
        row.blocked_count = blocked_count
        row.error_count = error_count
        row.last_user_id = last_user_id

    services.broadcast.record_progress = AsyncMock(side_effect=record_progress)
    services.broadcast.mark_completed = AsyncMock(return_value=True)
    services.notification.copy_broadcast_message = AsyncMock(return_value="sent")

    container.services = MagicMock(return_value=services)
    container.bot.send_message = AsyncMock()
    return container


async def test_run_broadcast_processes_batches_and_completes() -> None:
    row = _BroadcastRow()
    container = _container(
        row,
        batches={0: [(1, 101), (2, 102)], 2: [(5, 105)]},  # cursor 5 → empty → done
    )

    await run_broadcast(container, 1)

    services = container.services.return_value
    assert services.notification.copy_broadcast_message.await_count == 3
    # Cursor advanced through both batches.
    assert row.last_user_id == 5
    assert row.sent_count == 3
    services.broadcast.mark_completed.assert_awaited_once_with(1)
    # Completion report posted to the launching chat.
    report = container.bot.send_message.await_args.args
    assert report[0] == 555
    assert "Отправлено: 3" in report[1]


async def test_run_broadcast_counts_blocked_and_errors() -> None:
    row = _BroadcastRow()
    container = _container(row, batches={0: [(1, 101), (2, 102), (3, 103)]})
    services = container.services.return_value
    services.notification.copy_broadcast_message = AsyncMock(
        side_effect=["sent", "blocked", "error"]
    )

    await run_broadcast(container, 1)

    assert row.sent_count == 1
    assert row.blocked_count == 1
    assert row.error_count == 1
    assert row.last_user_id == 3  # errors still advance the cursor — no infinite loop


async def test_run_broadcast_resumes_from_existing_cursor() -> None:
    # Restart scenario: 2 already sent, cursor at user 2 — only the tail goes out.
    row = _BroadcastRow(last_user_id=2)
    row.sent_count = 2
    container = _container(row, batches={2: [(5, 105)]})

    await run_broadcast(container, 1)

    services = container.services.return_value
    services.notification.copy_broadcast_message.assert_awaited_once()
    assert row.sent_count == 3


async def test_run_broadcast_noop_when_already_completed() -> None:
    row = _BroadcastRow(status="completed")
    container = _container(row, batches={})

    await run_broadcast(container, 1)

    services = container.services.return_value
    services.notification.copy_broadcast_message.assert_not_awaited()
    services.broadcast.mark_completed.assert_not_awaited()


async def test_run_broadcast_survives_crash_without_raising() -> None:
    row = _BroadcastRow()
    container = _container(row, batches={0: [(1, 101)]})
    services = container.services.return_value
    services.broadcast.next_recipients = AsyncMock(side_effect=RuntimeError("db down"))

    await run_broadcast(container, 1)  # must not raise — row stays in_progress


async def test_resume_unfinished_spawns_each() -> None:
    container = MagicMock()
    session = MagicMock()
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=False)
    container.session_factory = MagicMock(return_value=cm)
    services = MagicMock()
    services.broadcast.list_in_progress = AsyncMock(
        return_value=[SimpleNamespace(id=4, sent_count=10), SimpleNamespace(id=9, sent_count=0)]
    )
    container.services = MagicMock(return_value=services)

    spawned: list[int] = []
    from app.jobs import broadcast_runner

    original = broadcast_runner.spawn_broadcast
    broadcast_runner.spawn_broadcast = lambda c, bid: spawned.append(bid)  # type: ignore[assignment]
    try:
        await resume_unfinished_broadcasts(container)
    finally:
        broadcast_runner.spawn_broadcast = original  # type: ignore[assignment]

    assert spawned == [4, 9]
