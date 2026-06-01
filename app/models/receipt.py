"""payment_receipts table — see DATABASE_SPEC §5.3."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Index,
    String,
    text,
)
from sqlalchemy.dialects.mysql import BIGINT
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.models.types import UTCDateTime


class PaymentReceipt(Base):
    """A bank-receipt screenshot uploaded by a user awaiting approval.

    Holds a perceptual hash (``image_phash``) for duplicate detection and
    a reference back to the admin-group message so the original notification
    can be edited in place when the receipt is resolved.
    """

    __tablename__ = "payment_receipts"

    id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        primary_key=True,
        autoincrement=True,
    )
    user_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        # Spec abbreviates ``payment_receipts`` to ``receipts`` in constraint
        # names. Pin them all explicitly to match §5.3.
        ForeignKey("users.id", ondelete="RESTRICT", name="fk_receipts__user_id"),
        nullable=False,
    )
    telegram_file_id: Mapped[str] = mapped_column(String(256), nullable=False)
    telegram_file_unique_id: Mapped[str] = mapped_column(String(64), nullable=False)
    # Stored as SIGNED 64-bit so asyncmy's literal escaper accepts the
    # full bit range (it rejects Python ints > 2^63 - 1). The hasher
    # reinterprets the unsigned pHash as signed before insert; the bit
    # pattern is preserved, so Hamming-distance math is unaffected.
    # Diverges from DATABASE_SPEC §5.3 (BIGINT UNSIGNED) for driver
    # compatibility.
    image_phash: Mapped[int | None] = mapped_column(BigInteger(), nullable=True)
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default="pending",
    )
    rejection_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    reviewed_by_admin_id: Mapped[int | None] = mapped_column(
        BIGINT(unsigned=True),
        ForeignKey("admins.id", ondelete="SET NULL", name="fk_receipts__reviewed_by"),
        nullable=True,
    )
    # The original admin-group message id, so the bot can later edit it to
    # show "✅ Одобрено @admin" and strip the buttons (ARCHITECTURE_SPEC §8.3).
    admin_notification_message_id: Mapped[int | None] = mapped_column(
        BigInteger(),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(fsp=6),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP(6)"),
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(UTCDateTime(fsp=6), nullable=True)

    __table_args__ = (
        Index("ix_receipts__user_id_status", "user_id", "status"),
        Index("ix_receipts__status_created", "status", "created_at"),
        Index("ix_receipts__phash", "image_phash"),
        CheckConstraint(
            "status IN ('pending','approved','rejected')",
            name="ck_receipts__status_enum",
        ),
        CheckConstraint(
            "status <> 'rejected' OR rejection_reason IS NOT NULL",
            name="ck_receipts__rejected_has_reason",
        ),
        # NOTE: a ``ck_receipts__reviewed_has_admin`` check on
        # ``reviewed_by_admin_id`` was originally specified in
        # DATABASE_SPEC §5.3 but MySQL 8.4 rejects it (error 3823):
        # a CHECK cannot reference a column targeted by an
        # ``ON DELETE SET NULL`` FK, because the SET-NULL could
        # violate the CHECK. We chose SET NULL deliberately (§7,
        # "an admin leaving shouldn't blank the receipt"), so the
        # CHECK is dropped. The same invariant is enforced by
        # ``ReceiptService.approve`` / ``reject`` — they always set
        # ``reviewed_by_admin_id`` atomically with the status flip.
        {
            "mysql_engine": "InnoDB",
            "mysql_charset": "utf8mb4",
            "mysql_collate": "utf8mb4_unicode_ci",
        },
    )
