"""Idempotent owner-admin seed (DATABASE_SPEC §13).

After ``alembic upgrade head`` a fresh database has every settings row
populated but **zero admins** — no one can use the receipt-approval
buttons, ``/upload_test``, ``/settings``, or any other gated command.
This script inserts (or updates) the first admin row so the teacher
can take over from inside Telegram.

Usage::

    # via positional arg
    python -m scripts.seed_admin 12345678

    # or via env var
    SEED_ADMIN_TELEGRAM_ID=12345678 python -m scripts.seed_admin

    # role is ``owner`` by default; pass ``--role moderator`` for the
    # 1–3 trusted assistants the teacher hands the bot to.

Connection params are taken from the standard ``DB_HOST`` / ``DB_PORT``
/ ``DB_USER`` / ``DB_PASSWORD`` / ``DB_NAME`` env vars (or whatever
``.env`` defines). The script is intentionally standalone — it does
not require ``BOT_TOKEN`` or any of the Telegram envs, so it can run
from a fresh deploy shell before those are configured.

Re-running with the same ``telegram_id`` is a no-op: the script
verifies the existing row has the right role (and warns if it does
not). Re-running with a different role on an existing row updates it
in place.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models.admin import Admin

Role = Literal["owner", "moderator"]


def _db_url_from_env() -> str:
    """Build an async MySQL URL from the standard env vars (mirrors load_test)."""
    host = os.environ.get("DB_HOST", "127.0.0.1")
    port = int(os.environ.get("DB_PORT", "3306"))
    user = os.environ.get("DB_USER", "bot")
    password = os.environ.get("DB_PASSWORD", "botpass")
    name = os.environ.get("DB_NAME", "attestation")
    return f"mysql+asyncmy://{user}:{password}@{host}:{port}/{name}"


async def _seed(telegram_id: int, role: Role) -> int:
    """Insert or update the admin row. Returns process exit code."""
    engine = create_async_engine(_db_url_from_env())
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    try:
        async with session_factory() as session:
            stmt = select(Admin).where(Admin.telegram_id == telegram_id)
            existing = (await session.execute(stmt)).scalar_one_or_none()

            if existing is None:
                admin = Admin(
                    telegram_id=telegram_id,
                    role=role,
                    added_by_admin_id=None,
                )
                session.add(admin)
                await session.commit()
                print(f"✅ Created admin row: telegram_id={telegram_id} role={role}")
                return 0

            if existing.role == role:
                print(
                    f"= Admin row already exists with the requested role: "
                    f"telegram_id={telegram_id} role={role} (no-op)"
                )
                return 0

            existing.role = role
            await session.commit()
            print(f"↻ Updated admin row: telegram_id={telegram_id} role: {existing.role} → {role}")
            return 0
    finally:
        await engine.dispose()


def _parse_args() -> tuple[int, Role]:
    parser = argparse.ArgumentParser(
        description="Idempotently seed an admin row (DATABASE_SPEC §13).",
    )
    parser.add_argument(
        "telegram_id",
        nargs="?",
        type=int,
        default=None,
        help="Telegram ID to grant admin access (falls back to $SEED_ADMIN_TELEGRAM_ID).",
    )
    parser.add_argument(
        "--role",
        choices=["owner", "moderator"],
        default="owner",
        help="Admin role (default: owner — the teacher).",
    )
    args = parser.parse_args()

    telegram_id = args.telegram_id
    if telegram_id is None:
        env_val = os.environ.get("SEED_ADMIN_TELEGRAM_ID")
        if env_val is None:
            parser.error(
                "telegram_id is required as a positional arg or via $SEED_ADMIN_TELEGRAM_ID."
            )
        try:
            telegram_id = int(env_val)
        except ValueError:
            parser.error(f"$SEED_ADMIN_TELEGRAM_ID must be an integer; got {env_val!r}.")

    role: Role = args.role  # type: ignore[assignment]
    return telegram_id, role


def main() -> int:
    telegram_id, role = _parse_args()
    return asyncio.run(_seed(telegram_id, role))


if __name__ == "__main__":
    sys.exit(main())
