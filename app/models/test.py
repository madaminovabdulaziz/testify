"""tests table — see DATABASE_SPEC §5.4."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    Computed,
    ForeignKey,
    Index,
    String,
    text,
)
from sqlalchemy.dialects.mysql import BIGINT, INTEGER, TINYINT
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.models.types import UTCDateTime


class Test(Base):
    """A 50-question mock attestation exam.

    Lifecycle: ``draft`` → ``active`` → ``archived``. The "exactly one
    active test" invariant is enforced both in ``TestService.publish``
    (archive-then-activate in one transaction) **and** at the DB layer via
    the ``is_active_flag`` generated column + ``ux_tests__one_active`` unique
    index (DATABASE_SPEC §5.4). The DB index is the backstop for two admins
    publishing concurrently with no active test, which the app-layer check
    alone can't serialize (CODE_REVIEW C8).
    """

    # Tell pytest this isn't a unittest-style test class. Without this,
    # the ``Test*`` python_classes pattern matches whenever a test module
    # imports ``Test`` into its namespace.
    __test__ = False

    __tablename__ = "tests"

    id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        primary_key=True,
        autoincrement=True,
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default="draft",
    )
    duration_seconds: Mapped[int] = mapped_column(
        INTEGER(unsigned=True),
        nullable=False,
        server_default=text("3200"),
    )
    created_by_admin_id: Mapped[int | None] = mapped_column(
        BIGINT(unsigned=True),
        # Spec uses the abbreviated FK name ``fk_tests__created_by``.
        ForeignKey("admins.id", ondelete="SET NULL", name="fk_tests__created_by"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(fsp=6),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP(6)"),
    )
    published_at: Mapped[datetime | None] = mapped_column(UTCDateTime(fsp=6), nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(UTCDateTime(fsp=6), nullable=True)

    # Generated column: 1 only while the row is ``active``, NULL otherwise.
    # MySQL allows many NULLs in a unique index, so the ``ux_tests__one_active``
    # unique index below permits any number of draft/archived rows but at
    # most one active row — the DB-side guarantee of "exactly one active
    # test" (DATABASE_SPEC §5.4 / CODE_REVIEW C8). Read-only; the app never
    # writes it.
    is_active_flag: Mapped[int | None] = mapped_column(
        TINYINT,
        Computed("CASE WHEN status = 'active' THEN 1 END", persisted=False),
        nullable=True,
    )

    __table_args__ = (
        Index("ix_tests__status", "status"),
        Index("ix_tests__published_at", "published_at"),
        Index("ux_tests__one_active", "is_active_flag", unique=True),
        CheckConstraint(
            "status IN ('draft','active','archived')",
            name="ck_tests__status_enum",
        ),
        CheckConstraint(
            "duration_seconds > 0",
            name="ck_tests__duration_positive",
        ),
        CheckConstraint(
            "status = 'draft' OR published_at IS NOT NULL",
            name="ck_tests__active_has_published_at",
        ),
        CheckConstraint(
            "status <> 'archived' OR archived_at IS NOT NULL",
            name="ck_tests__archived_has_archived_at",
        ),
        {
            "mysql_engine": "InnoDB",
            "mysql_charset": "utf8mb4",
            "mysql_collate": "utf8mb4_unicode_ci",
        },
    )
