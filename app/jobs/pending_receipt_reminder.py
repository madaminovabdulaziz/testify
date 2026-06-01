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

from datetime import timedelta

import structlog

from app.jobs._runtime import get_runtime_container
from app.utils.datetime import now_utc
from app.utils.text import html_escape

logger = structlog.get_logger()

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


def _format_reminder(receipt_id: int, age_hours: int) -> str:
    """Build the admin-group nag message. Plural form is fine in Russian here."""
    receipt_token = html_escape(f"#{receipt_id}")
    return f"⏰ Чек {receipt_token} ждёт проверки {age_hours} ч."
