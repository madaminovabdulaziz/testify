"""Send messages to users, the admin group, and broadcast new tests.

ARCHITECTURE_SPEC §8.5 + §12. Three concerns live here:

* respecting Telegram's ~30 msg/sec global cap (token bucket from
  :mod:`app.utils.rate_limiter`) plus a bounded concurrency semaphore;
* deciding what to do per Telegram error (``Forbidden`` → mark the user
  ``bot_blocked``; ``RetryAfter`` → sleep its hint and retry, up to a few
  attempts; everything else → log and count as an error);
* keeping DB writes (``mark_bot_blocked``) **serialized** at the end of
  a broadcast batch because ``AsyncSession`` is not safe across
  concurrent tasks.

This service does *not* compose Russian copy itself — callers pass the
finalized text (typically pulled from ``SettingsService``) along with
the recipient list.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from dataclasses import dataclass
from typing import Any

import structlog
from aiogram import Bot
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramForbiddenError,
    TelegramRetryAfter,
)
from aiogram.types import InlineKeyboardMarkup, Message

from app.repositories.user_repository import UserRepository
from app.utils.rate_limiter import AsyncTokenBucket

logger = structlog.get_logger()

# Per-recipient send is retried on repeated ``TelegramRetryAfter`` instead of
# dropping after a single retry (CODE_REVIEW H15). We honour Telegram's own
# ``retry_after`` hint each time, capped so a pathological value can't stall
# the whole broadcast.
_MAX_SEND_ATTEMPTS = 3
_MAX_RETRY_SLEEP_SECONDS = 60


@dataclass(frozen=True)
class BroadcastSummary:
    """Per-status counts after a broadcast finishes."""

    sent: int
    blocked: int
    errors: int

    @property
    def total(self) -> int:
        return self.sent + self.blocked + self.errors


class NotificationService:
    """Telegram-facing send helpers. Service-layer code never calls ``bot`` directly."""

    def __init__(
        self,
        bot: Bot,
        user_repository: UserRepository,
        *,
        admin_group_id: int,
        broadcast_concurrency: int = 20,
        broadcast_rate_per_second: int = 25,
    ) -> None:
        self._bot = bot
        self._users = user_repository
        self._admin_group_id = admin_group_id
        self._broadcast_concurrency = broadcast_concurrency
        self._broadcast_rate_per_second = broadcast_rate_per_second

    # ---------- broadcast ----------

    async def broadcast_new_test(
        self,
        text: str,
        recipients: list[tuple[int, int]],
        *,
        reply_markup: InlineKeyboardMarkup | None = None,
    ) -> BroadcastSummary:
        """Fan out ``text`` to every ``(user_id, telegram_id)`` in ``recipients``.

        Caps in-flight sends at ``broadcast_concurrency`` and the
        outgoing rate at ``broadcast_rate_per_second`` (both per
        ARCHITECTURE_SPEC §12). Users that raise
        ``TelegramForbiddenError`` are flagged ``bot_blocked=True``
        serially after the parallel send phase completes.

        Returns a :class:`BroadcastSummary` the admin handler reports
        back as "sent / blocked / errors".
        """
        sem = asyncio.Semaphore(self._broadcast_concurrency)
        bucket = AsyncTokenBucket(rate=self._broadcast_rate_per_second)

        async def send_one(user_id: int, telegram_id: int) -> tuple[str, int]:
            async with sem:
                # Retry on repeated 429s up to _MAX_SEND_ATTEMPTS, sleeping the
                # server-supplied retry_after each time (capped). A burst of
                # throttling no longer means silently dropped notifications
                # (CODE_REVIEW H15).
                for attempt in range(1, _MAX_SEND_ATTEMPTS + 1):
                    await bucket.acquire()
                    try:
                        await self._bot.send_message(telegram_id, text, reply_markup=reply_markup)
                        return ("sent", user_id)
                    except TelegramForbiddenError:
                        return ("blocked", user_id)
                    except TelegramRetryAfter as exc:
                        if attempt == _MAX_SEND_ATTEMPTS:
                            logger.warning(
                                "broadcast_retry_exhausted",
                                user_id=user_id,
                                telegram_id=telegram_id,
                                attempts=attempt,
                            )
                            return ("error", user_id)
                        await asyncio.sleep(min(exc.retry_after, _MAX_RETRY_SLEEP_SECONDS))
                    except TelegramAPIError:
                        logger.exception(
                            "broadcast_error",
                            user_id=user_id,
                            telegram_id=telegram_id,
                        )
                        return ("error", user_id)
                return ("error", user_id)

        results = await asyncio.gather(*(send_one(uid, tg) for uid, tg in recipients))

        # ---- serialized DB-write phase ----
        # AsyncSession is not safe for concurrent task access, so the
        # bot_blocked UPDATEs happen one-at-a-time after the parallel
        # send phase wraps up.
        blocked_user_ids = [uid for status, uid in results if status == "blocked"]
        for user_id in blocked_user_ids:
            await self._users.mark_bot_blocked(user_id)

        counts = Counter(status for status, _ in results)
        summary = BroadcastSummary(
            sent=counts.get("sent", 0),
            blocked=counts.get("blocked", 0),
            errors=counts.get("error", 0),
        )
        logger.info(
            "broadcast_finished",
            total=summary.total,
            sent=summary.sent,
            blocked=summary.blocked,
            errors=summary.errors,
        )
        return summary

    async def copy_broadcast_message(
        self,
        user_id: int,
        telegram_id: int,
        *,
        from_chat_id: int,
        message_id: int,
    ) -> str:
        """Copy one admin-composed message to a student via ``copyMessage``.

        ``copyMessage`` replays the source message verbatim — text with its
        formatting entities, photo/video/GIF with caption — without the
        "forwarded from" header, so an announcement looks like the bot wrote
        it. Returns ``"sent"`` / ``"blocked"`` / ``"error"``; a blocked user
        is flagged ``bot_blocked`` immediately (the caller's session must be
        open). 429s are retried with Telegram's own hint, like the test
        broadcast (CODE_REVIEW H15).
        """
        for attempt in range(1, _MAX_SEND_ATTEMPTS + 1):
            try:
                await self._bot.copy_message(
                    chat_id=telegram_id,
                    from_chat_id=from_chat_id,
                    message_id=message_id,
                )
                return "sent"
            except TelegramForbiddenError:
                await self._users.mark_bot_blocked(user_id)
                return "blocked"
            except TelegramRetryAfter as exc:
                if attempt == _MAX_SEND_ATTEMPTS:
                    logger.warning(
                        "announcement_retry_exhausted",
                        user_id=user_id,
                        telegram_id=telegram_id,
                    )
                    return "error"
                await asyncio.sleep(min(exc.retry_after, _MAX_RETRY_SLEEP_SECONDS))
            except TelegramAPIError:
                logger.exception(
                    "announcement_copy_failed",
                    user_id=user_id,
                    telegram_id=telegram_id,
                )
                return "error"
        return "error"  # pragma: no cover — loop always returns

    # ---------- admin group ----------

    async def send_to_admin_group(
        self,
        text: str,
        *,
        photo_file_id: str | None = None,
        reply_markup: InlineKeyboardMarkup | None = None,
    ) -> Message:
        """Post a notification to the admin group; returns the resulting message."""
        if photo_file_id is not None:
            return await self._bot.send_photo(
                self._admin_group_id,
                photo=photo_file_id,
                caption=text,
                reply_markup=reply_markup,
            )
        return await self._bot.send_message(self._admin_group_id, text, reply_markup=reply_markup)

    async def edit_admin_group_message(
        self,
        message_id: int,
        *,
        caption: str,
        reply_markup: InlineKeyboardMarkup | None = None,
    ) -> Any:
        """Edit the caption / buttons of a previously-posted admin-group photo."""
        return await self._bot.edit_message_caption(
            chat_id=self._admin_group_id,
            message_id=message_id,
            caption=caption,
            reply_markup=reply_markup,
        )

    # ---------- per-user DM ----------

    async def send_user_message(
        self,
        user_id: int,
        telegram_id: int,
        text: str,
        *,
        reply_markup: InlineKeyboardMarkup | None = None,
    ) -> bool:
        """DM a user; flag ``bot_blocked`` on ``Forbidden``. Returns whether delivered.

        Centralizes the "send + mark blocked" pattern (CODE_REVIEW M7): a
        student who blocked the bot is flagged the first time we try to reach
        them (e.g. the approval DM), not only when the next broadcast fails.
        The caller must hold an open session — the ``bot_blocked`` UPDATE
        writes through this service's ``UserRepository``.
        """
        try:
            await self._bot.send_message(telegram_id, text, reply_markup=reply_markup)
            return True
        except TelegramForbiddenError:
            logger.info("user_dm_forbidden", user_id=user_id, telegram_id=telegram_id)
            await self._users.mark_bot_blocked(user_id)
            return False
        except TelegramAPIError:
            logger.exception("user_dm_failed", user_id=user_id, telegram_id=telegram_id)
            return False

    async def send_time_warning(self, telegram_id: int, text: str) -> None:
        """DM ``text`` to one user. Swallows ``Forbidden`` so a single block doesn't crash the job."""
        try:
            await self._bot.send_message(telegram_id, text)
        except TelegramForbiddenError:
            logger.info("send_time_warning_forbidden", telegram_id=telegram_id)
        except TelegramAPIError:
            logger.exception("send_time_warning_failed", telegram_id=telegram_id)
