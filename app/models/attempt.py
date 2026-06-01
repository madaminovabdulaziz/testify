"""attempts table — see DATABASE_SPEC §5.6."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.mysql import BIGINT, TINYINT
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.models.types import UTCDateTime


class Attempt(Base):
    """One user's session of taking one test.

    ``expires_at`` is denormalized from ``started_at + tests.duration_seconds``
    so the expiry sweep (DATABASE_SPEC §10.15) does not need to JOIN
    ``tests``. ``warning_*_sent_at`` columns make warning dispatch
    idempotent across bot restarts (ARCHITECTURE_SPEC §21.2).
    """

    __tablename__ = "attempts"

    id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        primary_key=True,
        autoincrement=True,
    )
    user_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        ForeignKey("users.id", ondelete="RESTRICT", name="fk_attempts__user_id"),
        nullable=False,
    )
    test_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        ForeignKey("tests.id", ondelete="RESTRICT", name="fk_attempts__test_id"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default="in_progress",
    )
    current_position: Mapped[int] = mapped_column(
        TINYINT(unsigned=True),
        nullable=False,
        server_default=text("1"),
    )
    started_at: Mapped[datetime] = mapped_column(
        UTCDateTime(fsp=6),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP(6)"),
    )
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime(fsp=6), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime(fsp=6), nullable=False)

    score_total_correct: Mapped[int | None] = mapped_column(TINYINT(unsigned=True), nullable=True)
    score_rus_tili_correct: Mapped[int | None] = mapped_column(
        TINYINT(unsigned=True), nullable=True
    )
    score_pedagogik_correct: Mapped[int | None] = mapped_column(
        TINYINT(unsigned=True), nullable=True
    )
    score_kasbiy_correct: Mapped[int | None] = mapped_column(TINYINT(unsigned=True), nullable=True)

    warning_10min_sent_at: Mapped[datetime | None] = mapped_column(
        UTCDateTime(fsp=6), nullable=True
    )
    warning_5min_sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime(fsp=6), nullable=True)
    warning_1min_sent_at: Mapped[datetime | None] = mapped_column(UTCDateTime(fsp=6), nullable=True)

    __table_args__ = (
        # Spec uses abbreviated names that do not match the default
        # ``column_0_N_name`` derivation; pin them explicitly.
        UniqueConstraint("user_id", "test_id", name="ux_attempts__user_test"),
        Index("ix_attempts__status", "status"),
        Index(
            "ix_attempts__test_score",
            "test_id",
            text("score_total_correct DESC"),
        ),
        Index("ix_attempts__expires", "expires_at", "status"),
        CheckConstraint(
            "status IN ('in_progress','submitted','expired')",
            name="ck_attempts__status_enum",
        ),
        CheckConstraint(
            "current_position BETWEEN 1 AND 50",
            name="ck_attempts__current_position_range",
        ),
        CheckConstraint(
            "(status = 'in_progress' AND finished_at IS NULL) OR "
            "(status <> 'in_progress' AND finished_at IS NOT NULL)",
            name="ck_attempts__finished_consistent",
        ),
        CheckConstraint(
            "status = 'in_progress' OR score_total_correct IS NOT NULL",
            name="ck_attempts__score_total_when_finished",
        ),
        CheckConstraint(
            "(score_rus_tili_correct  IS NULL OR score_rus_tili_correct  BETWEEN 0 AND 35) AND "
            "(score_pedagogik_correct IS NULL OR score_pedagogik_correct BETWEEN 0 AND 10) AND "
            "(score_kasbiy_correct    IS NULL OR score_kasbiy_correct    BETWEEN 0 AND 5)  AND "
            "(score_total_correct     IS NULL OR score_total_correct     BETWEEN 0 AND 50)",
            name="ck_attempts__score_ranges",
        ),
        {
            "mysql_engine": "InnoDB",
            "mysql_charset": "utf8mb4",
            "mysql_collate": "utf8mb4_unicode_ci",
        },
    )
