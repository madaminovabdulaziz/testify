"""one active test DB guard

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-01

Adds the DB-side enforcement of "exactly one active test" that
DATABASE_SPEC §5.4 describes as the bolt-on for when the application layer
alone is not trusted. Two admins publishing different drafts concurrently
while no test is active can otherwise both activate (CODE_REVIEW C8); the
``is_active_flag`` generated column + unique index makes the second
activation fail with an IntegrityError that ``TestService.publish`` turns
into a friendly "try again" message.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# Revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Generated column: 1 only while the row is ``active``, NULL otherwise.
    # VIRTUAL (not stored) — it is recomputed from ``status`` on read/index.
    op.execute(
        "ALTER TABLE tests "
        "ADD COLUMN is_active_flag TINYINT "
        "GENERATED ALWAYS AS (CASE WHEN status = 'active' THEN 1 END) VIRTUAL"
    )
    # MySQL allows many NULLs in a unique index, so this permits any number
    # of draft/archived rows but at most one row with is_active_flag = 1.
    op.create_index("ux_tests__one_active", "tests", ["is_active_flag"], unique=True)


def downgrade() -> None:
    op.drop_index("ux_tests__one_active", table_name="tests")
    op.drop_column("tests", "is_active_flag")
