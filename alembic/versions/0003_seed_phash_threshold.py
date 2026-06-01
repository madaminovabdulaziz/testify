"""seed configurable pHash threshold setting

Revision ID: 0003
Revises: 0002
Create Date: 2026-06-01

Adds the ``phash_hamming_threshold`` row to ``settings`` so the receipt
duplicate-detection sensitivity is editable via /set instead of hardcoded
(PRODUCT_BLUEPRINT §15.4 / CODE_REVIEW M23). The application falls back to 5
when the row is absent, so this is purely so the value shows up in
/settings and is documented in one place.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "INSERT INTO settings (`key`, value, description) VALUES "
        "('phash_hamming_threshold', '5', "
        "'Порог различия pHash для дубликатов чеков (0–64; меньше — строже)')"
    )


def downgrade() -> None:
    op.execute("DELETE FROM settings WHERE `key` = 'phash_hamming_threshold'")
