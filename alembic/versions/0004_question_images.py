"""question illustrations (tables / charts / diagrams)

Revision ID: 0004
Revises: 0003
Create Date: 2026-06-01

Adds optional per-question imagery so the faithful DTM reproduction can carry
the tables / charts / figures some questions depend on (Goal G2). We persist
only Telegram's identifiers — never bytes, never disk — mirroring the receipt
decision in ARCHITECTURE_SPEC §21.5:

* ``image_file_id`` — what the bot re-sends to students (``sendPhoto`` /
  ``editMessageMedia`` by id; no re-upload, no storage).
* ``image_file_unique_id`` — Telegram's stable content id; persisted now for a
  future re-download / dedup path even though v1 doesn't use it.
* ``has_image`` — the durable "this question is *supposed* to carry a picture"
  flag. Set at draft time from the Excel ``has_image`` column; the authoring
  flow then collects the photo in-bot. A network drop mid-collection leaves the
  intent recoverable (PRODUCT_BLUEPRINT principle 3 — survive the network)
  instead of stranded in FSM state.

``ck_questions__image_consistent`` keeps a text question (``has_image = 0``)
from ever carrying an image id — defense in depth, in the same spirit as
``ck_questions__section_position_consistent``.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "questions",
        sa.Column("has_image", sa.Boolean(), nullable=False, server_default=sa.text("0")),
    )
    op.add_column(
        "questions",
        sa.Column("image_file_id", sa.String(256), nullable=True),
    )
    op.add_column(
        "questions",
        sa.Column("image_file_unique_id", sa.String(64), nullable=True),
    )
    op.create_check_constraint(
        "ck_questions__image_consistent",
        "questions",
        "has_image = 1 OR image_file_id IS NULL",
    )


def downgrade() -> None:
    op.drop_constraint("ck_questions__image_consistent", "questions", type_="check")
    op.drop_column("questions", "image_file_unique_id")
    op.drop_column("questions", "image_file_id")
    op.drop_column("questions", "has_image")
