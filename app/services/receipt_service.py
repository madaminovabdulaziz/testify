"""Receipt submission and admin review.

ARCHITECTURE_SPEC §8.2 + PRODUCT_BLUEPRINT §8.2/§8.3/§14.1. The service
owns three things the handler is not allowed to decide on its own:

* the 3-pending-per-user submission cap (anti-abuse — §14.1)
* perceptual-hash duplicate detection (silent reject vs admin warning)
* the idempotent approve/reject transitions (two admins tapping at once)

Per ARCHITECTURE_SPEC §4: services never call ``bot.send_message``. We
return ``ReceiptSubmissionResult`` / the updated ``User`` so the handler
can dispatch the admin-group post and the DM via ``NotificationService``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

import structlog

from app.exceptions import (
    ReceiptAlreadyPendingError,
    ReceiptAlreadyProcessedError,
    ReceiptLimitExceededError,
    ReceiptUserBannedError,
)
from app.models.admin import Admin
from app.models.receipt import PaymentReceipt
from app.models.user import User
from app.repositories.receipt_repository import ReceiptRepository
from app.repositories.user_repository import UserRepository
from app.services.image_hasher import DEFAULT_HAMMING_THRESHOLD, ImageHasher
from app.services.settings_service import SettingsService
from app.services.user_service import UserService

logger = structlog.get_logger()


class ReceiptWarning(StrEnum):
    """Soft anti-fraud signals attached to a submission for the admin to weigh.

    None of these block submission — they annotate the admin-group caption so
    the human reviewer looks twice (PRODUCT_BLUEPRINT §8.2 / §14). The handler
    maps each to its Russian caption line; keeping them as codes keeps copy
    out of the service layer.
    """

    DUPLICATE_APPROVED = "duplicate_approved"  # collides with an approved receipt
    DUPLICATE_REJECTED = "duplicate_rejected"  # collides with a previously rejected one (M10)
    DUPLICATE_PENDING_OTHER = "duplicate_pending_other"  # another user's pending receipt (M9)
    PHONE_REUSED = "phone_reused"  # phone already on a different approved user (M8)


@dataclass(frozen=True)
class ReceiptSubmissionResult:
    """What ``ReceiptService.submit`` hands back to the caller.

    ``warnings`` carries the soft anti-fraud signals (pHash collisions, reused
    phone) the handler renders into the admin-group caption.
    """

    receipt: PaymentReceipt
    warnings: tuple[ReceiptWarning, ...]


class ReceiptService:
    """Submit / approve / reject receipts. Stateless across requests."""

    def __init__(
        self,
        receipt_repository: ReceiptRepository,
        user_repository: UserRepository,
        image_hasher: ImageHasher,
        settings_service: SettingsService,
        user_service: UserService,
        *,
        max_pending_per_user: int = 3,
    ) -> None:
        self._receipts = receipt_repository
        self._users = user_repository
        self._hasher = image_hasher
        self._settings = settings_service
        # Injected (not built here) so it can be decorated for caching/audit
        # without ReceiptService bypassing it (CODE_REVIEW L7). Must share the
        # request session — the container wires the same instance.
        self._user_service = user_service
        self._max_pending = max_pending_per_user

    # ---------- submission ----------

    async def submit(
        self,
        user: User,
        *,
        photo_file_id: str,
        photo_file_unique_id: str,
        photo_bytes: bytes,
    ) -> ReceiptSubmissionResult:
        """Validate + persist a fresh receipt from the student.

        Raises:
            ReceiptLimitExceededError: user already has the maximum allowed
                pending receipts (anti-abuse cap from PRODUCT_BLUEPRINT §8.2).
            ReceiptAlreadyPendingError: the same image is already queued
                for this user (silent dedup — they probably just retried).
            ValueError: ``photo_bytes`` doesn't decode as an image.
        """
        pending_count = await self._receipts.count_pending_for_user(user.id)
        if pending_count >= self._max_pending:
            raise ReceiptLimitExceededError()

        image_phash = self._hasher.hash(photo_bytes)
        threshold = await self._settings.get_int(
            "phash_hamming_threshold", default=DEFAULT_HAMMING_THRESHOLD
        )

        # Silent dedup against this user's own pending queue — they probably
        # just re-sent the same screenshot.
        for existing in await self._receipts.list_pending_for_user(user.id):
            if existing.image_phash is None:
                continue
            if self._hasher.is_similar(image_phash, existing.image_phash, threshold):
                raise ReceiptAlreadyPendingError()

        warnings = await self._scan_for_warnings(user, image_phash, threshold)

        receipt = await self._receipts.create(
            user_id=user.id,
            telegram_file_id=photo_file_id,
            telegram_file_unique_id=photo_file_unique_id,
            image_phash=image_phash,
        )

        # User funnel: pending_payment / rejected → pending_approval.
        await self._user_service.mark_pending_approval(user.id)

        logger.info(
            "receipt_submitted",
            user_id=user.id,
            receipt_id=receipt.id,
            warnings=[w.value for w in warnings],
        )

        return ReceiptSubmissionResult(receipt=receipt, warnings=warnings)

    async def _scan_for_warnings(
        self,
        user: User,
        image_phash: int,
        threshold: int,
    ) -> tuple[ReceiptWarning, ...]:
        """Collect the soft anti-fraud signals for a new submission.

        None of these block the submission — the admin makes the final call.
        Covers pHash collisions against approved / rejected (any user) and
        other users' pending receipts (CODE_REVIEW M9/M10), plus the reused-
        phone check (M8 / PRODUCT_BLUEPRINT §14.2).
        """
        warnings: set[ReceiptWarning] = set()

        # Approved / rejected, any user (including this user's own rejected
        # ones — the "rejected, then resubmit a tweaked copy" fraud pattern).
        for other in await self._receipts.list_with_phash(("approved", "rejected")):
            if other.image_phash is None:
                continue
            if self._hasher.is_similar(image_phash, other.image_phash, threshold):
                if other.status == "approved":
                    warnings.add(ReceiptWarning.DUPLICATE_APPROVED)
                else:
                    warnings.add(ReceiptWarning.DUPLICATE_REJECTED)

        # Pending receipts from *other* users (two people submitting the same
        # screenshot at once).
        for other in await self._receipts.list_with_phash(("pending",), exclude_user_id=user.id):
            if other.image_phash is None:
                continue
            if self._hasher.is_similar(image_phash, other.image_phash, threshold):
                warnings.add(ReceiptWarning.DUPLICATE_PENDING_OTHER)
                break

        # Phone already attached to a *different* approved student.
        if user.phone:
            twin = await self._users.find_approved_by_phone(user.phone, exclude_user_id=user.id)
            if twin is not None:
                warnings.add(ReceiptWarning.PHONE_REUSED)

        # Deterministic order so the admin caption and tests are stable.
        order = list(ReceiptWarning)
        return tuple(sorted(warnings, key=order.index))

    # ---------- admin review ----------

    async def approve(self, receipt_id: int, admin_user: Admin) -> User:
        """Move a pending receipt to ``approved`` and promote the student.

        Returns the now-approved :class:`~app.models.user.User` row so the
        caller can hand it to the notification layer.

        Raises:
            ReceiptAlreadyProcessedError: the receipt was already resolved
                (by this admin, another admin, or doesn't exist).
            ReceiptUserBannedError: the receipt belongs to a banned user;
                approving would un-ban them, so we refuse and let the whole
                transaction roll back (the receipt stays pending).
        """
        rowcount = await self._receipts.mark_approved(
            receipt_id, reviewed_by_admin_id=admin_user.id
        )
        if rowcount == 0:
            raise ReceiptAlreadyProcessedError()

        receipt = await self._receipts.get_by_id(receipt_id)
        assert receipt is not None  # we just updated it

        user_rows = await self._user_service.mark_approved(receipt.user_id)
        approved_user = await self._users.get_by_id(receipt.user_id)
        # A 0 rowcount means the status-guarded UPDATE flipped nothing. That
        # is either a banned user (the guard blocked it — an error we must
        # not let stand) or an already-approved user whose row simply didn't
        # change (legitimate: a second receipt approved for the same
        # student). The re-read tells them apart: only a banned/missing user
        # is an error, and raising rolls back the receipt approval above too,
        # so the ban stays intact and the receipt stays pending.
        if user_rows == 0 and (approved_user is None or approved_user.status == "banned"):
            raise ReceiptUserBannedError()
        assert approved_user is not None

        logger.info(
            "receipt_approved",
            receipt_id=receipt_id,
            admin_id=admin_user.id,
            user_id=approved_user.id,
        )
        return approved_user

    async def reject(
        self,
        receipt_id: int,
        admin_user: Admin,
        reason: str,
    ) -> User:
        """Move a pending receipt to ``rejected`` and return the user to ``rejected`` status.

        Raises:
            ValueError: ``reason`` is empty or whitespace.
            ReceiptAlreadyProcessedError: the receipt was already resolved.
        """
        cleaned_reason = reason.strip()
        if not cleaned_reason:
            raise ValueError("rejection reason must be a non-empty string")

        rowcount = await self._receipts.mark_rejected(
            receipt_id,
            reviewed_by_admin_id=admin_user.id,
            reason=cleaned_reason,
        )
        if rowcount == 0:
            raise ReceiptAlreadyProcessedError()

        receipt = await self._receipts.get_by_id(receipt_id)
        assert receipt is not None
        await self._user_service.mark_rejected(receipt.user_id)
        rejected_user = await self._users.get_by_id(receipt.user_id)
        assert rejected_user is not None

        logger.info(
            "receipt_rejected",
            receipt_id=receipt_id,
            admin_id=admin_user.id,
            user_id=rejected_user.id,
        )
        return rejected_user

    async def count_pending_for_user(self, user_id: int) -> int:
        """Number of receipts the user currently has waiting for review."""
        return await self._receipts.count_pending_for_user(user_id)

    async def attach_admin_notification_message(self, receipt_id: int, message_id: int) -> None:
        """Remember the message_id of the admin-group posting for later edit."""
        await self._receipts.set_admin_notification_message_id(receipt_id, message_id)

    async def list_pending_older_than(
        self,
        cutoff: datetime,
        *,
        limit: int = 100,
    ) -> list[PaymentReceipt]:
        """Pending receipts whose ``created_at < cutoff`` — feeds the reminder sweep."""
        return await self._receipts.list_pending_older_than(cutoff, limit=limit)

    async def list_pending_unnotified(
        self,
        cutoff: datetime,
        *,
        limit: int = 20,
    ) -> list[PaymentReceipt]:
        """Pending receipts that were never posted to the admin group.

        ``admin_notification_message_id`` is NULL when the original
        admin-group post failed; the reminder sweep re-posts these so
        they stay reviewable from Telegram.
        """
        return await self._receipts.list_pending_unnotified(cutoff, limit=limit)

    async def count_by_status(self) -> dict[str, int]:
        """``{status: count}`` across all receipts — feeds /stats."""
        return await self._receipts.count_by_status()
