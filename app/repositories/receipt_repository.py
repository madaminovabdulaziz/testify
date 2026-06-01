"""Data access for the ``payment_receipts`` table.

Drives the receipt approval flow (ARCHITECTURE_SPEC §8.2) and the pending
receipt reminder sweep (§11.2). Hamming-distance duplicate matching is
done in the service layer because v1 just scans the approved-hash list
(see DATABASE_SPEC §10.3 / §11.5 for the future BK-tree upgrade path).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select, update

from app.models.receipt import PaymentReceipt
from app.repositories.base import BaseRepository
from app.utils.datetime import now_utc


class ReceiptRepository(BaseRepository):
    """Reads + writes for ``payment_receipts``."""

    async def get_by_id(self, receipt_id: int) -> PaymentReceipt | None:
        """Fetch one receipt by id, refreshed from the DB.

        ``populate_existing=True`` so a re-read after ``mark_approved`` /
        ``mark_rejected`` reflects the new ``reviewed_at`` rather than a stale
        identity-map copy (see AttemptRepository.get_by_id for the full why).
        """
        return await self._session.get(PaymentReceipt, receipt_id, populate_existing=True)

    async def create(
        self,
        *,
        user_id: int,
        telegram_file_id: str,
        telegram_file_unique_id: str,
        image_phash: int | None,
    ) -> PaymentReceipt:
        """Insert a brand-new ``pending`` receipt and return it."""
        receipt = PaymentReceipt(
            user_id=user_id,
            telegram_file_id=telegram_file_id,
            telegram_file_unique_id=telegram_file_unique_id,
            image_phash=image_phash,
            status="pending",
        )
        self._session.add(receipt)
        await self._session.flush()
        # Server defaults (``created_at``) are fetched automatically
        # because ``Base.__mapper_args__`` sets ``eager_defaults=True``.
        return receipt

    async def set_admin_notification_message_id(self, receipt_id: int, message_id: int) -> None:
        """Store the message id of the admin-group posting (for later edit)."""
        stmt = (
            update(PaymentReceipt)
            .where(PaymentReceipt.id == receipt_id)
            .values(admin_notification_message_id=message_id)
        )
        await self._session.execute(stmt)

    async def mark_approved(self, receipt_id: int, reviewed_by_admin_id: int) -> int:
        """Move a pending receipt to ``approved``. Returns affected row count."""
        stmt = (
            update(PaymentReceipt)
            .where(
                PaymentReceipt.id == receipt_id,
                PaymentReceipt.status == "pending",
            )
            .values(
                status="approved",
                reviewed_by_admin_id=reviewed_by_admin_id,
                reviewed_at=now_utc(),
            )
        )
        result = await self._session.execute(stmt)
        return result.rowcount

    async def mark_rejected(
        self,
        receipt_id: int,
        reviewed_by_admin_id: int,
        reason: str,
    ) -> int:
        """Move a pending receipt to ``rejected`` with the given reason. Returns rowcount."""
        stmt = (
            update(PaymentReceipt)
            .where(
                PaymentReceipt.id == receipt_id,
                PaymentReceipt.status == "pending",
            )
            .values(
                status="rejected",
                reviewed_by_admin_id=reviewed_by_admin_id,
                rejection_reason=reason,
                reviewed_at=now_utc(),
            )
        )
        result = await self._session.execute(stmt)
        return result.rowcount

    async def count_pending_for_user(self, user_id: int) -> int:
        """Count this user's ``pending`` receipts — used to enforce the 3-per-user cap."""
        stmt = (
            select(func.count())
            .select_from(PaymentReceipt)
            .where(
                PaymentReceipt.user_id == user_id,
                PaymentReceipt.status == "pending",
            )
        )
        return int((await self._session.execute(stmt)).scalar_one())

    async def list_pending_for_user(self, user_id: int) -> list[PaymentReceipt]:
        """All ``pending`` receipts for one user — used to check for re-submissions."""
        stmt = (
            select(PaymentReceipt)
            .where(
                PaymentReceipt.user_id == user_id,
                PaymentReceipt.status == "pending",
            )
            .order_by(PaymentReceipt.created_at.asc())
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def list_approved_with_phash(self) -> list[PaymentReceipt]:
        """All ``approved`` receipts that have a phash, for duplicate detection."""
        return await self.list_with_phash(("approved",))

    async def list_with_phash(
        self,
        statuses: tuple[str, ...],
        *,
        exclude_user_id: int | None = None,
    ) -> list[PaymentReceipt]:
        """Receipts in any of ``statuses`` that carry a phash, for fraud scans.

        ``exclude_user_id`` drops one user's own rows — used to scan *other*
        users' pending receipts for cross-user duplicates (CODE_REVIEW M9)
        without re-flagging the submitter's own queue.
        """
        stmt = select(PaymentReceipt).where(
            PaymentReceipt.image_phash.is_not(None),
            PaymentReceipt.status.in_(statuses),
        )
        if exclude_user_id is not None:
            stmt = stmt.where(PaymentReceipt.user_id != exclude_user_id)
        stmt = stmt.order_by(PaymentReceipt.id)
        return list((await self._session.execute(stmt)).scalars().all())

    async def list_pending_older_than(
        self,
        cutoff: datetime,
        limit: int = 100,
    ) -> list[PaymentReceipt]:
        """Pending receipts created before ``cutoff`` — feeds the reminder job."""
        stmt = (
            select(PaymentReceipt)
            .where(
                PaymentReceipt.status == "pending",
                PaymentReceipt.created_at < cutoff,
            )
            .order_by(PaymentReceipt.created_at.asc())
            .limit(limit)
        )
        return list((await self._session.execute(stmt)).scalars().all())

    async def count_by_status(self) -> dict[str, int]:
        """``{status: count}`` across all receipts — feeds /stats."""
        stmt = select(PaymentReceipt.status, func.count()).group_by(PaymentReceipt.status)
        rows = await self._session.execute(stmt)
        return {status: int(count) for status, count in rows}
