"""Scheduler-invoked callables for attempt timer warnings + auto-submit.

ARCHITECTURE_SPEC §11. Four jobs per active attempt:

* ``attempt_warn_10min_job`` / ``attempt_warn_5min_job`` /
  ``attempt_warn_1min_job`` — DM the user the corresponding warning,
  idempotent via the ``warning_<slot>_sent_at`` column on ``attempts``.
* ``attempt_expire_job`` — finalize the attempt with ``status='expired'``
  via :meth:`AttemptService.finish`, then DM the user the result.

Each function:

1. resolves the live process container via :mod:`app.jobs._runtime`
   (APScheduler can't serialize it through ``kwargs``);
2. opens its own ``AsyncSession`` — no middleware in this context;
3. claims the slot atomically via a status-guarded UPDATE — the
   rowcount tells us whether we own the dispatch;
4. commits the claim **before** sending the Telegram message so a
   failed DM doesn't trigger a re-send on the next reconciliation
   sweep.

Trade-off (4 vs 3): we'd rather drop one warning to a network blip than
spam the user twice on bot restart. The DB row tells the truth either
way.
"""

from __future__ import annotations

import structlog

from app.bot.views.result_screen import render_result_screen
from app.jobs._runtime import get_runtime_container
from app.utils.datetime import now_utc

logger = structlog.get_logger()


# Per-slot Russian fallback text. The seeded ``settings`` rows (see
# DATABASE_SPEC §8) override these; fallbacks let the bot survive a
# wiped settings table per PRODUCT_BLUEPRINT §15.2.
_FALLBACK_WARN_10MIN = "⏱ Осталось 10 минут до конца теста."
_FALLBACK_WARN_5MIN = "⏱ Осталось 5 минут!"
_FALLBACK_WARN_1MIN = "⏱ Осталась 1 минута!"
_FALLBACK_AUTO_SUBMITTED = "⏰ Время вышло. Тест автоматически завершён."


# ---------- warning jobs ----------


async def attempt_warn_10min_job(attempt_id: int) -> None:
    """Fire 10 minutes before ``expires_at`` — DM the user the 10-min warning."""
    await _send_warning(
        attempt_id, slot="10min", settings_key="msg_warning_10min", fallback=_FALLBACK_WARN_10MIN
    )


async def attempt_warn_5min_job(attempt_id: int) -> None:
    """Fire 5 minutes before ``expires_at`` — DM the user the 5-min warning."""
    await _send_warning(
        attempt_id, slot="5min", settings_key="msg_warning_5min", fallback=_FALLBACK_WARN_5MIN
    )


async def attempt_warn_1min_job(attempt_id: int) -> None:
    """Fire 1 minute before ``expires_at`` — DM the user the 1-min warning."""
    await _send_warning(
        attempt_id, slot="1min", settings_key="msg_warning_1min", fallback=_FALLBACK_WARN_1MIN
    )


async def _send_warning(
    attempt_id: int,
    *,
    slot: str,
    settings_key: str,
    fallback: str,
) -> None:
    """Shared body for the three warning jobs.

    ``slot`` is the ``WarningSlot`` literal (``"10min"``/``"5min"``/
    ``"1min"``); ``settings_key`` is the row in ``settings`` that holds
    the user-facing Russian text.
    """
    container = get_runtime_container()
    async with container.session_factory() as session:
        services = container.services(session)
        claimed = await services.attempt.claim_warning_slot(attempt_id, slot)  # type: ignore[arg-type]
        if claimed is None:
            await session.rollback()
            logger.info("attempt_warning_skipped", attempt_id=attempt_id, slot=slot)
            return

        user = await services.user.get_user(claimed.user_id)
        if user is None:
            await session.rollback()
            logger.warning(
                "attempt_warning_user_missing",
                attempt_id=attempt_id,
                user_id=claimed.user_id,
            )
            return

        if user.status == "banned":
            # Banned mid-test (CODE_REVIEW H20). The ban cleanup normally
            # finalizes the attempt first so the slot claim above no-ops, but
            # if this job won the race, just don't DM the banned user. Keep
            # the claim committed so we don't retry on the next sweep.
            await session.commit()
            logger.info("attempt_warning_skipped_banned", attempt_id=attempt_id)
            return

        text = (await services.settings.get(settings_key)) or fallback
        telegram_id = user.telegram_id
        # Commit the slot claim BEFORE the network send so a transient
        # bot/Telegram failure can't snowball into a re-send loop after
        # restart.
        await session.commit()

    # Outside the DB transaction.
    await services.notification.send_time_warning(telegram_id, text)
    logger.info("attempt_warning_sent", attempt_id=attempt_id, slot=slot)


