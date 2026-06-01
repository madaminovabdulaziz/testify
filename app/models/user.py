"""users table — see DATABASE_SPEC §5.1."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    CHAR,
    BigInteger,
    Boolean,
    CheckConstraint,
    Index,
    String,
    text,
)
from sqlalchemy.dialects.mysql import BIGINT
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.models.types import UTCDateTime


class User(Base):
    """A Telegram user known to the bot.

    The primary lookup key is ``telegram_id`` — every incoming update hits
    this table via that column. ``status`` drives the onboarding /
    payment / approval state machine (see PRODUCT_BLUEPRINT §10.1).
    """

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        primary_key=True,
        autoincrement=True,
    )
    telegram_id: Mapped[int] = mapped_column(
        BigInteger(),
        nullable=False,
        unique=True,
    )
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    full_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    reference_code: Mapped[str | None] = mapped_column(CHAR(6), nullable=True, unique=True)
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        server_default="new",
    )
    bot_blocked: Mapped[bool] = mapped_column(
        Boolean(),
        nullable=False,
        server_default=text("0"),
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(fsp=6),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP(6)"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(fsp=6),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6)"),
    )
    approved_at: Mapped[datetime | None] = mapped_column(UTCDateTime(fsp=6), nullable=True)

    __table_args__ = (
        Index("ix_users__phone", "phone"),
        Index("ix_users__username", "username"),
        Index("ix_users__status", "status"),
        CheckConstraint(
            "status IN ('new','onboarding_phone','onboarding_name','pending_payment',"
            "'pending_approval','rejected','approved','banned')",
            name="ck_users__status_enum",
        ),
        {
            "mysql_engine": "InnoDB",
            "mysql_charset": "utf8mb4",
            "mysql_collate": "utf8mb4_unicode_ci",
        },
    )
