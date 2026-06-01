# Build Prompts — Claude Code

Sequential prompts to build the Russian Attestation Bot end-to-end. Run them **in order**. Each prompt assumes prior prompts are complete and tests are green.

Two principles to enforce in every session:

1. **Make Claude read the specs first.** Each prompt names which docs to load. If Claude starts coding without quoting the relevant spec section back to you, stop it and ask: "Did you read @docs/...?"
2. **Make Claude verify before claiming done.** Every prompt ends with verification commands. If they don't pass, the task isn't done.

---

## 0. Setup (do this manually, before opening Claude Code)

```bash
mkdir -p docs
# Copy the three spec files into docs/
cp /path/to/PRODUCT_BLUEPRINT.md   docs/
cp /path/to/ARCHITECTURE_SPEC.md   docs/
cp /path/to/DATABASE_SPEC.md       docs/
# Copy CLAUDE.md to root
cp /path/to/CLAUDE.md              .
# Init git
git init && git add . && git commit -m "chore: bootstrap docs and CLAUDE.md"
# Open Claude Code
claude
```

Now paste the prompts below, one at a time.

---

## Prompt 1 — Project bootstrap

> **Read first:** @CLAUDE.md and @docs/ARCHITECTURE_SPEC.md §3 (project structure), §1 (tech stack), §5 (config), §14 (deployment), §19 (code conventions).
>
> **Goal:** create the empty project skeleton — folders, tooling configs, and dependency files — with no application logic yet.
>
> **Tasks:**
>
> - Create the full directory tree from ARCHITECTURE_SPEC §3, with empty `__init__.py` files. Do **not** create logic files yet — only directories and inits.
> - Create `pyproject.toml` with the dependencies from ARCHITECTURE_SPEC §1 (production + dev), ruff config (line length 100, target py312), and mypy config (strict for `app.services` and `app.repositories` only).
> - Create `Makefile` with the targets listed in CLAUDE.md.
> - Create `docker-compose.yml` (dev) and `docker-compose.prod.yml` per ARCHITECTURE_SPEC §14.1.
> - Create `docker/Dockerfile` (multi-stage, non-root) per §14.2.
> - Create `docker/nginx.conf` per §14.3 — leave the cert paths and domain as placeholders with comments.
> - Create `.env.example` with every variable from the `Settings` class in ARCHITECTURE_SPEC §5.
> - Create `.gitignore` (Python defaults + `.env`, `*.sqlite`, `htmlcov/`, `.ruff_cache`, `.mypy_cache`).
> - Create `README.md` with a 5-line project description and a "Quick start" section showing `make dev`.
> - Initialize Alembic: `alembic init alembic`, then update `alembic.ini` and `alembic/env.py` to read the DB URL from environment, use the `app.models.base.Base.metadata`, and support async.
>
> **Verification:**
>
> - `tree -L 3 -I '__pycache__|node_modules'` matches ARCHITECTURE_SPEC §3.
> - `pip install -e .` succeeds in a fresh venv (or `uv sync` if using uv).
> - `ruff check .` passes (no files to lint yet, but config must be valid).
> - `docker compose config` validates without errors.
>
> **Do not:** write any application logic. No models, no handlers, nothing in `app/services/`. Just structure and tooling.

---

## Prompt 2 — Database models + initial migration

