"""Dev seed: ensure the admin row exists if ``SEED_ADMIN_TELEGRAM_ID`` is set.

Called by ``make dev`` as a friendly idempotent step. If the env var
isn't set, this is a no-op that prints a hint — the bot will still run,
but you won't be able to use admin commands until you seed yourself
via ``make seed-admin TELEGRAM_ID=<your-id>``.
"""

from __future__ import annotations

import asyncio
import os
import sys

from scripts.seed_admin import _seed


async def _main() -> int:
    raw = os.environ.get("SEED_ADMIN_TELEGRAM_ID")
    if not raw:
        print(
            "ℹ scripts.seed_dev: SEED_ADMIN_TELEGRAM_ID not set — "
            "skipping admin seed.\n"
            "  After the bot starts, run:\n"
            "    make seed-admin TELEGRAM_ID=<your-telegram-id>\n"
            "  (Find your ID by DM'ing @userinfobot on Telegram.)"
        )
        return 0
    try:
        telegram_id = int(raw)
    except ValueError:
        print(f"✗ SEED_ADMIN_TELEGRAM_ID must be an integer, got {raw!r}")
        return 1
    return await _seed(telegram_id, role="owner")


def main() -> int:
    return asyncio.run(_main())


if __name__ == "__main__":
    sys.exit(main())
