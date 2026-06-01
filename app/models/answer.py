"""answers table — see DATABASE_SPEC §5.7."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    CHAR,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.mysql import BIGINT
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.models.types import UTCDateTime


class Answer(Base):
    """A single answer the user submitted for one question of one attempt.

    Rows are inserted only when a user actually picks an option (absence
    means unanswered). ``is_correct`` is denormalized at write time so
    the per-question correctness query in DATABASE_SPEC §10.11 stays a
    single-table scan.
    """

    __tablename__ = "answers"

    id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        primary_key=True,
        autoincrement=True,
    )
    attempt_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        ForeignKey("attempts.id", ondelete="CASCADE", name="fk_answers__attempt_id"),
        nullable=False,
    )
    question_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        ForeignKey("questions.id", ondelete="RESTRICT", name="fk_answers__question_id"),
        nullable=False,
    )
    selected_option: Mapped[str] = mapped_column(CHAR(1), nullable=False)
    is_correct: Mapped[bool] = mapped_column(Boolean(), nullable=False)
    answered_at: Mapped[datetime] = mapped_column(
        UTCDateTime(fsp=6),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP(6)"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(fsp=6),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6)"),
    )

    __table_args__ = (
        # Spec uses abbreviated names; pin explicitly.
        UniqueConstraint(
            "attempt_id",
            "question_id",
            name="ux_answers__attempt_question",
        ),
        Index("ix_answers__question_is_correct", "question_id", "is_correct"),
        CheckConstraint(
            "selected_option IN ('A','B','C','D')",
            name="ck_answers__selected_enum",
        ),
        {
            "mysql_engine": "InnoDB",
            "mysql_charset": "utf8mb4",
            "mysql_collate": "utf8mb4_unicode_ci",
        },
    )
