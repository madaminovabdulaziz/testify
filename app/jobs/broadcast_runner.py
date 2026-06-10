"""Durable announcement delivery loop.

Reads recipients in small cursor-ordered batches, copies the source
message to each at Telegram's safe rate, and commits progress
(counters + ``last_user_id`` cursor) after every batch. A crash or
deploy mid-run therefore loses at most one batch of progress — and
:func:`resume_unfinished_broadcasts` picks the run back up at startup,
so an announcement is never silently half-delivered. The worst case is
a re-send of one batch's worth of messages (bounded by ``_BATCH_SIZE``),
which we prefer over silently missing students.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import structlog
from aiogram.exceptions import TelegramAPIError

from app.utils.rate_limiter import AsyncTokenBucket

if TYPE_CHECKING:
    from app.core.container import Container

logger = structlog.get_logger()

# Cursor commit granularity. Small enough that a crash re-sends at most a
# couple dozen duplicates; large enough that MySQL isn't hammered.
_BATCH_SIZE = 25

# Strong refs so the event loop doesn't GC in-flight broadcast tasks
# (same pattern as the publish broadcast in admin/tests.py).
_RUNNING: set[asyncio.Task[None]] = set()

_DRAIN_TIMEOUT_SECONDS = 30.0


def spawn_broadcast(container: Container, broadcast_id: int) -> None:
    """Fire-and-forget the delivery loop for one broadcast."""
    task = asyncio.create_task(run_broadcast(container, broadcast_id))
    _RUNNING.add(task)
    task.add_done_callback(_RUNNING.discard)


async def wait_for_running_broadcasts(timeout: float = _DRAIN_TIMEOUT_SECONDS) -> None:
    """Give in-flight announcement runs a grace window on graceful shutdown.

    Runs that don't finish in time are abandoned — safely, because the
    startup resume picks them up from the last committed cursor.
    """
    pending = [t for t in _RUNNING if not t.done()]
    if not pending:
        return
    logger.info("awaiting_running_announcements", count=len(pending))
    _, still_pending = await asyncio.wait(pending, timeout=timeout)
    if still_pending:
        logger.warning("announcements_resumed_on_next_start", count=len(still_pending))


async def resume_unfinished_broadcasts(container: Container) -> None:
    """Startup reconciliation: restart delivery of every ``in_progress`` broadcast."""
    async with container.session_factory() as session:
        unfinished = await container.services(session).broadcast.list_in_progress()
    for broadcast in unfinished:
        logger.info(
            "announcement_resumed",
            broadcast_id=broadcast.id,
            already_sent=broadcast.sent_count,
        )
        spawn_broadcast(container, broadcast.id)


async def run_broadcast(container: Container, broadcast_id: int) -> None:
    """Deliver one announcement to every approved student, resumably.

    Each batch runs in its own short DB session: read the broadcast row +
    the next recipients, copy the message to each (sequentially, under the
    token bucket — sequencing is what keeps the cursor exact), then commit
    counters + cursor. Telegram I/O dominates the wall clock; at 25 msg/s
    a 1000-student announcement completes in ~40 s.
    """
    bucket = AsyncTokenBucket(rate=container.settings.broadcast_messages_per_second)
    try:
        while True:
            done = await _run_one_batch(container, broadcast_id, bucket)
            if done:
                return
    except Exception:
        # Unexpected failure (DB down, etc.). The row stays in_progress, so
        # the next startup resumes from the last committed cursor.
        logger.exception("announcement_run_crashed", broadcast_id=broadcast_id)


async def _run_one_batch(
    container: Container,
    broadcast_id: int,
    bucket: AsyncTokenBucket,
) -> bool:
    """Process one batch. Returns True when the broadcast is finished."""
    async with container.session_factory() as session:
        services = container.services(session)
        broadcast = await services.broadcast.get(broadcast_id)
        if broadcast is None or broadcast.status != "in_progress":
            return True

        recipients = await services.broadcast.next_recipients(
            broadcast.last_user_id, limit=_BATCH_SIZE
        )
        if not recipients:
            completed = await services.broadcast.mark_completed(broadcast_id)
            await session.commit()
            if completed:
                await _report_completion(container, broadcast_id)
            return True

        sent, blocked, errors = (
            broadcast.sent_count,
            broadcast.blocked_count,
            (broadcast.error_count),
        )
        cursor = broadcast.last_user_id
        for user_id, telegram_id in recipients:
            await bucket.acquire()
            status = await services.notification.copy_broadcast_message(
                user_id,
                telegram_id,
                from_chat_id=broadcast.source_chat_id,
                message_id=broadcast.source_message_id,
            )
            if status == "sent":
                sent += 1
            elif status == "blocked":
                blocked += 1
            else:
                errors += 1
            cursor = user_id

        await services.broadcast.record_progress(
            broadcast_id,
            sent_count=sent,
            blocked_count=blocked,
            error_count=errors,
            last_user_id=cursor,
        )
        await session.commit()
    return False


async def _report_completion(container: Container, broadcast_id: int) -> None:
    """Post the delivery summary back to the chat that launched the broadcast."""
    async with container.session_factory() as session:
        broadcast = await container.services(session).broadcast.get(broadcast_id)
    if broadcast is None or broadcast.report_chat_id is None:
        return
    text = (
        f"✅ Рассылка #{broadcast.id} завершена.\n"
        f"Отправлено: {broadcast.sent_count}\n"
        f"Заблокировали бота: {broadcast.blocked_count}\n"
        f"Ошибок: {broadcast.error_count}"
    )
    try:
        await container.bot.send_message(broadcast.report_chat_id, text)
    except TelegramAPIError:
        logger.warning("announcement_report_failed", broadcast_id=broadcast_id)
    logger.info(
        "announcement_completed",
        broadcast_id=broadcast_id,
        sent=broadcast.sent_count,
        blocked=broadcast.blocked_count,
        errors=broadcast.error_count,
    )
