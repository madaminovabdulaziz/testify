# Technical Architecture Specification

**Document type:** Engineering spec (Phase 2 of 3)
**Version:** 1.0
**Date:** 2026-05-21
**Status:** Draft
**Author role:** Senior Software Engineer
**Predecessor:** `PRODUCT_BLUEPRINT.md`
**Successor:** `DATABASE_SPEC.md` (Phase 3)
**Scope:** How we build this — modules, layers, runtime, deployment. **Not** schema (Phase 3).

---

## 1. Tech Stack

Every dependency below is chosen deliberately. If you swap one, swap it with intent.

| Layer | Choice | Version | Why |
|---|---|---|---|
| Language | Python | 3.12+ | Modern asyncio, structural pattern matching, type-hint improvements |
| Bot framework | aiogram | 3.13+ | Native async, router-based, FSM built-in, Telegram Bot API parity |
| Web server | aiohttp | 3.10+ | Comes with aiogram; webhook handler |
| DB driver | asyncmy | 0.2.10+ | Async MySQL driver; native, not a wrapper around pymysql |
| ORM | SQLAlchemy | 2.0+ | Async support, mature, the only sensible choice |
| Migrations | Alembic | 1.13+ | SQLAlchemy's official migration tool |
| FSM storage | redis (`redis-py`) | 5.0+ | Persistent FSM across restarts |
| Scheduler | APScheduler | 3.10+ | Cron + interval jobs; we use it for test timer expiry |
| Excel parsing | openpyxl | 3.1+ | Read .xlsx; no need for pandas overhead |
| Image hashing | imagehash | 4.3+ | pHash for receipt deduplication |
| HTTP client (Telegram file downloads) | aiohttp | (already) | Pull receipt photos for hashing |
| Config | pydantic-settings | 2.5+ | Type-safe env var loading |
| Logging | structlog | 24.4+ | Structured JSON logs |
| Error tracking | sentry-sdk | 2.14+ | Exception monitoring |
| Metrics (optional v1) | prometheus-client | 0.21+ | Prometheus-compatible metrics |
| Testing | pytest, pytest-asyncio, pytest-mock | latest | Standard async test stack |
| Container DB for tests | testcontainers | 4.8+ | Spin up real MySQL for integration tests |
| Linting | ruff | 0.6+ | Replaces black + flake8 + isort |
| Type checking | mypy | 1.11+ | Strict mode for `app/services` and `app/repositories` |
| Container | Docker + docker-compose | latest stable | Standard ops |
| Reverse proxy | nginx | 1.27+ | TLS termination, webhook forwarding |

**Deliberate non-choices:**
- ❌ FastAPI — we don't need a REST API; the bot is the API surface.
- ❌ Celery — APScheduler handles our timer-driven jobs; Celery is overkill for ~hundreds of users.
- ❌ Pandas — openpyxl is enough for a 50-row file.
- ❌ Pydantic for domain models — use SQLAlchemy models + dataclasses. Pydantic only at config and IO boundaries.
- ❌ aiogram-dialog — extra complexity; native FSM + carefully-built keyboards are sufficient.

---

## 2. High-Level Runtime Topology

```
                          ┌─────────────────────┐
                          │   Telegram Cloud    │
                          └──────────┬──────────┘
                                     │ HTTPS webhook
                                     ▼
                          ┌─────────────────────┐
                          │       nginx         │  TLS, rate-limit, secret-path
                          └──────────┬──────────┘
                                     │ HTTP (loopback)
                                     ▼
              ┌──────────────────────────────────────────────┐
              │              Bot Process (aiohttp)            │
              │                                                │
              │   ┌────────────┐  ┌──────────────────────┐    │
              │   │  Webhook   │  │     Health Probe     │    │
              │   │  Handler   │  │     /healthz         │    │
              │   └─────┬──────┘  └──────────────────────┘    │
              │         │                                       │
              │   ┌─────▼───────────────────────────────┐      │
              │   │   aiogram Dispatcher + Routers       │      │
              │   │   • Middleware chain                 │      │
              │   │   • Handlers (thin)                  │      │
              │   └─────┬───────────────────────────────┘      │
              │         │                                       │
              │   ┌─────▼─────────┐    ┌─────────────────┐     │
              │   │  Services     │◀──▶│  Repositories   │     │
              │   │  (business)   │    │  (data access)  │     │
              │   └───────────────┘    └────────┬────────┘     │
              │                                  │              │
              │   ┌────────────────────────┐    │              │
              │   │ APScheduler (in-proc)  │    │              │
              │   │ Timer warnings, expiry │    │              │
              │   └─────────────┬──────────┘    │              │
              └─────────────────┼────────────────┼──────────────┘
                                │                │
                ┌───────────────▼──┐     ┌──────▼─────────┐
                │      Redis        │     │     MySQL      │
                │ FSM + job store   │     │ Business data  │
                └───────────────────┘     └────────────────┘
                                                  │
                                          ┌───────▼────────┐
                                          │    Sentry      │ (exceptions)
                                          └────────────────┘
```

