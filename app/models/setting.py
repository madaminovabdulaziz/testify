"""settings table — see DATABASE_SPEC §5.8."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    ForeignKey,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.mysql import BIGINT
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.models.types import UTCDateTime


class Setting(Base):
    """A single key/value runtime setting (e.g. ``welcome_message``).

    Edited by admins via ``/set`` so user-facing copy can be tweaked without
    a redeploy. ``key`` is a MySQL reserved word — SQLAlchemy auto-quotes
    it in emitted DDL.
    """

    __tablename__ = "settings"

    # ``key`` is the natural primary key; row count is small (~20) so no
    # surrogate id is needed.
    key: Mapped[str] = mapped_column("key", String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text(), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500), nullable=True)
    updated_by_admin_id: Mapped[int | None] = mapped_column(
        BIGINT(unsigned=True),
        # Spec uses abbreviated FK name ``fk_settings__updated_by``.
        ForeignKey("admins.id", ondelete="SET NULL", name="fk_settings__updated_by"),
        nullable=True,
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(fsp=6),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6)"),
    )

    __table_args__ = (
        {
            "mysql_engine": "InnoDB",
            "mysql_charset": "utf8mb4",
            "mysql_collate": "utf8mb4_unicode_ci",
        },
    )