# ---------- expire job ----------


async def attempt_expire_job(attempt_id: int) -> None:
    """Fire at ``expires_at`` — finalize the attempt with status='expired'.

    Idempotent. ``AttemptService.finish`` is itself idempotent (status-
    guarded UPDATE) and now reports whether *this* call owned the
    finalization. We only DM the user when we did — if a manual finish (or
    another worker) flipped the row first, ``owned_finalization`` is False
    and we stay quiet so the student doesn't get a "time's up" + duplicate
    result moments after seeing their own submit (CODE_REVIEW H2).
    """
    container = get_runtime_container()
    async with container.session_factory() as session:
        services = container.services(session)

        # Cheap existence guard so a (theoretically) missing row degrades
        # gracefully instead of raising out of the scheduler.
        attempt = await services.attempt.get_attempt(attempt_id)
        if attempt is None:
            await session.rollback()
            logger.warning("attempt_expire_missing_row", attempt_id=attempt_id)
            return

        result = await services.attempt.finish(attempt_id, reason="expired")

        if not result.owned_finalization:
            # A manual finish (or a racing worker) finalized first; the user
            # already has their result. Nothing to send.
            await session.commit()
            logger.info("attempt_expire_noop_not_owner", attempt_id=attempt_id)
            return

        user = await services.user.get_user(result.attempt.user_id)
        if user is None:
            await session.rollback()
            logger.warning(
                "attempt_expire_user_missing",
                attempt_id=attempt_id,
                user_id=result.attempt.user_id,
            )
            return

        if user.status == "banned":
            # Finalize (already done above) but don't DM a banned user
            # (CODE_REVIEW H20).
            await session.commit()
            logger.info("attempt_expire_skipped_dm_banned", attempt_id=attempt_id)
            return

        invite_link = await services.settings.get("group_invite_link")
        auto_text = (await services.settings.get("msg_auto_submitted")) or _FALLBACK_AUTO_SUBMITTED
        result_screen = render_result_screen(
            result.attempt,
            result.scores,
            group_invite_link=invite_link,
        )
        telegram_id = user.telegram_id
        await session.commit()

    # First send the brief "time's up" notice, then the full result
    # screen. Both use send_time_warning's forgiving error handling
    # (it swallows TelegramForbiddenError).
    await services.notification.send_time_warning(telegram_id, auto_text)
    try:
        await container.bot.send_message(
            telegram_id,
            result_screen.text,
            reply_markup=result_screen.reply_markup,
        )
    except Exception:
        # The notification service swallowed the first one; the result
        # screen is best-effort too. The DB already records the
        # finalized attempt.
        logger.exception("attempt_expire_result_dm_failed", attempt_id=attempt_id)

    logger.info("attempt_expire_finalized", attempt_id=attempt_id)


# ---------- recurring safety-net sweep ----------


async def expired_attempt_sweep_job() -> None:
    """Finalize any ``in_progress`` attempt already past its ``expires_at``.

    Registered to run every minute (ARCHITECTURE_SPEC §10.15). It is the
    backstop for a lost per-attempt expire job — Redis flush/eviction, a
    jobstore write that never landed, or an event loop blocked past the
    misfire-grace window. Without it, a lost timer leaves the attempt
    ``in_progress`` forever and the ``(user_id, test_id)`` unique constraint
    permanently bricks the student out of retaking the test (CODE_REVIEW C4).

    Delegates each id to :func:`attempt_expire_job`, which is idempotent
    (status-guarded ``finish`` + ownership-gated DM), so an attempt that the
    real timer finalizes a moment later is a harmless no-op.
    """
    container = get_runtime_container()
    async with container.session_factory() as session:
        services = container.services(session)
        expired_ids = await services.attempt.list_expired_in_progress(now_utc())

    if not expired_ids:
        return

    finalized = 0
    for attempt_id in expired_ids:
        try:
            await attempt_expire_job(attempt_id)
            finalized += 1
        except Exception:
            # One bad row must not abort the sweep for the rest.
            logger.exception("expired_attempt_sweep_item_failed", attempt_id=attempt_id)

    logger.info("expired_attempt_sweep_done", swept=len(expired_ids), finalized=finalized)
