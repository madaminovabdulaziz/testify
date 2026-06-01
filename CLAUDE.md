# Russian Attestation Bot

Telegram bot for a Russian-language teacher in Uzbekistan. Students pay once, take 50-question mock attestation exams, and join a private group chat. Built on aiogram 3 + MySQL + SQLAlchemy 2 + Redis.

## Source of truth — read these before touching anything

- **@docs/PRODUCT_BLUEPRINT.md** — what to build, user flows, business rules, Russian copy
- **@docs/ARCHITECTURE_SPEC.md** — module structure, layering, FSM, deployment
- **@docs/DATABASE_SPEC.md** — exact schema, indexes, FK policy, seed data, Alembic migration

If a spec answers your question, follow it. If it doesn't, **ask before deciding** — do not invent product, architecture, or schema rules.

## Tech stack

Python 3.12 · aiogram 3.13+ · SQLAlchemy 2.0 async · asyncmy · MySQL 8.4 · Redis 7 · Alembic · APScheduler · openpyxl · imagehash · pydantic-settings · structlog · sentry-sdk · pytest · ruff · mypy · Docker.

No FastAPI, no Celery, no pandas, no aiogram-dialog. If a new dependency feels needed, justify it first.

## Layering (strict)

```
handlers → services → repositories → models
```

Inward only. Handlers know aiogram + services. Services know repositories + domain rules. Repositories know SQLAlchemy. Models know nothing. **Business logic in handlers is a bug.**

## Commands

- `make dev` — start MySQL + Redis, run migrations, seed fixtures, run bot in polling mode
- `make test` — full pytest suite (unit + integration)
- `make lint` — `ruff check .` + `ruff format --check .`
- `make typecheck` — `mypy app/services app/repositories`
- `make migrate name="…"` — create a new Alembic revision
- `make migrate-up` — `alembic upgrade head`
- `make migrate-down` — `alembic downgrade -1`
- `make build` — build Docker image
- `make deploy` — production deploy runbook (see ARCHITECTURE_SPEC §14.4)

## Code style

- Type hints **required** on every function in `app/services/` and `app/repositories/`. Enforced by mypy.
- Async everywhere. No blocking IO in handlers or services.
- snake_case files/functions/vars, PascalCase classes, SCREAMING_SNAKE constants.
- One class per file for models, services, repositories.
- No `from x import *`. No `print()` outside `scripts/`. Use the logger.
- Russian user-facing copy lives in the `settings` table — never hardcode it in handlers or views. Defaults are seeded by the initial migration.
- All timestamps UTC. Use `app/utils/datetime.py:now_utc()`, never `datetime.now()`.
- HTML-escape every user-provided string before sending to Telegram.

## Critical "do not"s

- ❌ Do not put business logic in handlers — extract to a service.
- ❌ Do not access models/sessions from handlers — go through a repository.
- ❌ Do not call `bot.send_message` from services — services use `NotificationService`.
- ❌ Do not modify the schema without writing an Alembic migration.
- ❌ Do not run `alembic upgrade` from inside the bot process — migrations are a one-shot container step.
- ❌ Do not reproduce copyrighted song lyrics, articles, or test questions from real DTM exams in code or fixtures. Generate synthetic test data.
- ❌ Do not hardcode the admin Telegram ID, card number, bot token, or group ID. Env vars or settings table only.

## Testing

- Unit tests next to the layer they cover: `tests/unit/services/`, `tests/unit/views/`, etc.
- Integration tests use real MySQL via `testcontainers`. Do not mock the ORM.
- Bot handlers tested with aiogram's `MockedBot`.
- Before declaring any task done: `make lint && make typecheck && make test` all pass.
- New code without tests is incomplete code.

## Git etiquette

- Branch names: `feat/<scope>`, `fix/<scope>`, `chore/<scope>`. Lowercase, hyphen-separated.
- Commit format: `<type>(<scope>): <imperative subject>` — e.g., `feat(payment): add receipt deduplication via pHash`.
- One logical change per commit. No "fix typo + add feature" mixed commits.
- Never commit: `.env`, `__pycache__/`, `*.pyc`, `*.sqlite`, `htmlcov/`, secrets of any kind.
- Always run `make lint && make test` before committing.

## When uncertain

1. Re-read the relevant spec section.
2. If the spec is silent, **ask the user** — do not invent rules.
3. Prefer doing less. Flag scope concerns rather than expanding.
4. If a spec contradicts itself, surface the contradiction; do not pick one silently.

## Build order

See `docs/PROMPTS.md` if present, or follow ARCHITECTURE_SPEC §3 bottom-up: bootstrap → models → core → repositories → services → bot setup → handlers (by domain) → background jobs → tests → deploy.