> **Read first:** @docs/DATABASE_SPEC.md in full (it's the source of truth for this prompt) and @docs/ARCHITECTURE_SPEC.md §3 (where models live).
>
> **Goal:** implement every SQLAlchemy 2.0 model exactly as defined in DATABASE_SPEC §5, plus the initial Alembic migration matching §9 verbatim.
>
> **Tasks:**
>
> - Implement `app/models/base.py` with the declarative base, naming convention for constraints (so Alembic autogenerates predictable names), and a mixin for `created_at` / `updated_at` if useful.
> - Implement one model per file under `app/models/`: `user.py`, `admin.py`, `receipt.py`, `test.py`, `question.py`, `attempt.py`, `answer.py`, `setting.py`.
> - Every model must reflect §5 of DATABASE_SPEC **exactly**: column types (use `sqlalchemy.dialects.mysql.BIGINT(unsigned=True)`, `TINYINT`, `DATETIME(fsp=6)` where specified), nullability, defaults, constraints, foreign keys, indexes.
> - Create `alembic/versions/0001_initial_schema.py` matching DATABASE_SPEC §9. The seed settings INSERT (§8) must be in the migration — full text, not abbreviated.
> - Wire up `alembic/env.py` to use the async engine.
>
> **Verification:**
>
> 1. `docker compose up -d mysql redis` succeeds.
> 2. `make migrate-up` runs cleanly against the fresh MySQL.
> 3. `docker compose exec mysql mysql -uroot -p<pass> -e "SHOW CREATE TABLE users\G"` shows every column, index, FK, and CHECK from DATABASE_SPEC §5.1. Spot-check 2 more tables of your choice.
> 4. `docker compose exec mysql mysql -uroot -p<pass> -e "SELECT \`key\` FROM appdb.settings ORDER BY \`key\`"` returns all keys from §8.
> 5. `make migrate-down` reverses cleanly (downgrade implemented).
>
> **Do not:** add repositories or services yet. Models and migration only.

---

## Prompt 3 — Core infrastructure

> **Read first:** @docs/ARCHITECTURE_SPEC.md §5 (config), §7 (DI / Container), §11 (scheduler), §13 (webhook), §15 (logging).
>
> **Goal:** implement cross-cutting infrastructure that every other layer depends on.
>
> **Tasks:**
>
> - `app/core/config.py` — pydantic-settings `Settings` class with every env var from §5. Use `SecretStr` for tokens/passwords.
> - `app/core/logging.py` — structlog setup; JSON format in prod, console in dev. Helper to bind request context.
> - `app/core/database.py` — async engine + `async_sessionmaker`. Function `create_engine_and_session(settings) -> tuple[AsyncEngine, async_sessionmaker]`.
> - `app/core/redis.py` — Redis client factory.
> - `app/core/scheduler.py` — APScheduler `AsyncIOScheduler` with Redis jobstore per §11.1.
> - `app/core/sentry.py` — Sentry init, skipped if `sentry_dsn` is None.
> - `app/core/i18n.py` — module that loads short UI strings (button labels) used in code. Long copy stays in the `settings` table.
> - `app/utils/datetime.py` — `now_utc()` and tz formatting helpers.
> - `app/utils/text.py` — `html_escape()` wrapper and any safe-formatting helpers.
> - `app/utils/retry.py` — async retry decorator with exponential backoff (used later for Telegram API calls).
> - `app/exceptions.py` — the exception hierarchy from §16.1.
> - `app/main.py` — minimal entrypoint that constructs the `Container` from §7 but does **not** start the bot yet (just print "infra ready" and exit).
>
> **Verification:**
>
> - `python -m app.main` prints "infra ready" with no errors, given a valid `.env`.
> - `make typecheck` passes for `app/core/` and `app/utils/`.
> - `make lint` clean.
>
> **Do not:** import aiogram in `app/core/`. Core is framework-agnostic.

---

## Prompt 4 — Repository layer

> **Read first:** @docs/DATABASE_SPEC.md §10 (every query pattern) and @docs/ARCHITECTURE_SPEC.md §4 (layering rules).
>
> **Goal:** implement every repository with the query methods needed by the services described in ARCHITECTURE_SPEC §8. Each repository wraps SQLAlchemy and exposes domain-shaped methods, not generic CRUD.
>
> **Tasks:**
>
> - `app/repositories/base.py` — `BaseRepository` that takes an `AsyncSession` in `__init__` and provides nothing else (no generic CRUD — be explicit per repo).
> - One repository per table under `app/repositories/`, each with the methods needed by §8 services + §10 query patterns. For example, `UserRepository` needs at least: `get_by_telegram_id`, `get_by_id`, `create`, `update_status`, `set_phone`, `set_name`, `set_reference_code`, `find_by_query` (for `/find`), `list_approved_for_broadcast`.
> - Every method has full type hints and a one-line docstring.
> - Use SQLAlchemy 2.0 style: `select()`, `update()`, `delete()`, awaited via `session.execute()`.
> - Map `selectinload` / `joinedload` only where eager-loading is genuinely needed (don't N+1).
>
> **Verification:**
>
> - For each repository, write one integration test in `tests/integration/repositories/test_<name>.py` using testcontainers MySQL. The test covers the happy path of every public method.
> - `make test` passes.
> - `make typecheck` clean.
>
> **Constraint:** zero business logic in repositories. No `if user.status == "approved": ...` — that belongs in services. Repositories only translate domain queries to SQL.

---

## Prompt 5 — Utility services (Excel parser, image hasher, reference code)

> **Read first:** @docs/PRODUCT_BLUEPRINT.md §12 (Excel template), @docs/ARCHITECTURE_SPEC.md §8.7 (parser), §8.8 (hasher), §8.9 (ref code), §14 (anti-abuse).
>
> **Goal:** build the three pure-utility services that have no DB writes and only one input → output transformation.
>
> **Tasks:**
>
> - `app/services/excel_parser.py` — `ExcelParser.parse(file_bytes) -> ParsedTest | list[ParseError]`. Use openpyxl. Validate every rule in PRODUCT_BLUEPRINT §12. Never raise on bad data — return `ParseError` list. Define `ParsedTest`, `ParsedQuestion`, `ParseError` as frozen dataclasses.
> - `app/services/image_hasher.py` — `ImageHasher.hash(image_bytes) -> int` (returns 64-bit unsigned int) and `is_similar(a: int, b: int, threshold: int = 5) -> bool` (Hamming distance via XOR + `bin(...).count('1')`).
> - `app/services/reference_code.py` — `ReferenceCodeService.generate_unique() -> str`. 6 chars from A–Z and 0–9 minus confusables (`0`, `O`, `1`, `I`, `L`). Uses `UserRepository` to check uniqueness; retry up to 5 times before raising.
> - `scripts/generate_template.py` — produce a sample `template.xlsx` matching PRODUCT_BLUEPRINT §12 with 5 example rows (synthetic, not real attestation questions). Save to `app/static/template.xlsx`.
>
> **Verification:**
>
> - Unit tests for the parser covering: valid file, every validation error type (wrong row count, wrong section, wrong correct_option, out-of-range position, empty cell), at least 10 tests total.
> - Unit tests for the hasher: same image → same hash; slightly modified image → small distance; different image → large distance. Use small test fixtures in `tests/fixtures/`.
> - Unit tests for the reference code: format is correct, no confusable chars, raises on persistent collisions.
> - All tests pass; coverage on these modules ≥90%.

---

## Prompt 6 — Domain services (user, settings, receipt)

> **Read first:** @docs/ARCHITECTURE_SPEC.md §8 (every service contract). @docs/PRODUCT_BLUEPRINT.md §8.1, §8.2, §8.3 (onboarding, payment, receipt review).
>
> **Goal:** implement the services that drive user lifecycle and payment.
>
> **Tasks:**
>
> - `app/services/settings_service.py` — `get(key)`, `set(key, value, admin_id)`, `get_all()`. Cache reads in Redis with 60s TTL; invalidate on write.
> - `app/services/user_service.py` — methods listed in ARCHITECTURE_SPEC §8.1. Enforce state-machine transitions (use the diagram in §10 of PRODUCT_BLUEPRINT as the source).
> - `app/services/receipt_service.py` — `submit()`, `approve()`, `reject()`, `count_pending_for_user()` per §8.2. Submission flow: compute pHash, check for duplicates (against approved set), enforce per-user pending limit (≤3), persist. Approval marks user as `approved`. Rejection requires a reason.
> - All services take their repositories via `__init__`. No global state.
>
> **Verification:**
>
> - Unit tests for each service method, mocking repositories.
> - One integration test per service covering its main flow with real DB.
> - Edge cases covered: invalid state transition (e.g., approving an already-approved receipt → guard returns silently), pending limit exceeded, duplicate hash detection flag.
>
> **Constraint:** services never call `bot.send_message` directly. If a notification is needed, return data that the handler will pass to `NotificationService` later.

---

## Prompt 7 — Domain services (test, attempt, scoring, notification)

> **Read first:** @docs/PRODUCT_BLUEPRINT.md §8.4–§8.6 (publish, take, results) and §10 (state machines). @docs/ARCHITECTURE_SPEC.md §8.3–§8.5, §11 (scheduler), §12 (broadcast).
>
> **Goal:** the test-running services and the broadcast helper.
>
> **Tasks:**
>
> - `app/services/test_service.py` — `create_draft_from_excel()` (calls parser, persists test + 50 questions in one transaction), `publish(draft_id, notify)` (archives previous active in same tx as activating new; triggers broadcast if `notify`), `cancel_draft()`, `get_active_test()`.
> - `app/services/scoring_service.py` — `compute_scores(answers, questions) -> Scores`. Pure function. Returns total + per-section correct counts.
> - `app/services/attempt_service.py` — `start(user, test)` (creates attempt, schedules timer jobs), `get_state(attempt_id, user_id)` (returns DTO for rendering), `submit_answer(...)` (UPSERT), `update_current_position(...)`, `finish(attempt_id, reason)` (idempotent — guard on status, compute score, cancel jobs).
> - `app/services/notification_service.py` — `broadcast_new_test(test, recipient_ids)` with semaphore + token-bucket rate limit per §12; `send_to_admin_group(...)`; `send_time_warning(attempt_id, minutes)`; handles `TelegramForbiddenError` → marks user `bot_blocked=True`.
> - Implement the token-bucket rate limiter in `app/utils/rate_limiter.py` (~30 lines, no new deps).
>
> **Verification:**
>
> - Integration tests:
>   - publishing a new test archives the previous one in a single transaction (verify both rows after).
>   - starting an attempt creates the row with correct `expires_at = started_at + 3200s`.
>   - calling `finish()` twice doesn't double-update.
>   - broadcast simulation with mocked Bot: 50 recipients, 10 of them raise `Forbidden`, the rest succeed; result counter is correct and `bot_blocked` is set on the 10.
>
> **Think hard** about the publish transaction — getting the active-archive-publish atomicity wrong here is a class of bug that's hard to detect later.

---

## Prompt 8 — Bot factory, dispatcher, middleware, filters

> **Read first:** @docs/ARCHITECTURE_SPEC.md §6 (aiogram setup), §13 (webhook). PRODUCT_BLUEPRINT §10 (state machines).
>
> **Goal:** the wiring layer that gives us a runnable bot in polling mode (dev) and webhook mode (prod), with no handlers yet.
>
> **Tasks:**
>
> - `app/bot/bot.py` — `build_bot(settings)` and `build_dispatcher(redis, container)` per §6.1. Register all middlewares in the order given in §6.3.
> - `app/bot/middlewares/` — implement all four middlewares in §6.3. `DbSessionMiddleware` must commit on success, rollback on exception, close always. `UserLoaderMiddleware` creates `new` users on first interaction and short-circuits banned users with "Доступ ограничён".
> - `app/bot/filters/` — `AdminOnly`, `ApprovedOnly`, `AdminGroupOnly`, `PhotoOnly`. Each is a `Filter` subclass.
> - `app/bot/states/` — all `StatesGroup` classes from ARCHITECTURE_SPEC §6.6.
> - `app/bot/callbacks/` — all `CallbackData` factories from §6.5.
> - `app/bot/webhook.py` — aiohttp webhook handler per §13, with secret-token verification and a `/healthz` endpoint that pings DB + Redis.
> - Update `app/main.py` to start in polling mode if `env == "dev"` and webhook mode otherwise.
>
> **Verification:**
>
> - `python -m app.main` starts the bot in dev mode, connects to Telegram, prints "bot started".
> - Send `/start` to the bot from a personal account → no handlers registered yet, so nothing should happen, but the bot must not crash. The middleware chain must execute: a `users` row should appear in DB with status `new`.
> - `curl http://localhost:8080/healthz` returns 200 in webhook mode.

---

## Prompt 9 — Handlers: onboarding + payment + admin receipts

> **Read first:** @docs/PRODUCT_BLUEPRINT.md §8.1, §8.2, §8.3 and §11 (Russian copy). @docs/ARCHITECTURE_SPEC.md §6.4 (handler contract), §9 (views).
>
> **Goal:** the first end-to-end user flow: user can `/start`, share contact, enter name, see payment instructions, send a receipt, and an admin can approve or reject it.
>
> **Tasks:**
>
> - `app/bot/views/payment_screen.py` — `render_payment_instructions(user, settings) -> RenderedMessage`. Pulls templates from settings table, substitutes placeholders.
> - `app/bot/views/admin_receipt.py` — `render_admin_receipt_notification(user, receipt, warnings) -> RenderedMessage`.
> - `app/bot/handlers/common.py` — `/start` (entry point; routes by user.status), `/help`, fallback.
> - `app/bot/handlers/onboarding.py` — phone capture, name capture, transition to payment.
> - `app/bot/handlers/payment.py` — "Я оплатил" button, receipt photo reception, forward to admin group.
> - `app/bot/handlers/admin/receipts.py` — approve/reject buttons, reject-reason flow, edit original message after decision, DM user with result.
>
> **Verification:**
>
> - Manual: from your personal Telegram, walk the full flow `/start → contact → name → payment screen → send a sample image → see it appear in admin group → approve → receive success DM with invite link`.
> - Reject path: send a new image after approval should fail with "уже студент"; before approval, rejecting must DM the user the reason and let them resubmit.
> - Duplicate detection: send the same image from a second test account; admin notification must include the "⚠️ Похожий чек" warning.
> - Pending limit: send 4 different images in a row from one account; the 4th must be refused.
> - Integration tests for the receipt service path; handler tests with `MockedBot`.

---

## Prompt 10 — Handlers: admin test upload + publish

> **Read first:** @docs/PRODUCT_BLUEPRINT.md §8.4, §12. @docs/ARCHITECTURE_SPEC.md §8.3 (TestService).
>
> **Goal:** admin can upload an Excel test and publish it (with or without notification).
>
> **Tasks:**
>
> - `app/bot/handlers/admin/tests.py` — `/upload_test` initiates the flow, accepts a `.xlsx` document, calls `ExcelParser`, shows a preview message with inline buttons (`Опубликовать с уведомлением`, `Опубликовать тихо`, `Отменить`).
> - `/template` returns the generated template file from `app/static/template.xlsx`.
> - On parse failure, show per-line errors as in PRODUCT_BLUEPRINT §8.4.
> - On publish, archive the prior active test in the same transaction. If notify-publish, trigger the broadcast (await in background — don't block the callback).
>
> **Verification:**
>
> - Upload a valid template → preview shows correct section counts → publish → confirmation message.
> - Upload an invalid file (wrong section count, position gap, empty cell) → see specific errors.
> - Upload then publish-notify → all approved test accounts (create 2-3) receive the broadcast.
> - DB state after publish: exactly one test with `status='active'`, the previous one with `status='archived'` and `archived_at` set.

---

## Prompt 11 — Handlers: test taking (the hard one)

> **Read first, then think hard:** @docs/PRODUCT_BLUEPRINT.md §8.5 (full test-taking flow). @docs/ARCHITECTURE_SPEC.md §9.1 (keyboard layout), §10.2 (handler chain).
>
> **Goal:** a student can take a 50-question test with timer, navigation grid, and a clean result screen. This is the most complex flow — implement carefully.
>
> **Tasks:**
>
> - `app/bot/views/test_screen.py` — pure function `render_test_screen(state: AttemptState) -> RenderedMessage` per ARCHITECTURE_SPEC §9.1. Keyboard layout exactly as in §9.1.
> - `app/bot/views/result_screen.py` — render score, per-section breakdown, link to group chat.
> - `app/bot/handlers/test_taking.py`:
>   - Pre-test entry → confirmation → `attempt_service.start()` → render first question.
>   - `TestAnswerCD`: idempotent ack, save answer, advance to next *unanswered* question (or stay on current if all later are answered), re-render.
>   - `TestNavCD`: jump to target question, update `current_position` in DB, re-render.
>   - `TestFinishCD(confirmed=False)`: show confirmation with answered/unanswered counts.
>   - `TestFinishCD(confirmed=True)`: call `attempt_service.finish(reason="user")`, render result.
>   - Resume: if user re-enters mid-attempt, render current state.
>   - Already-attempted: show prior result, don't allow second attempt.
>
> **Verification:**
>
> - Full test simulation with one approved user:
>   - Answer all 50, finish, see correct score.
>   - Skip 5, finish, see correct counts.
>   - Restart the bot process mid-attempt, return to the chat, tap a button — state must be intact.
>   - Re-enter after finishing → see "Вы уже проходили этот тест" with score, no new attempt.
>   - Confirm finish dialog correctly reports answered/unanswered counts.
> - Concurrent test: 5 fake users taking the test simultaneously (via test-bot helpers) without state collision.
>
> **Constraint:** the test screen must be rendered as **one message that gets edited**, not a new message each time. Use `message.edit_text(...)`. Handle `TelegramBadRequest` ("message is not modified") gracefully — it's not an error.

---

## Prompt 12 — Background jobs

> **Read first:** @docs/ARCHITECTURE_SPEC.md §11 (jobs), §12 (broadcast). @docs/DATABASE_SPEC.md §10.13–§10.15 (reconciliation queries).
>
> **Goal:** scheduled jobs for timer warnings, auto-submit at expiry, pending-receipt reminders, and startup reconciliation.
>
> **Tasks:**
>
> - `app/jobs/attempt_timer.py` — four job functions: `warn_10min`, `warn_5min`, `warn_1min`, `expire`. Each is idempotent (checks `warning_X_sent_at` and `status` before acting).
> - `app/jobs/pending_receipt_reminder.py` — runs hourly, finds receipts pending > 24h, notifies admin group. Uses a small marker (could be Redis key with TTL) to avoid duplicate reminders.
> - `app/jobs/registry.py` — `register_recurring_jobs(scheduler)` and `schedule_attempt_jobs(scheduler, attempt)`. Called from `AttemptService.start()`.
> - `app/jobs/startup_reconciliation.py` — on bot startup, scan `attempts WHERE status='in_progress'`, re-register their jobs (with `replace_existing=True`). Also runs a one-shot safety sweep for already-expired attempts that were missed.
> - Wire into `app/main.py`: scheduler starts before the bot, reconciliation runs once before accepting updates.
>
> **Verification:**
>
> - Manual: start an attempt with a temporarily shortened `test_duration_seconds` (e.g., 60s) via env. Walk away. Verify warning messages arrive at the expected times and the attempt auto-submits with `status='expired'`.
> - Kill the bot mid-attempt, restart, verify the expiry job fires correctly.
> - Submit a receipt and don't review it; fast-forward DB clock (`UPDATE payment_receipts SET created_at = NOW() - INTERVAL 25 HOUR ...`) and trigger the reminder job manually; admin group must receive the reminder.

---

## Prompt 13 — Admin operations + settings management

> **Read first:** @docs/PRODUCT_BLUEPRINT.md §8.8, §8.9. @docs/DATABASE_SPEC.md §10.4, §10.10, §10.16.
>
> **Goal:** the admin's command surface for day-to-day operations.
>
> **Tasks:**
>
> - `app/bot/handlers/admin/operations.py`: `/stats`, `/find <q>`, `/ban <id>`, `/unban <id>`, `/leaderboard <test_id>`, `/attempt <id>`. All admin-only.
> - `app/bot/handlers/admin/settings.py`: `/settings`, `/set <key> <value>`, `/preview <key>`. Validate keys; warn on unknown keys.
> - All admin output uses HTML formatting with proper escaping. Tables rendered with monospace where useful.
>
> **Verification:**
>
> - Each command produces correct output against a seeded DB.
> - Non-admins running any of these commands get no response.
> - `/set group_invite_link https://t.me/+abcdef` updates settings, and a fresh approval DM uses the new link.
> - `/preview welcome_message` renders exactly as a real user would see it on `/start`.

---

## Prompt 14 — End-to-end tests + load smoke test

> **Read first:** @docs/ARCHITECTURE_SPEC.md §17 (testing strategy). @docs/PRODUCT_BLUEPRINT.md §17 (acceptance criteria).
>
> **Goal:** cover the 5 critical flows from §17.2 of ARCHITECTURE_SPEC with end-to-end tests, plus a synthetic load test.
>
> **Tasks:**
>
> - E2E tests in `tests/integration/flows/`:
>   - `test_full_onboarding_to_first_score.py`
>   - `test_receipt_rejection_and_retry.py`
>   - `test_auto_submit_on_expiry.py` (uses shortened duration)
>   - `test_resume_after_restart.py`
>   - `test_duplicate_receipt_detection.py`
> - Each uses real MySQL via testcontainers and a mocked Bot. Each runs in <30s.
> - `scripts/load_test.py` — simulate 50 concurrent users taking the same test. Asserts no errors, all attempts finish, scores are consistent.
>
> **Verification:**
>
> - `make test` passes including the new E2E tests.
> - `python scripts/load_test.py` reports zero errors and all 50 attempts in `submitted` state.

---

## Prompt 15 — Deploy

> **Read first:** @docs/ARCHITECTURE_SPEC.md §14, §15. @docs/DATABASE_SPEC.md §12 (backup), §13 (admin seed).
>
> **Goal:** production-ready deploy.
>
> **Tasks:**
>
> - Finalize `docker-compose.prod.yml`.
> - Finalize `docker/nginx.conf` with real domain and cert paths (templated via env).
> - `scripts/seed_admin.py` — idempotent owner-admin insertion by Telegram ID.
> - `scripts/backup.sh` — `mysqldump` + gzip + timestamp; cron-friendly.
> - `Makefile` — `make deploy` runbook per §14.4.
> - `docs/RUNBOOK.md` — operator's guide: how to deploy, rollback, restore from backup, add a new admin, rotate the bot token.
>
> **Verification:**
>
> - Full deploy onto a staging server.
> - `/healthz` returns 200.
> - Webhook receives a `/start` from a real Telegram client.
> - Manual restoration drill: stop the bot, restore the latest backup to a fresh DB, restart, verify the previously-approved user is still approved.
>
> **Final acceptance:** every box in PRODUCT_BLUEPRINT §17 and ARCHITECTURE_SPEC §22 and DATABASE_SPEC §16 is checked.

---

## Tips for working through these

- **One prompt per session.** Mixing creates context bleed.
- **Don't accept "looks good".** Always run the verification commands and paste the output back to Claude Code if anything fails.
- **If Claude proposes deviating from a spec**, ask "where does the spec say that?" If it doesn't, stop and revise the spec first or push back on the proposal.
- **Commit after each prompt.** `git commit -m "feat(<scope>): <prompt N summary>"`.
- **Use `ultrathink` for prompts 7 and 11** — they're the highest-stakes correctness work. Append "ultrathink before writing code" to the prompt to get extended reasoning.
- **If a prompt is too big for one session**, split it yourself rather than letting Claude truncate. Half a feature is better than a finished feature with hidden gaps.
