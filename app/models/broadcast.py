"""broadcasts table — durable state for admin announcements to all students.

A broadcast copies one admin-composed Telegram message (text with
formatting entities, photo, video or GIF — preserved verbatim by
``copyMessage``) to every approved student. The row exists so delivery
is *resumable*: ``last_user_id`` is a cursor over ``users.id``, advanced
as recipients are processed, and a bot restart picks unfinished
broadcasts back up instead of silently dropping the tail.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, String, text
from sqlalchemy.dialects.mysql import BIGINT, INTEGER
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.models.types import UTCDateTime


class Broadcast(Base):
    """One admin announcement fan-out, with resumable progress."""

    __tablename__ = "broadcasts"

    id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True),
        primary_key=True,
        autoincrement=True,
    )
    # The admin-composed source message that copyMessage replays to every
    # student. It lives in the admin's private chat with the bot and must
    # not be deleted until the broadcast completes.
    source_chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source_message_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        server_default="in_progress",
    )
    created_by_admin_id: Mapped[int | None] = mapped_column(
        BIGINT(unsigned=True),
        ForeignKey("admins.id", ondelete="SET NULL", name="fk_broadcasts__created_by"),
        nullable=True,
    )
    # Where to post the completion report (the admin's chat).
    report_chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    total_recipients: Mapped[int] = mapped_column(
        INTEGER(unsigned=True), nullable=False, server_default=text("0")
    )
    sent_count: Mapped[int] = mapped_column(
        INTEGER(unsigned=True), nullable=False, server_default=text("0")
    )
    blocked_count: Mapped[int] = mapped_column(
        INTEGER(unsigned=True), nullable=False, server_default=text("0")
    )
    error_count: Mapped[int] = mapped_column(
        INTEGER(unsigned=True), nullable=False, server_default=text("0")
    )
    # Resume cursor: every approved user with id <= last_user_id has been
    # processed (sent, blocked or errored). 0 = nothing processed yet.
    last_user_id: Mapped[int] = mapped_column(
        BIGINT(unsigned=True), nullable=False, server_default=text("0")
    )

    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime(fsp=6),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP(6)"),
    )
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime(fsp=6), nullable=True)

    __table_args__ = (
        CheckConstraint(
            "status IN ('in_progress','completed')",
            name="ck_broadcasts__status_enum",
        ),
    )
