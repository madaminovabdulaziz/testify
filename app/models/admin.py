"""admins table — see DATABASE_SPEC §5.2."""

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


class Admin(Base):
    """A bot administrator.

    Lives in its own table (not a flag on ``users``) so the initial admin
    can be seeded by ``telegram_id`` before they ever ``/start`` the bot
    (DATABASE_SPEC §13).
    """

    __tablename__ = "admins"

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
    user_id: Mapped[int | None] = mapped_column(
        BIGINT(unsigned=True),
        ForeignKey("users.id", ondelete="SET NULL", name="fk_admins__user_id"),
        nullable=True,
    )
    role: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default="moderator",
    )
    added_by_admin_id: Mapped[int | None] = mapped_column(
        BIGINT(unsigned=True),
        # Spec uses the abbreviated FK name ``fk_admins__added_by`` rather than
        # the auto-derived ``fk_admins__added_by_admin_id``; pin it explicitly.
        ForeignKey("admins.id", ondelete="SET NULL", name="fk_admins__added_by"),
        nullable=True,
    )
    added_at: Mapped[datetime] = mapped_column(
        UTCDateTime(fsp=6),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP(6)"),
    )

    __table_args__ = (
        Index("ix_admins__user_id", "user_id"),
        CheckConstraint(
            "role IN ('owner','moderator')",
            name="ck_admins__role_enum",
        ),
        {
            "mysql_engine": "InnoDB",
            "mysql_charset": "utf8mb4",
            "mysql_collate": "utf8mb4_unicode_ci",
        },
    )
