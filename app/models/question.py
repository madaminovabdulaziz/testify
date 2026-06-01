"""questions table — see DATABASE_SPEC §5.5."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    CHAR,
    Boolean,
    CheckConstraint,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.mysql import BIGINT, TINYINT
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.models.types import UTCDateTime


class Question(Base):
    """One question of a test.

    The (section, position) ranges are mirrored as a CHECK constraint —
    defense in depth so that even an out-of-band INSERT cannot violate
    the 35/10/5 split.
    """

    __tablename__ = "questions"

    id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        primary_key=True,
        autoincrement=True,
    )
    test_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        ForeignKey("tests.id", ondelete="CASCADE", name="fk_questions__test_id"),
        nullable=False,
    )
    section: Mapped[str] = mapped_column(String(16), nullable=False)
    position: Mapped[int] = mapped_column(TINYINT(unsigned=True), nullable=False)
    question_text: Mapped[str] = mapped_column(Text(), nullable=False)
    option_a: Mapped[str] = mapped_column(String(500), nullable=False)
    option_b: Mapped[str] = mapped_column(String(500), nullable=False)
    option_c: Mapped[str] = mapped_column(String(500), nullable=False)
    option_d: Mapped[str] = mapped_column(String(500), nullable=False)
    correct_option: Mapped[str] = mapped_column(CHAR(1), nullable=False)
    # Illustration (table / chart / diagram). We store Telegram's identifiers,
    # not bytes — ``image_file_id`` is what we re-send to students; the bot
    # never downloads or stores the image (migration 0004 / ARCHITECTURE_SPEC
    # §21.5). ``has_image`` is the durable "supposed to carry a picture" flag so
    # a mid-authoring network drop is recoverable from the DB, not FSM state.
    has_image: Mapped[bool] = mapped_column(
        Boolean(),
        nullable=False,
        server_default=text("0"),
    )
    image_file_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    image_file_unique_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(fsp=6),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP(6)"),
    )

    __table_args__ = (
        # Spec uses the abbreviated name ``ux_questions__test_position`` rather
        # than the auto-derived ``ux_questions__test_id_position``.
        UniqueConstraint("test_id", "position", name="ux_questions__test_position"),
        CheckConstraint(
            "section IN ('rus_tili','pedagogik','kasbiy')",
            name="ck_questions__section_enum",
        ),
        CheckConstraint(
            "correct_option IN ('A','B','C','D')",
            name="ck_questions__correct_enum",
        ),
        CheckConstraint(
            "position BETWEEN 1 AND 50",
            name="ck_questions__position_range",
        ),
        CheckConstraint(
            "(section='rus_tili'  AND position BETWEEN 1  AND 35) OR "
            "(section='pedagogik' AND position BETWEEN 36 AND 45) OR "
            "(section='kasbiy'    AND position BETWEEN 46 AND 50)",
            name="ck_questions__section_position_consistent",
        ),
        # A text question (has_image=0) must never carry an image id —
        # defense in depth against an out-of-band write (migration 0004).
        CheckConstraint(
            "has_image = 1 OR image_file_id IS NULL",
            name="ck_questions__image_consistent",
        ),
        {
            "mysql_engine": "InnoDB",
            "mysql_charset": "utf8mb4",
            "mysql_collate": "utf8mb4_unicode_ci",
        },
    )
