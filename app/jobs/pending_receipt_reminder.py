"""Hourly sweep: nag the admin group about receipts that haven't been reviewed.

ARCHITECTURE_SPEC §11.2 / PRODUCT_BLUEPRINT §13 (the 7-day reminder).
Three reminder thresholds — 24h, 72h, 7d. For each one we list receipts
older than the threshold and post one admin-group message per receipt,
de-duplicated by a per-(receipt, threshold) Redis marker so a restart
or a re-fire on a tight schedule doesn't re-spam.

Single-process. The marker is also a safety net for any future scenario
where two workers race the same row.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING

import structlog
from aiogram.exceptions import TelegramAPIError

from app.bot.views.admin_receipt import render_admin_receipt_notification
from app.jobs._runtime import get_runtime_container
from app.utils.datetime import now_utc
from app.utils.text import html_escape

if TYPE_CHECKING:
    # Runtime import would be circular: container → attempt_service → jobs.
    from app.core.container import Container

logger = structlog.get_logger()

# How long after submission a receipt with no admin-group message id is
# considered "the original post failed" rather than "still in flight".
_UNNOTIFIED_GRACE_MINUTES = 5

_REPOST_NOTE = "⚠️ Повторная отправка — исходное уведомление не дошло до группы."

# (threshold_hours, marker_label). The label is what gets baked into the
# Redis key, so don't rename it once the bot is in prod or you'll
# re-fire previously-sent reminders.
_REMINDER_THRESHOLDS: tuple[tuple[int, str], ...] = (
    (24, "24h"),
    (72, "72h"),
    (168, "7d"),
)

# 30 days — longer than any threshold so the marker can't expire and
# allow a fresh fire before the receipt itself is resolved.
_MARKER_TTL_SECONDS = 30 * 24 * 60 * 60


def _marker_key(env: str, receipt_id: int, label: str) -> str:
    """Redis key used to dedupe per-threshold reminders (env-namespaced — L15)."""
    return f"{env}:receipt_reminder:{receipt_id}:{label}"


async def pending_receipt_reminder_job() -> None:
    """Scan ``payment_receipts`` for unresolved entries past each threshold.

    Called once an hour by the cron job registered in
    :func:`app.jobs.registry.register_recurring_jobs`. Idempotent across
    restarts via Redis markers.

    The DB read and the Telegram sends are deliberately split into two
    phases (CODE_REVIEW M15): we collect everything due under the session,
    release it, then post the reminders. Holding a pool connection across a
    slow run of admin-group sends would otherwise starve the small pool.
    """
    container = get_runtime_container()
    redis = container.redis
    env = container.settings.env
    now = now_utc()

    # Phase 0 — re-post receipts whose original admin-group notification
    # failed to send (admin_notification_message_id IS NULL). Without this,
    # such a receipt is unreviewable: the nag below only carries the id,
    # no photo and no ✅/❌ buttons. Deduped by the DB write itself — once
    # the message id is stored, the receipt drops out of the query.
    await _repost_unnotified_receipts(container, now)

    # Phase 1 — collect under the DB session, then release it.
    due: list[tuple[int, str, int]] = []  # (receipt_id, label, age_hours)
    async with container.session_factory() as session:
        services = container.services(session)
        for threshold_hours, label in _REMINDER_THRESHOLDS:
            cutoff = now - timedelta(hours=threshold_hours)
            for receipt in await services.receipt.list_pending_older_than(cutoff):
                age_hours = max(1, int((now - receipt.created_at).total_seconds() // 3600))
                due.append((receipt.id, label, age_hours))
    # ``send_to_admin_group`` only touches the bot, not the session, so it's
    # safe to use this reference after the session above has closed.
    notification = services.notification

    # Phase 2 — dedup + send with the session already released.
    total_sent = 0
    for receipt_id, label, age_hours in due:
        marker = _marker_key(env, receipt_id, label)
        # NX: only set if the key doesn't exist. Truthy iff we acquired it.
        claimed = await redis.set(marker, b"1", nx=True, ex=_MARKER_TTL_SECONDS)
        if not claimed:
            continue
        try:
            await notification.send_to_admin_group(_format_reminder(receipt_id, age_hours))
            total_sent += 1
        except Exception:
            # Release the marker so the next sweep retries this reminder
            # rather than treating it as already-sent.
            await redis.delete(marker)
            logger.exception(
                "pending_receipt_reminder_send_failed",
                receipt_id=receipt_id,
                label=label,
            )

    logger.info("pending_receipt_reminder_done", sent=total_sent)


async def _repost_unnotified_receipts(container: Container, now: datetime) -> None:
    """Retry the admin-group approval card for receipts that never got one.

    Sends the same photo + caption + ✅/❌ buttons as the original
    submission path (payment.on_receipt_photo), then stores the resulting
    message id so the approve/reject edits work and the receipt stops
    being selected. A failed send is retried on the next hourly sweep.
    """
    cutoff = now - timedelta(minutes=_UNNOTIFIED_GRACE_MINUTES)

    async with container.session_factory() as session:
        services = container.services(session)
        receipts = await services.receipt.list_pending_unnotified(cutoff)
        if not receipts:
            return
        cards = []
        for receipt in receipts:
            user = await services.user.get_user(receipt.user_id)
            if user is None:  # pragma: no cover — FK guarantees the row
                continue
            rendered = render_admin_receipt_notification(user, receipt, warnings=[_REPOST_NOTE])
            cards.append((receipt.id, receipt.telegram_file_id, rendered))
        notification = services.notification

    posted: list[tuple[int, int]] = []  # (receipt_id, message_id)
    for receipt_id, file_id, rendered in cards:
        try:
            message = await notification.send_to_admin_group(
                rendered.text,
                photo_file_id=file_id,
                reply_markup=rendered.reply_markup,
            )
        except TelegramAPIError:
            logger.exception("receipt_repost_failed", receipt_id=receipt_id)
            continue
        posted.append((receipt_id, message.message_id))
        logger.info("receipt_reposted_to_admin_group", receipt_id=receipt_id)

    if not posted:
        return
    async with container.session_factory() as session:
        services = container.services(session)
        for receipt_id, message_id in posted:
            await services.receipt.attach_admin_notification_message(receipt_id, message_id)
        await session.commit()


def _format_reminder(receipt_id: int, age_hours: int) -> str:
    """Build the admin-group nag message. Plural form is fine in Russian here."""
    receipt_token = html_escape(f"#{receipt_id}")
    return f"⏰ Чек {receipt_token} ждёт проверки {age_hours} ч."
