"""broadcasts — durable, resumable admin announcements

Revision ID: 0005
Revises: 0004
Create Date: 2026-06-10

The admin composes one message in Telegram (formatting entities, photo,
video, GIF — all preserved by ``copyMessage``) and the bot fans it out
to every approved student. This table is what makes the fan-out
*durable*: ``last_user_id`` is a cursor over ``users.id`` advanced as
recipients are processed, so a deploy or crash mid-broadcast resumes on
startup instead of silently dropping the tail.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "broadcasts",
        sa.Column(
            "id", mysql.BIGINT(unsigned=True), autoincrement=True, primary_key=True
        ),
        sa.Column("source_chat_id", sa.BigInteger(), nullable=False),
        sa.Column("source_message_id", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="in_progress"),
        sa.Column("created_by_admin_id", mysql.BIGINT(unsigned=True), nullable=True),
        sa.Column("report_chat_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "total_recipients", mysql.INTEGER(unsigned=True), nullable=False, server_default="0"
        ),
        sa.Column("sent_count", mysql.INTEGER(unsigned=True), nullable=False, server_default="0"),
        sa.Column(
            "blocked_count", mysql.INTEGER(unsigned=True), nullable=False, server_default="0"
        ),
        sa.Column("error_count", mysql.INTEGER(unsigned=True), nullable=False, server_default="0"),
        sa.Column(
            "last_user_id", mysql.BIGINT(unsigned=True), nullable=False, server_default="0"
        ),
        sa.Column(
            "created_at",
            mysql.DATETIME(fsp=6),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP(6)"),
        ),
        sa.Column("finished_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.ForeignKeyConstraint(
            ["created_by_admin_id"],
            ["admins.id"],
            ondelete="SET NULL",
            name="fk_broadcasts__created_by",
        ),
        sa.CheckConstraint(
            "status IN ('in_progress','completed')",
            name="ck_broadcasts__status_enum",
        ),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_unicode_ci",
    )
    # The startup-resume scan: WHERE status = 'in_progress'.
    op.create_index("ix_broadcasts__status", "broadcasts", ["status"])


def downgrade() -> None:
    op.drop_index("ix_broadcasts__status", table_name="broadcasts")
    op.drop_table("broadcasts")
