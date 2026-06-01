# Russian Attestation Bot

Telegram bot for a Russian-language teacher in Uzbekistan. Students pay once,
take 50-question mock DTM attestation exams, and join a private group chat
where the teacher reviews answers live. Built on aiogram 3 + MySQL 8 +
SQLAlchemy 2 + Redis 7 + APScheduler, deployed via Docker behind nginx.

Source-of-truth specs live in `docs/`:
[`PRODUCT_BLUEPRINT.md`](docs/PRODUCT_BLUEPRINT.md),
[`ARCHITECTURE_SPEC.md`](docs/ARCHITECTURE_SPEC.md),
[`DATABASE_SPEC.md`](docs/DATABASE_SPEC.md).
Day-to-day ops (deploy, rollback, backup, admin seeding, token rotation)
are in [`docs/RUNBOOK.md`](docs/RUNBOOK.md).

## Quick start

```bash
cp .env.example .env
# edit .env: set BOT_TOKEN, ADMIN_GROUP_ID, etc.
make dev
```

`make dev` starts MySQL + Redis via `docker compose`, runs Alembic migrations,
seeds dev fixtures, and launches the bot in polling mode for easy iteration.
See the `Makefile` (or `make help`) for the full target list — `test`, `lint`,
`typecheck`, `migrate`, `build`, `deploy`.