**Process model:** single bot process, single replica. Sufficient for our scale (low thousands of users, hundreds of concurrent attempts max). Horizontal scaling is a future concern; we explicitly do not design for it in v1 (the single bot process is also the cleanest way to manage aiogram's webhook/FSM/scheduler interplay).

**Why webhook, not long-polling:** lower latency, no wasted polls, easier to integrate with nginx and proper monitoring. We pay the cost of needing a public HTTPS endpoint, which we have to have anyway.

---

## 3. Project Structure

```
attestation-bot/
├── app/
│   ├── __init__.py
│   ├── main.py                    # Entrypoint: build app, attach webhook, start
│   │
│   ├── core/                       # Cross-cutting infrastructure
│   │   ├── __init__.py
│   │   ├── config.py               # Pydantic Settings
│   │   ├── logging.py              # structlog setup
│   │   ├── database.py             # SQLAlchemy engine + sessionmaker
│   │   ├── redis.py                # Redis client factory
│   │   ├── scheduler.py            # APScheduler setup
│   │   ├── sentry.py               # Sentry init
│   │   └── i18n.py                 # Russian strings registry (loads from DB settings)
│   │
│   ├── bot/                        # Telegram-specific code
│   │   ├── __init__.py
│   │   ├── bot.py                  # Bot + Dispatcher factory
│   │   ├── webhook.py              # aiohttp webhook routes + secret check
│   │   │
│   │   ├── handlers/               # Thin handlers, one router per domain
│   │   │   ├── __init__.py
│   │   │   ├── common.py           # /help, /start (entrypoint), fallbacks
│   │   │   ├── onboarding.py
│   │   │   ├── payment.py
│   │   │   ├── test_taking.py
│   │   │   ├── results.py
│   │   │   └── admin/
│   │   │       ├── __init__.py
│   │   │       ├── receipts.py     # Approve/reject buttons in admin group
│   │   │       ├── tests.py        # /upload_test, publish, preview
│   │   │       ├── settings.py     # /set, /settings, /preview
│   │   │       └── operations.py   # /stats, /find, /ban, /leaderboard, /attempt
│   │   │
│   │   ├── middlewares/
│   │   │   ├── __init__.py
│   │   │   ├── db_session.py
│   │   │   ├── user_loader.py      # Loads or creates user, injects into handler
│   │   │   ├── throttle.py         # Per-user rate limit
│   │   │   ├── logging.py          # Request-id + structured log context
│   │   │   └── error_handler.py    # Catches all, sends safe message + Sentry
│   │   │
│   │   ├── filters/
│   │   │   ├── __init__.py
│   │   │   ├── admin_only.py
│   │   │   ├── approved_only.py
│   │   │   ├── admin_group_only.py
│   │   │   └── photo_only.py
│   │   │
│   │   ├── keyboards/
│   │   │   ├── __init__.py
│   │   │   ├── onboarding.py       # request_contact button
│   │   │   ├── payment.py          # "Я оплатил" inline
│   │   │   ├── test_taking.py      # The big one: options + nav + grid
│   │   │   ├── admin.py            # Approve/reject, publish/cancel
│   │   │   └── common.py
│   │   │
│   │   ├── states/                 # aiogram StatesGroup definitions
│   │   │   ├── __init__.py
│   │   │   ├── onboarding.py
│   │   │   ├── payment.py
│   │   │   ├── test_taking.py
│   │   │   └── admin.py
│   │   │
│   │   ├── callbacks/              # CallbackData factories
│   │   │   ├── __init__.py
│   │   │   ├── test.py             # TestNav, TestAnswer, TestFinish
│   │   │   ├── receipt.py          # ReceiptDecision
│   │   │   └── publish.py          # PublishAction
│   │   │
│   │   └── views/                  # Pure functions: state -> rendered message
│   │       ├── __init__.py
│   │       ├── test_screen.py      # Render test screen from attempt state
│   │       ├── result_screen.py
│   │       ├── payment_screen.py
│   │       └── admin_receipt.py
│   │
│   ├── services/                   # Business logic — pure where possible
│   │   ├── __init__.py
│   │   ├── user_service.py
│   │   ├── payment_service.py
│   │   ├── receipt_service.py
│   │   ├── test_service.py
│   │   ├── attempt_service.py
│   │   ├── scoring_service.py
│   │   ├── notification_service.py # Broadcast helper
│   │   ├── excel_parser.py         # .xlsx -> validated Question DTOs
│   │   ├── image_hasher.py         # Receipt pHash
│   │   ├── reference_code.py       # Generate unique 6-char codes
│   │   └── settings_service.py     # Get/set settings, with cache
│   │
│   ├── repositories/               # Data access — async, session-injected
│   │   ├── __init__.py
│   │   ├── base.py
│   │   ├── user_repository.py
│   │   ├── receipt_repository.py
│   │   ├── test_repository.py
│   │   ├── question_repository.py
│   │   ├── attempt_repository.py
│   │   ├── answer_repository.py
│   │   ├── settings_repository.py
│   │   └── admin_repository.py
│   │
│   ├── models/                     # SQLAlchemy declarative models (schema in Phase 3)
│   │   ├── __init__.py
│   │   ├── base.py
│   │   ├── user.py
│   │   ├── receipt.py
│   │   ├── test.py
│   │   ├── question.py
│   │   ├── attempt.py
│   │   ├── answer.py
│   │   ├── setting.py
│   │   └── admin.py
│   │
│   ├── jobs/                       # APScheduler background jobs
│   │   ├── __init__.py
│   │   ├── registry.py             # Schedule jobs on startup; expose API
│   │   ├── attempt_timer.py        # Warning messages + auto-submit
│   │   └── pending_receipt_reminder.py
│   │
│   ├── utils/
│   │   ├── __init__.py
│   │   ├── text.py                 # html_escape, format helpers
│   │   ├── callback_data.py
│   │   ├── datetime.py             # tz-aware now(), formatting
│   │   └── retry.py                # Decorator with backoff
│   │
│   └── exceptions.py               # Custom exception hierarchy
│
├── alembic/
│   ├── versions/
│   ├── env.py
│   └── script.py.mako
│
├── tests/
│   ├── conftest.py
│   ├── unit/
│   │   ├── services/
│   │   ├── views/
│   │   └── utils/
│   ├── integration/
│   │   ├── repositories/
│   │   └── flows/                  # End-to-end-ish, real DB, mocked Bot
│   └── fixtures/
│       └── sample_test.xlsx
│
├── docker/
│   ├── Dockerfile
│   └── nginx.conf
│
├── docs/
│   ├── PRODUCT_BLUEPRINT.md
│   ├── ARCHITECTURE_SPEC.md       # This document
│   └── DATABASE_SPEC.md           # Phase 3 output
│
├── scripts/
│   ├── generate_template.py        # Create the user-facing .xlsx template
│   └── seed_dev.py                 # Insert dev fixtures (admin user, sample test)
│
├── .env.example
├── alembic.ini
├── docker-compose.yml
├── docker-compose.prod.yml
├── pyproject.toml                  # Deps + ruff + mypy config
├── README.md
└── Makefile                        # make dev, make test, make migrate, make lint
```

---

## 4. Architectural Principles

These resolve "which layer does this belong in?" disputes:

### 4.1 Layering

```
Handler (bot/handlers/*)
    │  knows about: aiogram types, view renderers, services, FSMContext
    │  does NOT know about: SQLAlchemy, repositories, Redis directly
    ▼
Service (services/*)
    │  knows about: repositories, other services, domain rules, DTOs
    │  does NOT know about: aiogram, Telegram types, HTTP, Redis
    ▼
Repository (repositories/*)
    │  knows about: SQLAlchemy models, sessions, queries
    │  does NOT know about: services, business rules, anything above
    ▼
Model (models/*)
    │  knows about: SQLAlchemy, type hints
    │  does NOT know about: anything else in the app
```

**Why this strict:** because the day we want to add a web admin panel, a CLI, or even just a unit test that doesn't spin up Telegram — services and repositories work unchanged. If business logic leaks into handlers, refactoring becomes a nightmare.

### 4.2 Dependency direction

- **Inward only.** Handlers depend on services; services depend on repositories; never the reverse.
- **No circular imports.** Enforced by structure; if `payment_service.py` needs `test_service.py`, extract the shared piece.
- **Constructor injection** for services: each service takes the repositories and other services it needs as `__init__` args. No global singletons (except `settings`, `logger`, the bot instance, and APScheduler).

### 4.3 Handler thinness

A handler does only:
1. Extract input from the aiogram update (callback data, message text, file_id)
2. Call exactly one service method
3. Render the response via a view function
4. Update FSM state if needed

If a handler has business logic (calculating a score, deciding if a receipt is a duplicate, checking time limits) — that logic lives in a service. The handler asks; the service decides.

### 4.4 Idempotency

Every callback handler must be **idempotent**: tapping the same button twice (network retry, user double-tap) must not double-write. Patterns:

- **Database-level guard:** check status before mutating ("if receipt.status != 'pending': return")
- **Telegram dedup:** aiogram automatically deduplicates `callback_query.id` within its in-memory queue, but only within ~30s and only if the worker hasn't acked it.
- **Explicit ack:** call `callback_query.answer()` early in every handler so Telegram knows we got it.

### 4.5 Time handling

- All timestamps in UTC, stored as `DATETIME` in MySQL.
- Display in the user's local time (Asia/Tashkent, UTC+5) — handled in view functions.
- Centralized `app/utils/datetime.py` provides `now_utc()` — never use `datetime.now()` directly.

### 4.6 No business logic in models

Models are dumb data containers. `User.is_approved` is fine as a column-derived property; `User.send_welcome()` is not — that's a handler/service concern.

---

## 5. Configuration

All config from environment variables, loaded via pydantic-settings.

**`app/core/config.py`:**

```python
class Settings(BaseSettings):
    # Telegram
    bot_token: SecretStr
    webhook_url: HttpUrl                 # https://bot.example.com/webhook
    webhook_path: str = "/webhook"
    webhook_secret: SecretStr            # random string, set in Telegram + verified per request
    admin_group_id: int                  # negative int, the supergroup ID

    # Database
    db_host: str
    db_port: int = 3306
    db_user: str
    db_password: SecretStr
    db_name: str
    db_pool_size: int = 10
    db_pool_max_overflow: int = 5

    # Redis
    redis_url: RedisDsn                  # redis://localhost:6379/0

    # App
    env: Literal["dev", "staging", "prod"] = "dev"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_format: Literal["json", "console"] = "json"
    sentry_dsn: SecretStr | None = None

    # Bot behavior
    test_duration_seconds: int = 3200
    receipt_max_pending_per_user: int = 3
    receipt_reminder_after_hours: int = 24
    broadcast_concurrency: int = 20      # asyncio semaphore size
    broadcast_messages_per_second: int = 25  # below Telegram's 30/s limit

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")
```

**Loaded once at startup**, passed into the DI graph. Mutating runtime settings (welcome message, payment amount) is **not** in this object — those live in the `settings` table and are accessed via `SettingsService`.

**`.env.example`** is checked in; `.env` is gitignored.

---

## 6. Bot Setup (Aiogram)

### 6.1 Bot & Dispatcher factory

**`app/bot/bot.py`:**

```python
async def build_bot(settings: Settings) -> Bot:
    return Bot(
        token=settings.bot_token.get_secret_value(),
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

async def build_dispatcher(redis: Redis, container: Container) -> Dispatcher:
    storage = RedisStorage(redis, key_builder=DefaultKeyBuilder(with_destiny=True))
    dp = Dispatcher(storage=storage)

    # Register middlewares (order matters)
    dp.update.middleware(LoggingMiddleware())
    dp.update.middleware(DbSessionMiddleware(container.session_factory))
    dp.update.middleware(UserLoaderMiddleware())
    dp.update.middleware(ThrottleMiddleware(redis=redis))

    # Register routers
    dp.include_router(onboarding.router)
    dp.include_router(payment.router)
    dp.include_router(test_taking.router)
    dp.include_router(results.router)
    dp.include_router(admin_receipts.router)
    dp.include_router(admin_tests.router)
    dp.include_router(admin_settings.router)
    dp.include_router(admin_operations.router)
    dp.include_router(common.router)  # MUST be last — catches /start, /help, fallbacks

    # Errors
    dp.errors.register(global_error_handler)

    return dp
```

### 6.2 Why router-based, not handler decorators on Dispatcher

- Routers are testable in isolation.
- Filters compose: `router.message.filter(F.chat.type == "private")` applies to all handlers in that router.
- Imports are explicit and traceable.

### 6.3 Middleware chain

| Order | Middleware | Purpose |
|---|---|---|
| 1 | `LoggingMiddleware` | Generate request_id, bind user_id + telegram_id into structlog context |
| 2 | `DbSessionMiddleware` | Create one `AsyncSession` per update, commit on success, rollback on exception, close always. Inject into handler kwargs as `session`. |
| 3 | `UserLoaderMiddleware` | Look up the user by `telegram_id`. Create a `new` user if not exists. Inject as `user`. Skips for updates without a sender (e.g. some scheduled callbacks). |
| 4 | `ThrottleMiddleware` | Per-user rate limit: max 10 actions/sec. Implemented via Redis INCR with TTL. Drops the update + sends silent ACK on overflow. |

**ErrorHandler is registered separately**, not as a middleware. It catches `dispatcher.errors`.

### 6.4 Handler invocation contract

Every handler signature:

```python
async def handler(
    event: Message | CallbackQuery,     # depending on filter
    state: FSMContext,
    session: AsyncSession,               # injected by DbSessionMiddleware
    user: User,                          # injected by UserLoaderMiddleware
    container: Container,                # service container (see §7)
) -> None:
    ...
```

`container` is the DI root; handlers pull services from it. (Alternative: middleware injects each service individually. We chose container-as-arg because there are 10+ services and method signatures get unwieldy.)

### 6.5 Callback data conventions

Use aiogram's `CallbackData` factory. Each domain has its own prefix:

```python
class TestAnswerCD(CallbackData, prefix="ta"):
    attempt_id: int
    question_pos: int       # 1..50
    option: str             # 'A'|'B'|'C'|'D'

class TestNavCD(CallbackData, prefix="tn"):
    attempt_id: int
    target_pos: int         # 1..50  (or 0 = "next unanswered")

class TestFinishCD(CallbackData, prefix="tf"):
    attempt_id: int
    confirmed: bool

class ReceiptDecisionCD(CallbackData, prefix="rd"):
    receipt_id: int
    decision: Literal["approve", "reject"]

class PublishCD(CallbackData, prefix="pub"):
    draft_id: int
    action: Literal["publish_notify", "publish_silent", "cancel"]
```

**Constraint:** Telegram caps callback_data at 64 bytes. With short prefixes and int IDs, we stay well under.

### 6.6 FSM states

Each multi-step flow has a `StatesGroup`.

**`app/bot/states/onboarding.py`:**
```python
class OnboardingState(StatesGroup):
    waiting_for_phone = State()
    waiting_for_name = State()
```

**`app/bot/states/payment.py`:**
```python
class PaymentState(StatesGroup):
    waiting_for_receipt = State()
```

**`app/bot/states/test_taking.py`:**
```python
class TestState(StatesGroup):
    in_progress = State()
    confirming_finish = State()
```

**`app/bot/states/admin.py`:**
```python
class AdminTestUploadState(StatesGroup):
    waiting_for_file = State()
    confirming_publish = State()

class AdminRejectReasonState(StatesGroup):
    waiting_for_reason = State()
```

**FSM data conventions (per state):**

State data is JSON-serializable. Keep it minimal — store IDs, not full objects. Heavy data (test questions, attempt details) is fetched from DB per request.

| State | Data keys |
|---|---|
| `OnboardingState.waiting_for_name` | `phone: str` |
| `PaymentState.waiting_for_receipt` | — (state alone is enough) |
| `TestState.in_progress` | `attempt_id: int`, `current_position: int` |
| `AdminTestUploadState.confirming_publish` | `draft_test_id: int` |
| `AdminRejectReasonState.waiting_for_reason` | `receipt_id: int` |

**Important:** FSM state is the *user's intent*, not authoritative business state. The DB is the source of truth. If the FSM says "in_test" but the DB says the attempt is expired, the DB wins — we clean up FSM and show the result.

---

## 7. Dependency Injection

Container pattern, manual. No DI framework — overkill for our size.

**`app/main.py` (excerpt):**

```python
async def build_container(settings: Settings) -> Container:
    engine = create_async_engine(settings.db_url, pool_size=settings.db_pool_size, ...)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    redis = await create_redis(settings.redis_url)

    container = Container(
        settings=settings,
        session_factory=session_factory,
        redis=redis,
        bot=await build_bot(settings),
    )
    container.scheduler = build_scheduler(redis, container)
    return container
```

**`Container`** is a frozen dataclass with the long-lived dependencies (settings, engine, session_factory, redis, bot, scheduler). Services are instantiated **per request** (lightweight, take a session as init arg):

```python
@dataclass(frozen=True)
class Container:
    settings: Settings
    session_factory: async_sessionmaker
    redis: Redis
    bot: Bot
    scheduler: AsyncIOScheduler  # set after creation

    def services(self, session: AsyncSession) -> Services:
        return Services(
            user=UserService(UserRepository(session)),
            receipt=ReceiptService(
                ReceiptRepository(session),
                UserRepository(session),
                ImageHasher(),
            ),
            test=TestService(TestRepository(session), QuestionRepository(session)),
            attempt=AttemptService(
                AttemptRepository(session),
                AnswerRepository(session),
                QuestionRepository(session),
                self.scheduler,
                self.bot,
            ),
            settings=SettingsService(SettingsRepository(session), self.redis),
            notification=NotificationService(self.bot, self.settings),
            excel_parser=ExcelParser(),
            ref_code=ReferenceCodeService(UserRepository(session)),
        )
```

In handlers:

```python
async def on_approve(callback: CallbackQuery, data: ReceiptDecisionCD, session, user, container):
    services = container.services(session)
    await services.receipt.approve(receipt_id=data.receipt_id, admin_user=user)
    await callback.answer("✅")
```

**Why instantiate services per request:** because they hold a session, which is per-request. No state leakage between users. Cheap.

---

## 8. Service Layer — Contracts

Each service exposes a small, sharp API. Below are the most important method signatures (return types omitted for brevity; all are async).

### 8.1 `UserService`
- `get_or_create(telegram_id, username) -> User`
- `set_phone(user_id, phone)`
- `set_name(user_id, name)`
- `mark_approved(user_id)`
- `ban(user_id)`, `unban(user_id)`
- `find(query: str) -> User | None` — for `/find` (matches phone, username, reference_code)

### 8.2 `ReceiptService`
- `submit(user, photo_file_id, photo_bytes) -> Receipt`
  - Computes pHash; checks duplicates; enforces pending-limit; persists; returns receipt with `is_duplicate_warning` flag for the caller to show in admin notification.
- `approve(receipt_id, admin_user) -> User` — returns the user who was approved.
- `reject(receipt_id, admin_user, reason: str)`
- `count_pending_for_user(user_id) -> int`

### 8.3 `TestService`
- `create_draft_from_excel(file_bytes, uploaded_by_admin_id) -> DraftTest`
  - Parses, validates, creates `test` row with status=`draft` + all `question` rows.
- `publish(draft_id, notify: bool) -> Test`
  - Archives previous active test, activates this one, kicks off broadcast if `notify`.
- `cancel_draft(draft_id)`
- `get_active_test() -> Test | None`

### 8.4 `AttemptService`
- `start(user, test) -> Attempt`
  - Verifies user is `approved` and hasn't already attempted this test. Schedules the warning + expiry jobs.
- `get_current_state(attempt_id) -> AttemptState`
  - DTO with: current question, all questions, current answers, time remaining.
- `submit_answer(attempt_id, question_pos, option)`
- `finish(attempt_id, *, reason: Literal["user", "expired"]) -> AttemptResult`
  - Idempotent. Calculates score, persists, cancels any pending scheduler jobs for this attempt.

### 8.5 `NotificationService`
- `broadcast_new_test(test)` — sends to all `approved` users with throttling.
- `send_to_admin_group(text, photo_file_id=None, reply_markup=None)`
- `send_time_warning(attempt_id, minutes_remaining)`
- `notify_pending_receipt(receipt_id, age_hours)`

### 8.6 `SettingsService`
- `get(key) -> str` — reads from Redis cache, falls through to DB on miss.
- `set(key, value)` — writes DB, invalidates cache.
- `get_all() -> dict[str, str]`

Cached for 60 seconds in Redis to avoid hitting DB on every render. Cache invalidation on writes.

### 8.7 `ExcelParser`
- `parse(file_bytes) -> ParsedTest | ParseErrors`
  - Returns either a `ParsedTest` DTO (50 question DTOs grouped by section) or a list of `ParseError` (line + message). Never raises; errors are data.

### 8.8 `ImageHasher`
- `hash(image_bytes) -> str` — returns pHash as hex string.
- `is_similar(hash_a, hash_b, threshold=5) -> bool` — Hamming distance.

### 8.9 `ReferenceCodeService`
- `generate_unique() -> str` — 6-char A–Z0–9 minus confusables (no `0,O,1,I,L`). Retries on collision (up to 5 times, then raises).

---

## 9. Views (Rendering)

Views are **pure functions** that take state and return `(text, reply_markup)`. They never touch the DB or call services. This makes them trivially testable.

**`app/bot/views/test_screen.py`:**

```python
def render_test_screen(state: AttemptState) -> RenderedMessage:
    text = format_test_text(
        time_remaining=state.time_remaining,
        current_pos=state.current_position,
        total=50,
        section_label=state.current_section_label_ru,
        question=state.current_question,
        options=state.current_options,
    )
    keyboard = build_test_keyboard(
        attempt_id=state.attempt_id,
        current_pos=state.current_position,
        answered_positions=state.answered_positions,
    )
    return RenderedMessage(text=text, reply_markup=keyboard)
```

**`RenderedMessage`** is a small dataclass: `text: str, reply_markup: InlineKeyboardMarkup | None, parse_mode: str = "HTML"`.

**Handler** then does:
```python
rendered = render_test_screen(state)
await callback.message.edit_text(text=rendered.text, reply_markup=rendered.reply_markup)
```

### 9.1 Test screen keyboard layout

```python
def build_test_keyboard(attempt_id, current_pos, answered_positions):
    kb = InlineKeyboardBuilder()

    # Options: A/B/C/D, one per row for readability
    for opt in ["A", "B", "C", "D"]:
        kb.button(text=opt, callback_data=TestAnswerCD(attempt_id=attempt_id, question_pos=current_pos, option=opt))
    kb.adjust(1)  # 1 per row

    # Navigation row
    kb.row(
        InlineKeyboardButton(text="⬅️ Назад", callback_data=TestNavCD(attempt_id=attempt_id, target_pos=max(1, current_pos - 1)).pack()),
        InlineKeyboardButton(text="Вперёд ➡️", callback_data=TestNavCD(attempt_id=attempt_id, target_pos=min(50, current_pos + 1)).pack()),
    )

    # Finish row
    kb.row(InlineKeyboardButton(text="🏁 Завершить тест", callback_data=TestFinishCD(attempt_id=attempt_id, confirmed=False).pack()))

    # Question grid: 10 buttons per row, sections separated by a header row of a single non-clickable label
    # Telegram doesn't support non-interactive buttons, so section labels go in the message TEXT, not the keyboard.
    for batch in chunked(range(1, 51), 10):
        row = []
        for pos in batch:
            label = format_grid_button_label(pos, current_pos, answered_positions)
            row.append(InlineKeyboardButton(text=label, callback_data=TestNavCD(attempt_id=attempt_id, target_pos=pos).pack()))
        kb.row(*row)

    return kb.as_markup()

def format_grid_button_label(pos, current, answered):
    if pos == current:
        return f"🔴{pos}"
    if pos in answered:
        return f"{pos}✅"
    return str(pos)
```

Total button count: 4 + 2 + 1 + 50 = **57 buttons**. Safe; Telegram's limit is 100.

### 9.2 Section headers in message text

```
⏱ Осталось: 42:15  ·  Вопрос 5/50  ·  Раздел: Русский язык

Какой из глаголов относится к первому спряжению?

📚 Русский язык (1–35)
👨‍🏫 Педагогическое мастерство (36–45)
📋 Профессиональный стандарт (46–50)
```

The user understands the section ranges from the static legend below the question; the grid buttons are unlabeled (just numbers).

---

## 10. State Machines — Detailed Flow

### 10.1 Onboarding handler chain

```
/start (any state)
  └─▶ if user.status == 'new':
        - render welcome
        - state.set(None)  # cleared; "Начать" button starts flow

[CallbackQuery: cd="start_onboarding"]
  └─▶ state.set(OnboardingState.waiting_for_phone)
      - render contact-request keyboard

[Message: contact]  filter: state == waiting_for_phone
  └─▶ user_service.set_phone(user.id, contact.phone)
      - state.set(OnboardingState.waiting_for_name)
      - ask for name

[Message: text]  filter: state == waiting_for_name
  └─▶ try user_service.set_name(user.id, message.text)
      on success:
        ref_code = ref_code_service.generate_unique()
        user_service.attach_reference_code(user.id, ref_code)
        user.status = pending_payment
        state.clear()
        render payment screen
      on validation error:
        reply "введите корректное имя"
        stay in state
```

### 10.2 Test-taking handler chain

```
[CallbackQuery: cd="start_test"]
  └─▶ pre-checks:
        - user.status == approved? else "оплатите подготовку"
        - active_test exists? else "нет доступных тестов"
        - user has prior attempt on this test? -> show prior result; STOP
        - confirm screen "вы готовы начать?"

[CallbackQuery: cd="confirm_start"]
  └─▶ attempt = attempt_service.start(user, active_test)
      - schedule jobs (see §11)
      - state.set(TestState.in_progress, data={attempt_id})
      - render test screen at position 1

[CallbackQuery: TestAnswerCD]
  └─▶ verify attempt belongs to this user and is in_progress
      attempt_service.submit_answer(attempt_id, question_pos, option)
      state = attempt_service.get_current_state(attempt_id)
      if state.has_unanswered:
          advance current_position to next unanswered
      else:
          stay on current
      edit_message with render_test_screen(state)

[CallbackQuery: TestNavCD]
  └─▶ verify attempt + ownership
      update current_position in DB (yes, persisted, not just FSM)
      state = ...
      edit_message

[CallbackQuery: TestFinishCD, confirmed=False]
  └─▶ render confirmation dialog
      state.set(TestState.confirming_finish)

[CallbackQuery: TestFinishCD, confirmed=True]
  └─▶ result = attempt_service.finish(attempt_id, reason="user")
      state.clear()
      render result screen

[Scheduled job: attempt_expire(attempt_id)]
  └─▶ result = attempt_service.finish(attempt_id, reason="expired")
      bot.send_message(user_id, render_result_screen(result))
      # NOTE: this is the only path where we send a fresh message rather than editing.
```

### 10.3 Why we persist `current_position` to DB (not just FSM)

If the FSM is lost (Redis flush, edge case), we want the user to resume on a sensible question rather than at position 1. DB writes on every nav are cheap (a single UPDATE on `attempts.current_position`).

---

## 11. Background Jobs

APScheduler with `AsyncIOScheduler`, jobstore in Redis (so jobs survive bot restart).

### 11.1 Scheduler setup

```python
def build_scheduler(redis: Redis, container: Container) -> AsyncIOScheduler:
    jobstores = {"default": RedisJobStore(host=redis_host, port=redis_port, db=1)}
    return AsyncIOScheduler(jobstores=jobstores, timezone="UTC")
```

### 11.2 Jobs

| Job | Trigger | Effect |
|---|---|---|
| `attempt_warn_10min` | `attempt.started_at + 2600s` | Send "осталось 10 минут" message to user |
| `attempt_warn_5min` | `attempt.started_at + 2900s` | Send "осталось 5 минут" |
| `attempt_warn_1min` | `attempt.started_at + 3140s` | Send "осталась 1 минута" |
| `attempt_expire` | `attempt.started_at + 3200s` | `attempt_service.finish(reason="expired")` |
| `pending_receipt_reminder` | 24h after receipt submitted, then again at 72h, 7d | Post to admin group: "чек ждёт проверки" |

All jobs are **named with the entity ID** for idempotency and cancellability:

```python
job_id = f"attempt_expire:{attempt_id}"
scheduler.add_job(
    attempt_expire_job,
    trigger=DateTrigger(run_date=expire_at),
    id=job_id,
    replace_existing=True,
    kwargs={"attempt_id": attempt_id},
)
```

On manual finish, the service cancels its own future jobs:

```python
def cancel_attempt_jobs(attempt_id):
    for suffix in ["warn_10min", "warn_5min", "warn_1min", "expire"]:
        try:
            scheduler.remove_job(f"attempt_{suffix}:{attempt_id}")
        except JobLookupError:
            pass
```

### 11.3 Job re-scheduling on bot restart

On startup, scan DB for `in_progress` attempts whose timer hasn't fully elapsed and re-register their jobs. This is a safety net in case jobs are lost (e.g., Redis flushed):

```python
async def reconcile_attempt_jobs(session, scheduler):
    active = await attempt_repository.list_in_progress(session)
    for attempt in active:
        schedule_jobs_for_attempt(scheduler, attempt)
```

Idempotent due to `replace_existing=True`.

---

## 12. Broadcast Strategy

For "publish with notify," potentially 1000+ messages to send. We must respect Telegram limits (30 msg/sec global) without serializing one-at-a-time (would be slow and block the event loop).

```python
async def broadcast_new_test(test, all_user_ids):
    sem = asyncio.Semaphore(settings.broadcast_concurrency)  # 20
    rate_limiter = AsyncRateLimiter(rate=settings.broadcast_messages_per_second)  # 25/s

    async def send_one(uid):
        async with sem:
            await rate_limiter.acquire()
            try:
                await bot.send_message(uid, text, reply_markup=...)
                return ("ok", uid)
            except TelegramForbiddenError:
                return ("blocked", uid)
            except TelegramRetryAfter as e:
                await asyncio.sleep(e.retry_after)
                return await send_one(uid)
            except Exception as e:
                logger.exception("broadcast_error", uid=uid)
                return ("error", uid)

    results = await asyncio.gather(*[send_one(uid) for uid in all_user_ids])
    summary = Counter(r[0] for r in results)
    return summary  # for admin report: "sent: 847, blocked: 12, errors: 1"
```

The `AsyncRateLimiter` is a token-bucket implementation (~30 lines, no external dep needed).

---

## 13. Webhook & HTTP

**`app/bot/webhook.py`:**

```python
async def webhook_handler(request: web.Request) -> web.Response:
    secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
    if secret != settings.webhook_secret.get_secret_value():
        return web.Response(status=403)

    update = Update.model_validate(await request.json(), context={"bot": bot})
    await dp.feed_update(bot=bot, update=update)
    return web.Response()

async def healthz(request) -> web.Response:
    # Liveness + readiness checks: DB ping, Redis ping
    try:
        async with session_factory() as s:
            await s.execute(text("SELECT 1"))
        await redis.ping()
        return web.json_response({"status": "ok"})
    except Exception:
        return web.json_response({"status": "fail"}, status=503)

def make_app() -> web.Application:
    app = web.Application()
    app.router.add_post(settings.webhook_path, webhook_handler)
    app.router.add_get("/healthz", healthz)
    return app
```

**Webhook registration on startup:**

```python
async def on_startup(bot: Bot, settings: Settings):
    await bot.set_webhook(
        url=str(settings.webhook_url),
        secret_token=settings.webhook_secret.get_secret_value(),
        allowed_updates=dp.resolve_used_update_types(),
        drop_pending_updates=False,  # in prod; True only for clean redeploys
    )

async def on_shutdown(bot: Bot):
    await bot.delete_webhook(drop_pending_updates=False)
```

---

## 14. Deployment

### 14.1 Container topology (docker-compose.prod.yml)

```yaml
services:
  bot:
    build: .
    restart: unless-stopped
    env_file: .env
    depends_on: [mysql, redis]
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8080/healthz"]
      interval: 30s
      timeout: 5s
      retries: 3

  mysql:
    image: mysql:8.4
    restart: unless-stopped
    environment:
      MYSQL_ROOT_PASSWORD: ${DB_ROOT_PASSWORD}
      MYSQL_DATABASE: ${DB_NAME}
      MYSQL_USER: ${DB_USER}
      MYSQL_PASSWORD: ${DB_PASSWORD}
    volumes:
      - mysql_data:/var/lib/mysql
    command: --default-authentication-plugin=caching_sha2_password --character-set-server=utf8mb4 --collation-server=utf8mb4_unicode_ci

  redis:
    image: redis:7.4-alpine
    restart: unless-stopped
    command: redis-server --appendonly yes --save 60 1000
    volumes:
      - redis_data:/data

  nginx:
    image: nginx:1.27-alpine
    restart: unless-stopped
    ports: ["443:443", "80:80"]
    volumes:
      - ./docker/nginx.conf:/etc/nginx/nginx.conf:ro
      - ./certs:/etc/nginx/certs:ro
    depends_on: [bot]

volumes:
  mysql_data:
  redis_data:
```

### 14.2 Dockerfile (multi-stage)

```dockerfile
FROM python:3.12-slim AS builder
WORKDIR /build
RUN pip install --no-cache-dir uv
COPY pyproject.toml uv.lock ./
RUN uv export --no-dev --format requirements-txt > requirements.txt && \
    pip install --no-cache-dir --target /deps -r requirements.txt

FROM python:3.12-slim
RUN useradd -m -u 1000 botuser && apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY --from=builder /deps /deps
ENV PYTHONPATH=/deps PYTHONUNBUFFERED=1
COPY --chown=botuser:botuser app/ ./app/
COPY --chown=botuser:botuser alembic/ ./alembic/
COPY --chown=botuser:botuser alembic.ini .
USER botuser
EXPOSE 8080
CMD ["python", "-m", "app.main"]
```

### 14.3 nginx config (core)

```nginx
server {
    listen 443 ssl http2;
    server_name bot.example.com;

    ssl_certificate     /etc/nginx/certs/fullchain.pem;
    ssl_certificate_key /etc/nginx/certs/privkey.pem;

    # Telegram only — restrict to their IP ranges
    # 149.154.160.0/20 and 91.108.4.0/22
    allow 149.154.160.0/20;
    allow 91.108.4.0/22;
    deny  all;

    location = /webhook/{{SECRET_PATH}} {
        proxy_pass         http://bot:8080/webhook;
        proxy_set_header   X-Telegram-Bot-Api-Secret-Token $http_x_telegram_bot_api_secret_token;
        client_max_body_size 20M;
    }

    location = /healthz {
        proxy_pass http://bot:8080/healthz;
        allow 127.0.0.1;
        deny all;
    }
}
```

The path itself contains a secret segment; the header is also verified inside the app. Belt and braces.

### 14.4 Startup order & migrations

`make deploy` runs:
```
1. docker compose pull
2. docker compose up -d mysql redis
3. wait for mysql healthy
4. docker compose run --rm bot alembic upgrade head
5. docker compose up -d bot nginx
6. curl https://bot.example.com/healthz  # smoke test
```

Migrations are **always run by a one-shot container**, never by the live bot at startup. Prevents two replicas racing (future-proof) and makes rollbacks explicit.

---

## 15. Logging & Monitoring

### 15.1 Structured logging (`structlog`)

```python
logger = structlog.get_logger()
logger.info("receipt_submitted", user_id=user.id, telegram_id=user.telegram_id, receipt_id=receipt.id)
```

JSON output in prod:
```json
{"event": "receipt_submitted", "user_id": 42, "telegram_id": 123456789, "receipt_id": 7, "request_id": "abc123", "timestamp": "2026-05-21T11:14:32Z", "level": "info"}
```

`LoggingMiddleware` binds `request_id`, `telegram_id`, `update_type` into context for every handler.

### 15.2 Sentry

Init in `app/core/sentry.py`. Captures unhandled exceptions from handlers + scheduled jobs. PII scrubbing — never send phone numbers or receipt photos.

### 15.3 Metrics (optional, can be deferred to v1.1)

Prometheus endpoint at `/metrics`, scraped by infra:
- `bot_updates_total{type, handler}`
- `bot_handler_duration_seconds{handler}` (histogram)
- `bot_errors_total{error_type}`
- `bot_attempts_in_progress` (gauge)
- `bot_broadcast_messages_total{result}`

### 15.4 Operational alerts (out-of-band, manual setup)

- Sentry error rate > 5/min → notify
- /healthz failing > 2 min → notify
- Pending receipts older than 24h → bot self-notifies in admin group (already in §11)

---

## 16. Error Handling

### 16.1 Exception hierarchy (`app/exceptions.py`)

```python
class BotError(Exception): ...

class UserError(BotError):
    """Surfaced to the user with a friendly message."""
    user_message: str

class InvalidNameError(UserError):
    user_message = "Пожалуйста, введите корректное имя."

class ReceiptLimitExceededError(UserError):
    user_message = "У вас уже есть чеки на проверке."

class TestParseError(UserError):
    """Carries a list of (line, message) tuples for admin display."""

class NotApprovedError(UserError):
    user_message = "Сначала нужно оплатить подготовку."

class NoActiveTestError(UserError):
    user_message = "Сейчас нет доступных тестов."

class AttemptAlreadyExistsError(UserError):
    """Carries the existing attempt for showing prior result."""

class SystemError(BotError):
    """Unexpected; logged + Sentry + generic message to user."""
```

### 16.2 Global error handler

```python
async def global_error_handler(event: ErrorEvent):
    exc = event.exception
    update = event.update

    if isinstance(exc, UserError):
        # Friendly message back to user; don't log as error
        await send_user_message(update, exc.user_message)
        return

    # Anything else: log + Sentry + safe fallback
    logger.exception("unhandled_exception", update=update.model_dump())
    sentry_sdk.capture_exception(exc)
    await send_user_message(update, "Произошла ошибка. Попробуйте позже.")
```

### 16.3 Telegram API errors

- `TelegramRetryAfter`: respect `retry_after`, backoff, retry once. If still failing, log and skip.
- `TelegramForbiddenError` (user blocked the bot): mark in DB (`user.bot_blocked = True`), don't retry future broadcasts to this user.
- `TelegramNotFound` on message edit (user deleted the chat): swallow, log.

---

## 17. Testing Strategy

### 17.1 Pyramid

| Layer | Coverage target | Tools |
|---|---|---|
| Unit tests (services, views, utils) | 90%+ | pytest, plain mocks |
| Repository tests | All public methods | pytest + testcontainers MySQL |
| Handler tests | All happy paths + key sad paths | pytest + aiogram's `MockedBot` |
| End-to-end flow tests | 5 critical journeys | pytest + testcontainers + mocked Bot |

### 17.2 Critical flows to E2E test

1. **Onboarding → Payment → Approval → First Test → Score**
2. **Receipt rejection + resubmission**
3. **Test auto-submit on time expiry** (uses scheduler with shortened duration)
4. **Resume after bot restart mid-attempt** (restart Redis between actions, verify state)
5. **Duplicate receipt detection** (submit same image twice)

### 17.3 Test data

`tests/fixtures/sample_test.xlsx` — a valid 50-question Excel file used in parser tests.
`tests/fixtures/invalid_*.xlsx` — files exercising each parse error.

### 17.4 What we explicitly do NOT mock

- The SQLAlchemy models / repositories in integration tests — use real MySQL (testcontainers spins one up). Mocking the ORM gives false confidence.

---

## 18. CI/CD

GitHub Actions (or equivalent). On push:

1. **Lint** — `ruff check .` and `ruff format --check .`
2. **Type check** — `mypy app/services app/repositories`
3. **Unit tests** — `pytest tests/unit`
4. **Integration tests** — spin up MySQL + Redis via service containers, run `pytest tests/integration`
5. **Build Docker image** — tagged with git SHA
6. **(On main) Push image to registry**
7. **(Manual trigger) Deploy** — SSH to server, pull image, run `make deploy`

No auto-deploy to prod. The teacher's bot is small enough that a human-gated release is fine and reduces risk.

---

## 19. Code Conventions

- **Type hints required** on every function signature in `services/` and `repositories/`. Enforced by mypy.
- **Async everywhere.** No blocking IO in handlers or services. (openpyxl is sync; we run `parser.parse()` inside `asyncio.to_thread()` for large files. 50 rows is small enough we don't bother in practice.)
- **Naming:** snake_case for files/functions/vars, PascalCase for classes, SCREAMING_SNAKE for constants.
- **No `from x import *`.** No exceptions.
- **One class per file** for models, services, repositories. Multiple small classes per file OK for callback data factories, exceptions, DTOs.
- **Docstrings** required on every public service method — what, not how. One paragraph max.
- **TODO comments** must include a date and author: `# TODO(2026-06-01, kamronbek): handle X`.
- **No `print()`** anywhere outside of `scripts/`. Use the logger.

---

## 20. Local Development

`make dev` starts MySQL + Redis via docker-compose, runs migrations, seeds dev fixtures (one admin user, one approved user, one sample test), starts the bot in polling mode (not webhook) for easier iteration:

```python
# app/main.py — dev branch
if settings.env == "dev":
    await dp.start_polling(bot)
else:
    await start_webhook(bot, dp, settings)
```

`make test` runs the full suite.
`make migrate name="add_xyz"` creates a new Alembic revision.

---

## 21. Open Engineering Questions

1. **Single vs multi-process.** v1 is single-process. If we ever need multi, we need: (a) a sticky load balancer or stateless handlers, (b) APScheduler moved to a separate worker process (or replaced with Celery), (c) explicit locking for receipt approvals. **Defer until usage data demands it.**

2. **Scheduled job replay on Redis flush.** §11.3 reconciles `in_progress` attempts. But warning messages already sent are not tracked. Risk: bot restart at minute 51 causes the 10-min warning to be re-sent. **Mitigation:** persist a `warning_X_sent_at` column on attempts; check before sending. (To be decided with DB engineer in Phase 3.)

3. **Image hash storage format.** pHash is 64 bits, naturally fits in BIGINT. Hamming distance via XOR + bit count, indexed via locality-sensitive hashing or BK-tree if we ever exceed 100K receipts. **For v1:** linear scan over a small set is fine.

4. **Question text rendering.** Some questions contain special chars. We HTML-escape user-facing text by default. If the teacher wants to use HTML/Markdown formatting in questions (bold, italics), we'd need a markup column to opt-in per question. **Decision:** plain text only in v1.

5. **Receipt photo retention.** We store only `file_id`; Telegram is the source of truth. But `file_id`s expire if not used. **Mitigation:** download + store the photo bytes on first receipt submission, in S3 or local disk. **Decision:** defer to v1.1 — for now, accept the small risk that very old receipts may not display. We retain the hash either way for dedup.

---

## 22. Acceptance Criteria for v1

Engineering-side checks before declaring v1 done:

- [ ] All routes covered by either unit + integration tests, or an explicit `pytest.skip` with reason
- [ ] `ruff check .` and `mypy` clean on services & repositories
- [ ] CI pipeline green on main
- [ ] Bot restarts during a load test (50 concurrent attempts) without losing state or producing user-visible errors
- [ ] Receipt approval roundtrip latency p95 < 1 second
- [ ] Test-screen edit latency p95 < 500ms
- [ ] Sentry receiving from a staged exception (test the integration)
- [ ] All copy strings read from settings table, none hardcoded outside `app/core/i18n.py`'s seed defaults
- [ ] Docker image < 250 MB
- [ ] `make deploy` runbook tested on staging
- [ ] Backup script for MySQL set up and tested (mysqldump nightly to off-machine storage)

---

## Hand-off to Phase 3 (DB Spec)

What the database spec must define:

1. Every table, column, type, nullability, default
2. Indexes for every query pattern this document implies (see callouts below)
3. Foreign keys and ON DELETE behavior
4. The `settings` table's seed data (default copy from PRODUCT_BLUEPRINT §11)
5. The Alembic initial migration

**Query patterns the schema must support efficiently:**

- Find user by `telegram_id` (used on every update) — primary key or unique index
- Find user by `phone`, `username`, or `reference_code` — for `/find`
- Count pending receipts per user — for §8.2 limit
- Find duplicate receipt by hash — perceptual match within Hamming distance ≤ 5
- Find the currently active test
- List all questions of a test, ordered by position
- Read an attempt with all its answers (joined) for state reconstruction
- List in-progress attempts (for startup reconciliation, §11.3)
- Aggregate per-section scores for `/leaderboard`
- Count per-question correctness across attempts of a test (for §16 analytics)
- List approved users (for broadcast)
- List receipts older than N hours in `pending` state (for reminder job)

These should be benchmarked at 10K users / 100K attempts / 5M answers to be sure indexes are right.
