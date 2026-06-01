# Database Engineering Specification

**Document type:** Database spec (Phase 3 of 3)
**Version:** 1.0
**Date:** 2026-05-21
**Status:** Draft
**Author role:** Senior Database Engineer
**Predecessors:** `PRODUCT_BLUEPRINT.md`, `ARCHITECTURE_SPEC.md`
**Scope:** Complete physical schema, indexes, constraints, FK policy, seed data, initial migration, query patterns. This is the contract between the application code and the storage layer.

---

## 1. Database Engine

| Choice | Value | Rationale |
|---|---|---|
| RDBMS | MySQL 8.4 LTS | Stated requirement; mature async driver (asyncmy); proven at this scale |
| Engine | InnoDB | Only sensible choice — row-level locking, FKs, transactions, crash safety |
| Charset | `utf8mb4` | Full UTF-8 (Russian + emoji in copy) |
| Collation | `utf8mb4_unicode_ci` | Case-insensitive, locale-aware; works for both Cyrillic and Latin |
| Time zone | UTC at the server | Application stores and reads UTC; conversion to Asia/Tashkent is presentation-layer only |
| `sql_mode` | `STRICT_TRANS_TABLES,NO_ZERO_IN_DATE,NO_ZERO_DATE,ERROR_FOR_DIVISION_BY_ZERO` | Default in MySQL 8; fail loudly rather than silently truncate |
| Isolation level | `REPEATABLE READ` (default) | Default; our writes are short-transactional, no hotspot risk |

**Connection settings (server `my.cnf`):**

```ini
[mysqld]
character-set-server     = utf8mb4
collation-server         = utf8mb4_unicode_ci
default-time-zone        = '+00:00'
innodb_buffer_pool_size  = 1G          # tune to ~70% of available RAM on prod
innodb_file_per_table    = ON
innodb_flush_log_at_trx_commit = 1     # full durability; receipts and money matter
max_connections          = 100
slow_query_log           = ON
slow_query_log_file      = /var/log/mysql/slow.log
long_query_time          = 0.5         # log anything >500ms
```

---

## 2. Schema Principles

These principles dictate every type decision and constraint below:

1. **Strict typing.** No `VARCHAR(255)` everywhere out of laziness. Field length comes from actual product constraints. If the column is bounded, the database enforces the bound.
2. **NOT NULL by default.** Every column is `NOT NULL` unless nullability is meaningful. "Unknown yet" is a meaning; "lazy schema design" is not.
3. **Foreign keys enforced.** All references between tables are FK-constrained. ON DELETE behavior is explicit per table (§7).
4. **Application-enforced invariants are documented.** Some rules (one active test, unique reference code) are enforced by the application layer because the database cannot express them efficiently. These are listed in §6 alongside DB-enforced constraints.
5. **Timestamps everywhere.** Every business entity has `created_at`. Mutable entities also have `updated_at`. Both are `DATETIME(6)` (microsecond precision) in UTC.
6. **Soft over hard delete.** v1 never `DELETE`s a business row. Status fields express "removed" semantics. The schema permits hard delete via FK cascade only for child entities (questions, answers).
7. **Index for every documented query.** Every query pattern in §10 has at least one supporting index. No "we'll add it later" — production starts indexed.
8. **No surrogate-key worship.** Every business table has an `id BIGINT AUTO_INCREMENT PRIMARY KEY`, but the **logical** identifier (`telegram_id` for users, `reference_code` for users, `(test_id, position)` for questions) gets a unique constraint of its own.
9. **TEXT only where genuinely unbounded.** `VARCHAR(N)` is preferred over `TEXT` because (a) it enforces a real limit, (b) it can sit on the row directly and not in overflow pages for short values.

---

## 3. Naming Conventions

| Element | Convention | Example |
|---|---|---|
| Table | `snake_case`, plural noun | `users`, `payment_receipts` |
| Column | `snake_case`, no abbreviations | `telegram_id`, `created_at` |
| PK | `id` | — |
| FK column | `<referenced_singular>_id` | `user_id`, `test_id` |
| Index | `ix_<table>__<cols>` | `ix_receipts__user_id_status` |
| Unique index | `ux_<table>__<cols>` | `ux_users__telegram_id` |
| FK constraint | `fk_<table>__<col>` | `fk_attempts__user_id` |
| Check constraint | `ck_<table>__<rule>` | `ck_users__status_enum` |
| Boolean | `is_<adjective>` or `<verb>_<entity>` | `is_correct`, `bot_blocked` |
| Timestamp | `<verb>_at` | `created_at`, `reviewed_at` |

---

## 4. Schema Overview (Entity Relationships)

```
                  ┌─────────────┐
                  │   admins    │
                  └──────┬──────┘
                         │ (reviewed_by, created_by, updated_by)
                         │
   ┌──────────┐    ┌─────▼────────────┐    ┌─────────────┐
   │  users   │◀───┤ payment_receipts │    │  settings   │
   └────┬─────┘    └──────────────────┘    └─────────────┘
        │
        │
   ┌────▼──────┐         ┌──────────┐         ┌────────────┐
   │ attempts  ├────────▶│  tests   │◀────────┤ questions  │
   └────┬──────┘         └──────────┘         └─────┬──────┘
        │                                            │
        │           ┌──────────┐                     │
        └──────────▶│ answers  │◀────────────────────┘
                    └──────────┘
```

**Cardinality summary:**
- A user has 0..N receipts, 0..N attempts.
- A test has exactly 50 questions, 0..N attempts.
- An attempt has 0..50 answers (one per question, only inserted when user picks an option).
- An admin reviews 0..N receipts, creates 0..N tests, updates 0..N settings.
- A user has 0..1 admin row (a user who is also an admin).

---

## 5. Tables

For every table: full DDL-style definition, column-by-column meaning, why each type and constraint is as shown.

### 5.1 `users`

The single most-read table in the system. Looked up by `telegram_id` on every incoming update.

```sql
CREATE TABLE users (
    id              BIGINT UNSIGNED AUTO_INCREMENT,
    telegram_id     BIGINT NOT NULL,
    username        VARCHAR(64)        NULL,
    full_name       VARCHAR(200)       NULL,
    phone           VARCHAR(32)        NULL,
    reference_code  CHAR(6)            NULL,
    status          VARCHAR(32)    NOT NULL DEFAULT 'new',
    bot_blocked     TINYINT(1)     NOT NULL DEFAULT 0,
    created_at      DATETIME(6)    NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at      DATETIME(6)    NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
                                            ON UPDATE CURRENT_TIMESTAMP(6),
    approved_at     DATETIME(6)        NULL,

    PRIMARY KEY (id),
    UNIQUE KEY ux_users__telegram_id (telegram_id),
    UNIQUE KEY ux_users__reference_code (reference_code),
    KEY ix_users__phone (phone),
    KEY ix_users__username (username),
    KEY ix_users__status (status),

    CONSTRAINT ck_users__status_enum CHECK (status IN (
        'new',
        'onboarding_phone',
        'onboarding_name',
        'pending_payment',
        'pending_approval',
        'rejected',
        'approved',
        'banned'
    ))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
```

**Column notes:**
- `telegram_id` — `BIGINT` signed; Telegram IDs are positive but never use the high bit. Indexed UNIQUE because every update lookup is `WHERE telegram_id = ?`.
- `username` — nullable; Telegram users may not have one.
- `full_name`, `phone` — nullable until onboarding completes.
- `reference_code` — UNIQUE but nullable; assigned when entering `pending_payment`. The unique index on a nullable column allows multiple NULLs in MySQL (unlike Postgres' behavior — convenient for us).
- `status` — `VARCHAR(32)` + `CHECK` rather than `ENUM`. ENUM is more compact but altering it requires `ALTER TABLE`; with CHECK we can add a new status with just a migration script and the check rewritten. Status changes are rare and we value flexibility.
- `bot_blocked` — set to `1` when we get `TelegramForbiddenError` sending to this user. Used to skip them in broadcasts.
- `approved_at` — separate from `updated_at` because we want it preserved across other status changes (e.g., banned later).

**Index choices:**
- `ix_users__phone`, `ix_users__username` — for the admin `/find` command.
- `ix_users__status` — for the broadcast query (`WHERE status = 'approved' AND bot_blocked = 0`). Selectivity is moderate but the query is run on every test publish, so it earns an index.

### 5.2 `admins`

Separated from `users` because:
1. Admins exist before they `/start` the bot (initial seed).
2. Role semantics ("owner" vs "moderator") are admin-specific.
3. A clear, queryable list of "who can do admin things" is operationally useful.

```sql
CREATE TABLE admins (
    id             BIGINT UNSIGNED AUTO_INCREMENT,
    telegram_id    BIGINT         NOT NULL,
    user_id        BIGINT UNSIGNED    NULL,
    role           VARCHAR(16)    NOT NULL DEFAULT 'moderator',
    added_by_admin_id BIGINT UNSIGNED NULL,
    added_at       DATETIME(6)    NOT NULL DEFAULT CURRENT_TIMESTAMP(6),

    PRIMARY KEY (id),
    UNIQUE KEY ux_admins__telegram_id (telegram_id),
    KEY ix_admins__user_id (user_id),

    CONSTRAINT fk_admins__user_id
        FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE SET NULL,
    CONSTRAINT fk_admins__added_by
        FOREIGN KEY (added_by_admin_id) REFERENCES admins (id) ON DELETE SET NULL,

    CONSTRAINT ck_admins__role_enum CHECK (role IN ('owner', 'moderator'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
```

**Column notes:**
- `telegram_id` UNIQUE NOT NULL — the source of identity. Initial admin is seeded via SQL with the teacher's Telegram ID.
- `user_id` nullable FK — populated lazily when the admin first interacts with the bot and a `users` row is created for them.
- `role` — `owner` can add/remove other admins; `moderator` can only approve receipts and publish tests. v1 has effectively one `owner` (the teacher). The role column is here so future RBAC is a schema-no-op.
- `added_by_admin_id` — self-referential; null only for the seed admin.

### 5.3 `payment_receipts`

```sql
CREATE TABLE payment_receipts (
    id                     BIGINT UNSIGNED AUTO_INCREMENT,
    user_id                BIGINT UNSIGNED NOT NULL,
    telegram_file_id       VARCHAR(256)    NOT NULL,
    telegram_file_unique_id VARCHAR(64)     NOT NULL,
    image_phash            BIGINT UNSIGNED     NULL,
    status                 VARCHAR(16)     NOT NULL DEFAULT 'pending',
    rejection_reason       VARCHAR(500)        NULL,
    reviewed_by_admin_id   BIGINT UNSIGNED     NULL,
    admin_notification_message_id BIGINT       NULL,
    created_at             DATETIME(6)     NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    reviewed_at            DATETIME(6)         NULL,

    PRIMARY KEY (id),
    KEY ix_receipts__user_id_status (user_id, status),
    KEY ix_receipts__status_created (status, created_at),
    KEY ix_receipts__phash (image_phash),

    CONSTRAINT fk_receipts__user_id
        FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE RESTRICT,
    CONSTRAINT fk_receipts__reviewed_by
        FOREIGN KEY (reviewed_by_admin_id) REFERENCES admins (id) ON DELETE SET NULL,

    CONSTRAINT ck_receipts__status_enum CHECK (status IN ('pending', 'approved', 'rejected')),
    CONSTRAINT ck_receipts__rejected_has_reason CHECK (
        status <> 'rejected' OR rejection_reason IS NOT NULL
    ),
    CONSTRAINT ck_receipts__reviewed_has_admin CHECK (
        status = 'pending' OR reviewed_by_admin_id IS NOT NULL
    )
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
```

**Column notes:**
- `telegram_file_id` — Telegram's transient identifier for a file. Long-lived in practice but not guaranteed. Used for re-displaying the photo in admin tools.
- `telegram_file_unique_id` — Telegram's stable identifier for the same file content across bots and users. We store both because they serve different purposes; `file_unique_id` is what you'd persist if doing your own dedup.
- `image_phash` — 64-bit perceptual hash. NULL is permitted for the brief window during async insertion before the hash is computed; in practice always set on commit. `BIGINT UNSIGNED` so we can use the full 64 bits and compute Hamming distance via bitwise XOR + `BIT_COUNT()` in SQL if desired.
- `admin_notification_message_id` — the message ID of the receipt's posting in the admin group. We store it so we can later edit the message to "✅ Одобрено @admin" and remove the buttons (see Architecture Spec §8.3).
- `rejection_reason` — required when status is `rejected`, enforced by `ck_receipts__rejected_has_reason`.
- FK `user_id` is `ON DELETE RESTRICT` — receipts are financial evidence, never delete them via cascade.

**Index choices:**
- `ix_receipts__user_id_status` — covers "how many pending receipts does this user have?" (§8.2 of Architecture Spec).
- `ix_receipts__status_created` — covers the pending-reminder scan: `WHERE status = 'pending' AND created_at < NOW() - INTERVAL 24 HOUR`.
- `ix_receipts__phash` — exact-match index. **Note:** Hamming-distance fuzzy match is a full scan of the candidate set in v1 (acceptable at our scale). See §11 for the upgrade path.

### 5.4 `tests`

```sql
CREATE TABLE tests (
    id                    BIGINT UNSIGNED AUTO_INCREMENT,
    title                 VARCHAR(200)    NOT NULL,
    status                VARCHAR(16)     NOT NULL DEFAULT 'draft',
    duration_seconds      INT UNSIGNED    NOT NULL DEFAULT 3200,
    created_by_admin_id   BIGINT UNSIGNED     NULL,
    created_at            DATETIME(6)     NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    published_at          DATETIME(6)         NULL,
    archived_at           DATETIME(6)         NULL,

    PRIMARY KEY (id),
    KEY ix_tests__status (status),
    KEY ix_tests__published_at (published_at),

    CONSTRAINT fk_tests__created_by
        FOREIGN KEY (created_by_admin_id) REFERENCES admins (id) ON DELETE SET NULL,

    CONSTRAINT ck_tests__status_enum CHECK (status IN ('draft', 'active', 'archived')),
    CONSTRAINT ck_tests__duration_positive CHECK (duration_seconds > 0),
    CONSTRAINT ck_tests__active_has_published_at CHECK (
        status = 'draft' OR published_at IS NOT NULL
    ),
    CONSTRAINT ck_tests__archived_has_archived_at CHECK (
        status <> 'archived' OR archived_at IS NOT NULL
    )
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
```

**The "only one active test at a time" invariant** is enforced by the application layer (`TestService.publish` archives the prior active test in the same transaction as activating the new one). MySQL 8 *can* express it with a generated column + unique index:

```sql
ALTER TABLE tests
  ADD COLUMN is_active_flag TINYINT GENERATED ALWAYS AS
      (CASE WHEN status = 'active' THEN 1 END) VIRTUAL,
  ADD UNIQUE KEY ux_tests__one_active (is_active_flag);
```

I'm **not including** this in the base schema because:
- It complicates `INSERT` and `UPDATE` order (the new test must be inserted as `draft` first, then activated *after* the old one is archived, otherwise the unique fails mid-transaction).
- The application enforces it correctly inside a single transaction anyway.

If we ever lose confidence in the application layer, the column above is the bolt-on.

### 5.5 `questions`

```sql
CREATE TABLE questions (
    id              BIGINT UNSIGNED AUTO_INCREMENT,
    test_id         BIGINT UNSIGNED NOT NULL,
    section         VARCHAR(16)     NOT NULL,
    position        TINYINT UNSIGNED NOT NULL,
    question_text   TEXT            NOT NULL,
    option_a        VARCHAR(500)    NOT NULL,
    option_b        VARCHAR(500)    NOT NULL,
    option_c        VARCHAR(500)    NOT NULL,
    option_d        VARCHAR(500)    NOT NULL,
    correct_option  CHAR(1)         NOT NULL,
    created_at      DATETIME(6)     NOT NULL DEFAULT CURRENT_TIMESTAMP(6),

    PRIMARY KEY (id),
    UNIQUE KEY ux_questions__test_position (test_id, position),

    CONSTRAINT fk_questions__test_id
        FOREIGN KEY (test_id) REFERENCES tests (id) ON DELETE CASCADE,

    CONSTRAINT ck_questions__section_enum CHECK (section IN ('rus_tili', 'pedagogik', 'kasbiy')),
    CONSTRAINT ck_questions__correct_enum CHECK (correct_option IN ('A', 'B', 'C', 'D')),
    CONSTRAINT ck_questions__position_range CHECK (position BETWEEN 1 AND 50),
    CONSTRAINT ck_questions__section_position_consistent CHECK (
        (section = 'rus_tili'  AND position BETWEEN 1  AND 35) OR
        (section = 'pedagogik' AND position BETWEEN 36 AND 45) OR
        (section = 'kasbiy'    AND position BETWEEN 46 AND 50)
    )
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
```

**Column notes:**
- `question_text` is `TEXT` (not `VARCHAR(1000)`) because Russian questions can run long, and `TEXT` doesn't take row-bytes when stored. The blueprint's 1000-char limit is application-enforced at parse time.
- `option_*` as four flat columns rather than JSON because v1 strictly has 4 options. Schema migrates if that ever changes.
- `correct_option` as `CHAR(1)`, always uppercase. Parser normalizes.
- The `ck_questions__section_position_consistent` constraint mirrors the Excel parser's validation directly in the database — defense in depth. Anyone bypassing the parser (e.g., a future SQL import) gets stopped here too.
- `ON DELETE CASCADE` on `test_id` — a test's questions die with the test. Service layer prevents test deletion under normal operation, but if a draft is cancelled, its questions go too.

**Index choices:**
- `ux_questions__test_position` — also covers "fetch all questions of a test ordered by position" because MySQL can scan the unique index in order.

### 5.6 `attempts`

```sql
CREATE TABLE attempts (
    id                       BIGINT UNSIGNED AUTO_INCREMENT,
    user_id                  BIGINT UNSIGNED NOT NULL,
    test_id                  BIGINT UNSIGNED NOT NULL,
    status                   VARCHAR(16)     NOT NULL DEFAULT 'in_progress',
    current_position         TINYINT UNSIGNED NOT NULL DEFAULT 1,
    started_at               DATETIME(6)     NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    finished_at              DATETIME(6)         NULL,
    expires_at               DATETIME(6)     NOT NULL,

    score_total_correct      TINYINT UNSIGNED    NULL,
    score_rus_tili_correct   TINYINT UNSIGNED    NULL,
    score_pedagogik_correct  TINYINT UNSIGNED    NULL,
    score_kasbiy_correct     TINYINT UNSIGNED    NULL,

    warning_10min_sent_at    DATETIME(6)         NULL,
    warning_5min_sent_at     DATETIME(6)         NULL,
    warning_1min_sent_at     DATETIME(6)         NULL,

    PRIMARY KEY (id),
    UNIQUE KEY ux_attempts__user_test (user_id, test_id),
    KEY ix_attempts__status (status),
    KEY ix_attempts__test_score (test_id, score_total_correct DESC),
    KEY ix_attempts__expires (expires_at, status),

    CONSTRAINT fk_attempts__user_id
        FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE RESTRICT,
    CONSTRAINT fk_attempts__test_id
        FOREIGN KEY (test_id) REFERENCES tests (id) ON DELETE RESTRICT,

    CONSTRAINT ck_attempts__status_enum CHECK (status IN ('in_progress', 'submitted', 'expired')),
    CONSTRAINT ck_attempts__current_position_range CHECK (current_position BETWEEN 1 AND 50),
    CONSTRAINT ck_attempts__finished_consistent CHECK (
        (status = 'in_progress' AND finished_at IS NULL) OR
        (status <> 'in_progress' AND finished_at IS NOT NULL)
    ),
    CONSTRAINT ck_attempts__score_total_when_finished CHECK (
        status = 'in_progress' OR score_total_correct IS NOT NULL
    ),
    CONSTRAINT ck_attempts__score_ranges CHECK (
        (score_rus_tili_correct  IS NULL OR score_rus_tili_correct  BETWEEN 0 AND 35) AND
        (score_pedagogik_correct IS NULL OR score_pedagogik_correct BETWEEN 0 AND 10) AND
        (score_kasbiy_correct    IS NULL OR score_kasbiy_correct    BETWEEN 0 AND 5)  AND
        (score_total_correct     IS NULL OR score_total_correct     BETWEEN 0 AND 50)
    )
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
```

**Column notes:**
- `expires_at` — denormalized from `started_at + duration_seconds`. Stored so we can query expiring attempts efficiently without joining `tests`. This is the indexed column for the auto-submit reconciliation job.
- `current_position` — persisted (not only in Redis FSM) so the user resumes at the right question after a Redis flush. See Architecture Spec §10.3.
- `score_*_correct` — raw counts (0–35, 0–10, 0–5, 0–50). Percentages computed at render time.
- `warning_*_sent_at` — set when the corresponding warning message is dispatched. Prevents duplicate warnings on scheduler replay after bot restart (resolves open question §21.2 of Architecture Spec).

**Index choices:**
- `ux_attempts__user_test` — enforces "one attempt per user per test" (business rule §9.4 of Blueprint) and accelerates "does this user already have an attempt on this test?" check.
- `ix_attempts__status` — for startup reconciliation: `WHERE status = 'in_progress'`.
- `ix_attempts__test_score` — for the leaderboard query. Reverse index on score lets MySQL scan in sorted order without a filesort.
- `ix_attempts__expires` — for the periodic safety-net sweep that catches attempts whose scheduler job died: `WHERE status = 'in_progress' AND expires_at < NOW()`.

### 5.7 `answers`

```sql
CREATE TABLE answers (
    id              BIGINT UNSIGNED AUTO_INCREMENT,
    attempt_id      BIGINT UNSIGNED NOT NULL,
    question_id     BIGINT UNSIGNED NOT NULL,
    selected_option CHAR(1)         NOT NULL,
    is_correct      TINYINT(1)      NOT NULL,
    answered_at     DATETIME(6)     NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    updated_at      DATETIME(6)     NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
                                                  ON UPDATE CURRENT_TIMESTAMP(6),

    PRIMARY KEY (id),
    UNIQUE KEY ux_answers__attempt_question (attempt_id, question_id),
    KEY ix_answers__question_is_correct (question_id, is_correct),

    CONSTRAINT fk_answers__attempt_id
        FOREIGN KEY (attempt_id) REFERENCES attempts (id) ON DELETE CASCADE,
    CONSTRAINT fk_answers__question_id
        FOREIGN KEY (question_id) REFERENCES questions (id) ON DELETE RESTRICT,

    CONSTRAINT ck_answers__selected_enum CHECK (selected_option IN ('A', 'B', 'C', 'D'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
```

**Column notes:**
- An answer row is **inserted only when the user picks an option**. Absence = unanswered. Lighter than pre-populating 50 rows per attempt.
- `is_correct` is **denormalized at write time**, computed by comparing `selected_option` to `questions.correct_option`. We could compute it at read time via JOIN, but: (a) we score frequently, (b) it lets per-question correctness analytics (`ix_answers__question_is_correct`) work without a join.
- `updated_at` — the user can change their answer (tap A, then change to B). The row is `UPSERT`ed via `INSERT ... ON DUPLICATE KEY UPDATE`.

**Index choices:**
- `ux_answers__attempt_question` — enforces "one answer per question per attempt" and accelerates score computation (the answers of one attempt all live close on this index).
- `ix_answers__question_is_correct` — for the per-question correctness analytics: "What percentage of attempts got Q42 right?"

### 5.8 `settings`

```sql
CREATE TABLE settings (
    `key`                 VARCHAR(64)  NOT NULL,
    value                 TEXT         NOT NULL,
    description           VARCHAR(500)     NULL,
    updated_by_admin_id   BIGINT UNSIGNED  NULL,
    updated_at            DATETIME(6)  NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
                                                ON UPDATE CURRENT_TIMESTAMP(6),

    PRIMARY KEY (`key`),

    CONSTRAINT fk_settings__updated_by
        FOREIGN KEY (updated_by_admin_id) REFERENCES admins (id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
```

**Column notes:**
- The primary key is the key itself — settings are looked up by name. ~20 rows total, so no scaling concern.
- `value` is `TEXT` because welcome message and payment instructions can be long.
- `description` documents what each key does, in Russian, for the admin's benefit when running `/settings`.
- ``key`` is back-ticked in DDL because it's a MySQL reserved word; the column is otherwise just `key`.

Seed data in §8.

---

## 6. Constraints Summary

### 6.1 Constraints the database enforces

| Constraint | Table | Mechanism |
|---|---|---|
| Telegram ID unique | users | UNIQUE index |
| Reference code unique (when set) | users | UNIQUE index (multiple NULLs allowed in MySQL) |
| User status from allowed set | users | CHECK |
| Admin role from allowed set | admins | CHECK |
| Receipt status from allowed set | payment_receipts | CHECK |
| Rejected receipt has a reason | payment_receipts | CHECK |
| Reviewed receipt has an admin | payment_receipts | CHECK |
| Test status from allowed set | tests | CHECK |
| Active/archived tests have appropriate timestamps | tests | CHECK |
| Question section from allowed set | questions | CHECK |
| Question section ↔ position consistency | questions | CHECK |
| Question position in 1–50 | questions | CHECK |
| Question position unique per test | questions | UNIQUE (test_id, position) |
| Correct option from A/B/C/D | questions | CHECK |
| One attempt per user per test | attempts | UNIQUE (user_id, test_id) |
| Attempt status from allowed set | attempts | CHECK |
| Finished status ↔ finished_at consistency | attempts | CHECK |
| Score ranges (0–35, 0–10, 0–5, 0–50) | attempts | CHECK |
| One answer per question per attempt | answers | UNIQUE (attempt_id, question_id) |
| Selected option from A/B/C/D | answers | CHECK |
| All FKs | various | FOREIGN KEY |

### 6.2 Invariants the application enforces (DB cannot or should not)

| Invariant | Where enforced |
|---|---|
| Exactly one test has `status='active'` at any time | `TestService.publish()` — archives prior active, activates new, in one transaction |
| Each test has exactly 50 questions, split 35/10/5 across sections | `ExcelParser.parse()` validates before insert |
| Pending receipts per user ≤ 3 | `ReceiptService.submit()` checks count before insert |
| Receipt approval is irreversible (no flipping approved → rejected) | `ReceiptService.approve/reject()` guard on `status='pending'` |
| Attempt finish is idempotent (calling twice doesn't double-score) | `AttemptService.finish()` guard on `status='in_progress'` |
| Banned users get no interaction | `UserLoaderMiddleware` short-circuits to "Доступ ограничён" |

---

## 7. Foreign Key Policy

| FK | ON DELETE | Rationale |
|---|---|---|
| `payment_receipts.user_id → users.id` | RESTRICT | Receipts are financial evidence; never auto-delete |
| `payment_receipts.reviewed_by_admin_id → admins.id` | SET NULL | An admin leaving shouldn't blank the receipt; we just lose the reviewer reference |
| `attempts.user_id → users.id` | RESTRICT | Preserve attempt history |
| `attempts.test_id → tests.id` | RESTRICT | A test with attempts cannot be deleted |
| `questions.test_id → tests.id` | CASCADE | Questions belong to one test; if test is removed (only possible for cancelled drafts), questions go too |
| `answers.attempt_id → attempts.id` | CASCADE | Answers belong to one attempt |
| `answers.question_id → questions.id` | RESTRICT | A question with answer history cannot be deleted; in practice it's CASCADE-protected via attempts → tests RESTRICT |
| `admins.user_id → users.id` | SET NULL | An admin can predate the user row |
| `admins.added_by_admin_id → admins.id` | SET NULL | Provenance is nice-to-have, not load-bearing |
| `tests.created_by_admin_id → admins.id` | SET NULL | Same |
| `settings.updated_by_admin_id → admins.id` | SET NULL | Same |

**Net effect:** the only entities that can be hard-deleted under normal operation are:
- **Draft tests** (cancelled before publish) → cascades to their questions.
- That's it.

Everything else uses status flags. This is deliberate.

---

## 8. Settings Seed Data

Inserted as part of the initial Alembic migration. All values can be edited later via `/set` commands.

```sql
INSERT INTO settings (`key`, value, description) VALUES
(
  'welcome_message',
  'Здравствуйте! 👋\n\nЭто бот для подготовки к аттестации учителей русского языка.\n\nЗдесь вы сможете:\n✅ Пройти полный пробный тест (50 вопросов)\n✅ Узнать свой балл и оценить готовность\n✅ Попасть в закрытый чат студентов, где преподаватель разбирает каждый тест\n\nСтруктура теста:\n📚 Русский язык — 35 вопросов\n👨‍🏫 Педагогическое мастерство — 10 вопросов\n📋 Профессиональный стандарт — 5 вопросов\n\n⏱ На весь тест отводится 53 минуты 20 секунд.\n\nЧтобы начать, нам нужно немного познакомиться.',
  'Первое сообщение пользователю при /start'
),
(
  'payment_amount',
  '150000',
  'Сумма оплаты в сумах (только число)'
),
(
  'payment_amount_display',
  '150 000 сум',
  'Сумма оплаты в формате для показа пользователю'
),
(
  'payment_card_number',
  '8600 1234 5678 9012',
  'Номер карты для приёма платежей'
),
(
  'payment_recipient_name',
  '[ИМЯ ПРЕПОДАВАТЕЛЯ]',
  'Имя получателя на карте'
),
(
  'payment_instructions',
  'Чтобы получить доступ к тестам, оплатите подготовку:\n\n💰 Сумма: {amount_display}\n💳 Карта: {card_number}\n👤 Получатель: {recipient_name}\n\n📌 ВАЖНО: в комментарии к платежу укажите ваш код:\n#{reference_code}\n\nЭто поможет нам быстро найти ваш платёж.\n\nПосле оплаты нажмите кнопку ниже и отправьте скриншот чека.',
  'Инструкция по оплате (плейсхолдеры: {amount_display}, {card_number}, {recipient_name}, {reference_code})'
),
(
  'group_invite_link',
  '',
  'Ссылка-приглашение в закрытый чат студентов'
),
(
  'support_contact',
  '',
  'Username администратора для кнопки "У меня вопрос" (например, @username)'
),
(
  'msg_receipt_accepted',
  '✅ Чек получен. Мы проверим его в ближайшее время и сообщим вам о решении.',
  'Сообщение пользователю после отправки чека'
),
(
  'msg_approved',
  '🎉 Поздравляем! Ваш платёж подтверждён.\n\nВот ссылка на закрытый чат студентов:\n{group_invite_link}\n\nКогда преподаватель опубликует тест, вы получите уведомление, и сможете пройти его в этом боте.',
  'Сообщение пользователю при одобрении чека (плейсхолдер: {group_invite_link})'
),
(
  'msg_rejected',
  '❌ К сожалению, ваш чек не был одобрен.\n\nПричина: {reason}\n\nВы можете отправить новый чек.',
  'Сообщение пользователю при отклонении чека (плейсхолдер: {reason})'
),
(
  'msg_new_test_broadcast',
  '📢 Доступен новый тест!\n\nОткройте бота и нажмите «Пройти тест», чтобы начать.\n\n⏱ У вас будет 53 минуты 20 секунд.',
  'Рассылка студентам при публикации нового теста'
),
(
  'msg_warning_10min',
  '⏱ Осталось 10 минут до конца теста.',
  'Предупреждение во время теста'
),
(
  'msg_warning_5min',
  '⏱ Осталось 5 минут!',
  'Предупреждение во время теста'
),
(
  'msg_warning_1min',
  '⏱ Осталась 1 минута!',
  'Предупреждение во время теста'
),
(
  'msg_auto_submitted',
  '⏰ Время вышло. Тест автоматически завершён.',
  'Сообщение при автозавершении теста'
),
(
  'msg_already_attempted',
  'Вы уже проходили этот тест.\n\nВаш результат: {score}/50',
  'Сообщение при повторной попытке (плейсхолдер: {score})'
),
(
  'msg_no_active_test',
  'Сейчас нет доступных тестов. Преподаватель опубликует следующий — мы вам сообщим.',
  'Сообщение при отсутствии активного теста'
),
(
  'msg_banned',
  'Доступ к боту ограничён.',
  'Сообщение заблокированному пользователю'
);
```

---

## 9. Initial Alembic Migration

The first revision creates everything above in one shot.

**File:** `alembic/versions/0001_initial_schema.py`

```python
"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-05-21
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import mysql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ---------- users ----------
    op.create_table(
        "users",
        sa.Column("id", mysql.BIGINT(unsigned=True), autoincrement=True, primary_key=True),
        sa.Column("telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("username", sa.String(64), nullable=True),
        sa.Column("full_name", sa.String(200), nullable=True),
        sa.Column("phone", sa.String(32), nullable=True),
        sa.Column("reference_code", sa.CHAR(6), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="new"),
        sa.Column("bot_blocked", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP(6)")),
        sa.Column("updated_at", mysql.DATETIME(fsp=6), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6)")),
        sa.Column("approved_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.UniqueConstraint("telegram_id", name="ux_users__telegram_id"),
        sa.UniqueConstraint("reference_code", name="ux_users__reference_code"),
        sa.CheckConstraint(
            "status IN ('new','onboarding_phone','onboarding_name','pending_payment',"
            "'pending_approval','rejected','approved','banned')",
            name="ck_users__status_enum",
        ),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_unicode_ci",
    )
    op.create_index("ix_users__phone", "users", ["phone"])
    op.create_index("ix_users__username", "users", ["username"])
    op.create_index("ix_users__status", "users", ["status"])

    # ---------- admins ----------
    op.create_table(
        "admins",
        sa.Column("id", mysql.BIGINT(unsigned=True), autoincrement=True, primary_key=True),
        sa.Column("telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", mysql.BIGINT(unsigned=True), nullable=True),
        sa.Column("role", sa.String(16), nullable=False, server_default="moderator"),
        sa.Column("added_by_admin_id", mysql.BIGINT(unsigned=True), nullable=True),
        sa.Column("added_at", mysql.DATETIME(fsp=6), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP(6)")),
        sa.UniqueConstraint("telegram_id", name="ux_admins__telegram_id"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"],
                                ondelete="SET NULL", name="fk_admins__user_id"),
        sa.ForeignKeyConstraint(["added_by_admin_id"], ["admins.id"],
                                ondelete="SET NULL", name="fk_admins__added_by"),
        sa.CheckConstraint("role IN ('owner','moderator')", name="ck_admins__role_enum"),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_unicode_ci",
    )
    op.create_index("ix_admins__user_id", "admins", ["user_id"])

    # ---------- payment_receipts ----------
    op.create_table(
        "payment_receipts",
        sa.Column("id", mysql.BIGINT(unsigned=True), autoincrement=True, primary_key=True),
        sa.Column("user_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("telegram_file_id", sa.String(256), nullable=False),
        sa.Column("telegram_file_unique_id", sa.String(64), nullable=False),
        sa.Column("image_phash", mysql.BIGINT(unsigned=True), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="pending"),
        sa.Column("rejection_reason", sa.String(500), nullable=True),
        sa.Column("reviewed_by_admin_id", mysql.BIGINT(unsigned=True), nullable=True),
        sa.Column("admin_notification_message_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP(6)")),
        sa.Column("reviewed_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"],
                                ondelete="RESTRICT", name="fk_receipts__user_id"),
        sa.ForeignKeyConstraint(["reviewed_by_admin_id"], ["admins.id"],
                                ondelete="SET NULL", name="fk_receipts__reviewed_by"),
        sa.CheckConstraint("status IN ('pending','approved','rejected')",
                           name="ck_receipts__status_enum"),
        sa.CheckConstraint("status <> 'rejected' OR rejection_reason IS NOT NULL",
                           name="ck_receipts__rejected_has_reason"),
        sa.CheckConstraint("status = 'pending' OR reviewed_by_admin_id IS NOT NULL",
                           name="ck_receipts__reviewed_has_admin"),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_unicode_ci",
    )
    op.create_index("ix_receipts__user_id_status", "payment_receipts", ["user_id", "status"])
    op.create_index("ix_receipts__status_created", "payment_receipts", ["status", "created_at"])
    op.create_index("ix_receipts__phash", "payment_receipts", ["image_phash"])

    # ---------- tests ----------
    op.create_table(
        "tests",
        sa.Column("id", mysql.BIGINT(unsigned=True), autoincrement=True, primary_key=True),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="draft"),
        sa.Column("duration_seconds", mysql.INTEGER(unsigned=True), nullable=False,
                  server_default="3200"),
        sa.Column("created_by_admin_id", mysql.BIGINT(unsigned=True), nullable=True),
        sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP(6)")),
        sa.Column("published_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("archived_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.ForeignKeyConstraint(["created_by_admin_id"], ["admins.id"],
                                ondelete="SET NULL", name="fk_tests__created_by"),
        sa.CheckConstraint("status IN ('draft','active','archived')",
                           name="ck_tests__status_enum"),
        sa.CheckConstraint("duration_seconds > 0", name="ck_tests__duration_positive"),
        sa.CheckConstraint("status = 'draft' OR published_at IS NOT NULL",
                           name="ck_tests__active_has_published_at"),
        sa.CheckConstraint("status <> 'archived' OR archived_at IS NOT NULL",
                           name="ck_tests__archived_has_archived_at"),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_unicode_ci",
    )
    op.create_index("ix_tests__status", "tests", ["status"])
    op.create_index("ix_tests__published_at", "tests", ["published_at"])

    # ---------- questions ----------
    op.create_table(
        "questions",
        sa.Column("id", mysql.BIGINT(unsigned=True), autoincrement=True, primary_key=True),
        sa.Column("test_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("section", sa.String(16), nullable=False),
        sa.Column("position", mysql.TINYINT(unsigned=True), nullable=False),
        sa.Column("question_text", sa.Text(), nullable=False),
        sa.Column("option_a", sa.String(500), nullable=False),
        sa.Column("option_b", sa.String(500), nullable=False),
        sa.Column("option_c", sa.String(500), nullable=False),
        sa.Column("option_d", sa.String(500), nullable=False),
        sa.Column("correct_option", sa.CHAR(1), nullable=False),
        sa.Column("created_at", mysql.DATETIME(fsp=6), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP(6)")),
        sa.ForeignKeyConstraint(["test_id"], ["tests.id"],
                                ondelete="CASCADE", name="fk_questions__test_id"),
        sa.UniqueConstraint("test_id", "position", name="ux_questions__test_position"),
        sa.CheckConstraint("section IN ('rus_tili','pedagogik','kasbiy')",
                           name="ck_questions__section_enum"),
        sa.CheckConstraint("correct_option IN ('A','B','C','D')",
                           name="ck_questions__correct_enum"),
        sa.CheckConstraint("position BETWEEN 1 AND 50",
                           name="ck_questions__position_range"),
        sa.CheckConstraint(
            "(section='rus_tili'  AND position BETWEEN 1  AND 35) OR "
            "(section='pedagogik' AND position BETWEEN 36 AND 45) OR "
            "(section='kasbiy'    AND position BETWEEN 46 AND 50)",
            name="ck_questions__section_position_consistent",
        ),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_unicode_ci",
    )

    # ---------- attempts ----------
    op.create_table(
        "attempts",
        sa.Column("id", mysql.BIGINT(unsigned=True), autoincrement=True, primary_key=True),
        sa.Column("user_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("test_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="in_progress"),
        sa.Column("current_position", mysql.TINYINT(unsigned=True), nullable=False,
                  server_default="1"),
        sa.Column("started_at", mysql.DATETIME(fsp=6), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP(6)")),
        sa.Column("finished_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("expires_at", mysql.DATETIME(fsp=6), nullable=False),
        sa.Column("score_total_correct", mysql.TINYINT(unsigned=True), nullable=True),
        sa.Column("score_rus_tili_correct", mysql.TINYINT(unsigned=True), nullable=True),
        sa.Column("score_pedagogik_correct", mysql.TINYINT(unsigned=True), nullable=True),
        sa.Column("score_kasbiy_correct", mysql.TINYINT(unsigned=True), nullable=True),
        sa.Column("warning_10min_sent_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("warning_5min_sent_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.Column("warning_1min_sent_at", mysql.DATETIME(fsp=6), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"],
                                ondelete="RESTRICT", name="fk_attempts__user_id"),
        sa.ForeignKeyConstraint(["test_id"], ["tests.id"],
                                ondelete="RESTRICT", name="fk_attempts__test_id"),
        sa.UniqueConstraint("user_id", "test_id", name="ux_attempts__user_test"),
        sa.CheckConstraint("status IN ('in_progress','submitted','expired')",
                           name="ck_attempts__status_enum"),
        sa.CheckConstraint("current_position BETWEEN 1 AND 50",
                           name="ck_attempts__current_position_range"),
        sa.CheckConstraint(
            "(status = 'in_progress' AND finished_at IS NULL) OR "
            "(status <> 'in_progress' AND finished_at IS NOT NULL)",
            name="ck_attempts__finished_consistent",
        ),
        sa.CheckConstraint("status = 'in_progress' OR score_total_correct IS NOT NULL",
                           name="ck_attempts__score_total_when_finished"),
        sa.CheckConstraint(
            "(score_rus_tili_correct  IS NULL OR score_rus_tili_correct  BETWEEN 0 AND 35) AND "
            "(score_pedagogik_correct IS NULL OR score_pedagogik_correct BETWEEN 0 AND 10) AND "
            "(score_kasbiy_correct    IS NULL OR score_kasbiy_correct    BETWEEN 0 AND 5)  AND "
            "(score_total_correct     IS NULL OR score_total_correct     BETWEEN 0 AND 50)",
            name="ck_attempts__score_ranges",
        ),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_unicode_ci",
    )
    op.create_index("ix_attempts__status", "attempts", ["status"])
    op.create_index("ix_attempts__test_score", "attempts",
                    ["test_id", sa.text("score_total_correct DESC")])
    op.create_index("ix_attempts__expires", "attempts", ["expires_at", "status"])

    # ---------- answers ----------
    op.create_table(
        "answers",
        sa.Column("id", mysql.BIGINT(unsigned=True), autoincrement=True, primary_key=True),
        sa.Column("attempt_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("question_id", mysql.BIGINT(unsigned=True), nullable=False),
        sa.Column("selected_option", sa.CHAR(1), nullable=False),
        sa.Column("is_correct", sa.Boolean(), nullable=False),
        sa.Column("answered_at", mysql.DATETIME(fsp=6), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP(6)")),
        sa.Column("updated_at", mysql.DATETIME(fsp=6), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6)")),
        sa.ForeignKeyConstraint(["attempt_id"], ["attempts.id"],
                                ondelete="CASCADE", name="fk_answers__attempt_id"),
        sa.ForeignKeyConstraint(["question_id"], ["questions.id"],
                                ondelete="RESTRICT", name="fk_answers__question_id"),
        sa.UniqueConstraint("attempt_id", "question_id", name="ux_answers__attempt_question"),
        sa.CheckConstraint("selected_option IN ('A','B','C','D')",
                           name="ck_answers__selected_enum"),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_unicode_ci",
    )
    op.create_index("ix_answers__question_is_correct", "answers", ["question_id", "is_correct"])

    # ---------- settings ----------
    op.create_table(
        "settings",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("description", sa.String(500), nullable=True),
        sa.Column("updated_by_admin_id", mysql.BIGINT(unsigned=True), nullable=True),
        sa.Column("updated_at", mysql.DATETIME(fsp=6), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP(6) ON UPDATE CURRENT_TIMESTAMP(6)")),
        sa.ForeignKeyConstraint(["updated_by_admin_id"], ["admins.id"],
                                ondelete="SET NULL", name="fk_settings__updated_by"),
        mysql_engine="InnoDB",
        mysql_charset="utf8mb4",
        mysql_collate="utf8mb4_unicode_ci",
    )

    # ---------- seed settings ----------
    _seed_settings()


def _seed_settings() -> None:
    """Insert default settings rows. See DATABASE_SPEC.md §8 for the canonical text."""
    op.execute("""
        INSERT INTO settings (`key`, value, description) VALUES
        ('welcome_message', '...', 'Первое сообщение пользователю при /start')
        -- See §8 of DATABASE_SPEC.md for the full INSERT statement.
    """)


def downgrade() -> None:
    op.drop_table("settings")
    op.drop_table("answers")
    op.drop_table("attempts")
    op.drop_table("questions")
    op.drop_table("tests")
    op.drop_table("payment_receipts")
    op.drop_table("admins")
    op.drop_table("users")
```

> The `_seed_settings()` body is abbreviated above for readability. The actual migration includes the full INSERT statement from §8 verbatim.

---

## 10. Query Patterns

Every query the application runs, with its supporting index and expected explain plan.

### 10.1 Lookup user by Telegram ID (every update)

```sql
SELECT id, telegram_id, username, full_name, phone, reference_code,
       status, bot_blocked, approved_at
FROM users
WHERE telegram_id = ?;
```

Uses `ux_users__telegram_id`. p99 < 1ms.

### 10.2 Count pending receipts for a user

```sql
SELECT COUNT(*) FROM payment_receipts
WHERE user_id = ? AND status = 'pending';
```

Uses `ix_receipts__user_id_status` (covering — no row lookup needed).

### 10.3 Find duplicate receipt by perceptual hash

```sql
SELECT id, user_id, image_phash, status
FROM payment_receipts
WHERE status = 'approved'
  AND image_phash IS NOT NULL;
```

Then Hamming distance is computed in application code (population is small enough for a linear scan). When the corpus grows past ~10K rows, the upgrade path is described in §11.

**Alternative SQL-side computation** (for the curious; not used in v1):

```sql
SELECT id, BIT_COUNT(image_phash ^ ?) AS distance
FROM payment_receipts
WHERE status = 'approved'
HAVING distance <= 5;
```

This works but still scans every row — no index can accelerate Hamming distance without LSH.

### 10.4 Find user by free-text query (admin `/find`)

```sql
SELECT id, telegram_id, username, full_name, phone, reference_code, status
FROM users
WHERE phone = ?           -- exact
   OR username = ?        -- exact, with @ stripped
   OR reference_code = ?
LIMIT 10;
```

`UNION` of three indexed lookups, all sub-millisecond. The application calls this with the same string for all three params; the database picks the matching index per branch.

### 10.5 Get the active test

```sql
SELECT id, title, duration_seconds, published_at
FROM tests
WHERE status = 'active'
LIMIT 1;
```

Uses `ix_tests__status`. Selectivity is high (one row at most). Application caches the result in Redis for 60 seconds.

### 10.6 Load all questions of a test for rendering

```sql
SELECT id, position, section, question_text, option_a, option_b, option_c, option_d
FROM questions
WHERE test_id = ?
ORDER BY position;
```

Uses `ux_questions__test_position` for both filter and order. 50 rows fetched.

### 10.7 Reconstruct attempt state on resume / button tap

```sql
-- 1: attempt + denormalized timer info
SELECT id, user_id, test_id, status, current_position, started_at,
       expires_at, finished_at
FROM attempts
WHERE id = ? AND user_id = ?;

-- 2: all questions of the test (cached per test_id)
SELECT id, position, section, question_text, option_a, option_b, option_c, option_d
FROM questions WHERE test_id = ? ORDER BY position;

-- 3: all answers of this attempt
SELECT question_id, selected_option, is_correct
FROM answers WHERE attempt_id = ?;
```

Three queries; questions are cached in Redis per `test_id` (immutable for active tests), so in steady state only queries 1 and 3 hit MySQL. Total p99 < 5ms.

### 10.8 Submit / change an answer (idempotent upsert)

```sql
INSERT INTO answers (attempt_id, question_id, selected_option, is_correct, answered_at)
VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP(6))
ON DUPLICATE KEY UPDATE
  selected_option = VALUES(selected_option),
  is_correct      = VALUES(is_correct),
  updated_at      = CURRENT_TIMESTAMP(6);
```

Single-row write, uses `ux_answers__attempt_question`.

### 10.9 Finalize an attempt (score + persist)

```sql
-- Run inside a transaction
SELECT q.section, a.is_correct
FROM answers a
JOIN questions q ON q.id = a.question_id
WHERE a.attempt_id = ?;

UPDATE attempts
SET status = ?,                          -- 'submitted' or 'expired'
    finished_at = CURRENT_TIMESTAMP(6),
    score_total_correct      = ?,
    score_rus_tili_correct   = ?,
    score_pedagogik_correct  = ?,
    score_kasbiy_correct     = ?
WHERE id = ? AND status = 'in_progress';
```

The `AND status = 'in_progress'` guard makes the update idempotent — calling `finish()` twice updates zero rows the second time.

### 10.10 Leaderboard

```sql
SELECT a.user_id, u.full_name, a.score_total_correct, a.finished_at
FROM attempts a
JOIN users u ON u.id = a.user_id
WHERE a.test_id = ?
  AND a.status IN ('submitted', 'expired')
ORDER BY a.score_total_correct DESC, a.finished_at ASC
LIMIT 20;
```

Uses `ix_attempts__test_score`. The tiebreaker on `finished_at` does require a tiny filesort but it's bounded to the matching test_id slice — fast.

### 10.11 Per-question correctness analytics

```sql
SELECT q.position, q.section,
       COUNT(*) AS attempted,
       SUM(a.is_correct) AS correct,
       ROUND(SUM(a.is_correct) / COUNT(*) * 100, 1) AS pct_correct
FROM questions q
JOIN answers a ON a.question_id = q.id
WHERE q.test_id = ?
GROUP BY q.id, q.position, q.section
ORDER BY q.position;
```

Uses `ix_answers__question_is_correct`. Returns 50 rows for the admin to spot ambiguous questions.

### 10.12 Broadcast recipient list

```sql
SELECT id, telegram_id
FROM users
WHERE status = 'approved' AND bot_blocked = 0;
```

Uses `ix_users__status`. Result is paged in-application; broadcast iterates with throttling.

### 10.13 Pending-receipt reminder sweep

```sql
SELECT id, user_id, created_at
FROM payment_receipts
WHERE status = 'pending'
  AND created_at < ?  -- now - 24 hours
LIMIT 100;
```

Uses `ix_receipts__status_created`. Runs every hour from APScheduler.

### 10.14 Startup attempt reconciliation

```sql
SELECT id, user_id, started_at, expires_at,
       warning_10min_sent_at, warning_5min_sent_at, warning_1min_sent_at
FROM attempts
WHERE status = 'in_progress';
```

Uses `ix_attempts__status`. Runs once at bot startup; re-registers scheduler jobs.

### 10.15 Safety-net expired-attempt sweep

```sql
SELECT id
FROM attempts
WHERE status = 'in_progress'
  AND expires_at < ?;  -- now
```

Uses `ix_attempts__expires`. Runs every minute as a backstop in case any timer job was lost.

### 10.16 `/stats` global counters

```sql
SELECT
  (SELECT COUNT(*) FROM users)                                               AS total_users,
  (SELECT COUNT(*) FROM users WHERE status = 'approved')                     AS approved_users,
  (SELECT COUNT(*) FROM users WHERE status = 'pending_approval')             AS pending_users,
  (SELECT COUNT(*) FROM payment_receipts WHERE status = 'pending')           AS pending_receipts,
  (SELECT COUNT(*) FROM tests WHERE status = 'active')                       AS active_tests,
  (SELECT COUNT(*) FROM tests WHERE status = 'archived')                     AS archived_tests,
  (SELECT COUNT(*) FROM attempts WHERE status IN ('submitted','expired'))    AS finished_attempts,
  (SELECT COUNT(*) FROM attempts WHERE status = 'in_progress')               AS active_attempts;
```

All branches hit indexed columns or use COUNT on small tables. p95 < 50ms.

---

## 11. Performance Considerations

### 11.1 Expected scale (v1 18-month horizon)

| Entity | Volume |
|---|---|
| Users | 10,000 |
| Receipts | 15,000 |
| Tests | ~200 (≈2/week × 18 months) |
| Questions | 10,000 (200 tests × 50) |
| Attempts | 50,000 |
| Answers | 2,500,000 (50K attempts × 50 avg) |

All comfortable for a single MySQL 8 instance on modest hardware (4 vCPU, 8 GB RAM). The largest table (`answers`) at ~2.5M rows with proper indexes is well under the level where partitioning starts to matter.

### 11.2 Hot paths

- **Every Telegram update** → 1 indexed SELECT on users + the handler's own queries.
- **Test screen render** → 3 queries (attempt, questions, answers), with questions cached in Redis.
- **Answer submit** → 1 upsert + 1 UPDATE on attempts.current_position.

### 11.3 Connection pooling

SQLAlchemy async engine: `pool_size=10, max_overflow=5`. At our load this is plenty; we don't expect to ever saturate it.

### 11.4 When to revisit

| Trigger | Action |
|---|---|
| `answers` > 20M rows | Consider partitioning by `attempt_id` range |
| Receipt corpus > 10K | Implement LSH/BK-tree for image dedup (see below) |
| Broadcast > 5K recipients | Move broadcast out of bot process to dedicated worker |
| Slow query log starts firing | Investigate, add index, or rewrite |

### 11.5 The pHash scaling note

The naive "load all approved hashes, compute Hamming" approach scales to ~10K receipts before becoming a noticeable cost (a few hundred ms per submission). At that point, the standard upgrade path is **multi-index probing**:

1. Split each 64-bit hash into 4 × 16-bit chunks.
2. Index each chunk in its own column.
3. To find candidates within Hamming distance 5, query for rows matching *at least one* chunk exactly (pigeonhole principle: a hash differing in ≤5 bits across 64 bits has ≥3 chunks matching).
4. Compute exact Hamming on the candidate set only.

This is a schema change (4 new indexed columns) but not a rewrite. Doing it preemptively in v1 is premature optimization. Document, defer, do later.

---

## 12. Backup & Recovery

### 12.1 Backup strategy

- **`mysqldump` nightly**, full dump, gzipped, written to a separate volume on the same host.
- **Off-host rsync to S3 or similar**, daily, encrypted.
- **Retention:** 7 daily backups, 4 weekly, 6 monthly.
- **Point-in-time recovery:** enable binary log (`log_bin = ON`) so we can replay between full dumps. Bin logs retained for 7 days.

### 12.2 Recovery drills

Before declaring v1 done, perform a restoration drill:
1. Spin up a fresh MySQL instance.
2. Restore the latest full dump.
3. Replay binlogs to a specific point.
4. Verify counts on key tables match production.

If we don't drill, the backup might as well not exist.

### 12.3 What we explicitly accept losing

- **Redis FSM state.** Persisted with AOF, but a catastrophic Redis loss is recoverable: users restart their interactions, in-progress attempts continue from DB state via the reconciliation job (§10.14).
- **APScheduler jobstore.** Same as above. Lost jobs are re-registered on startup.

Everything in MySQL is treated as durable. Everything in Redis is treated as a cache (with one accepted exception: in-flight FSM state for ongoing flows, which is mildly unfortunate but not catastrophic to lose).

---

## 13. Initial Seed (Bootstrap)

After running `alembic upgrade head`, a fresh database has all 20+ settings rows but **zero admins**. The first admin must be inserted manually:

```sql
INSERT INTO admins (telegram_id, role) VALUES
    (<THE TEACHER'S TELEGRAM ID>, 'owner');
```

This is a one-time operation done by the developer at deploy time, documented in the README. After this, the owner can `/add_admin <telegram_id>` to add others.

A helper script `scripts/seed_admin.py` will be provided that takes a Telegram ID and inserts/updates the row. Idempotent.

---

## 14. Schema Evolution Going Forward

### 14.1 Migration discipline

- Every schema change goes through Alembic. No manual `ALTER TABLE` on production.
- Each migration's `downgrade()` is **implemented**, not just `pass`. We will need it eventually.
- Migrations are run by a **one-shot container**, never by the bot process at startup. Concurrent migrations from multiple replicas are a known footgun.

### 14.2 Backward-compatible changes (safe)

- Add nullable column
- Add new table
- Add new index (online, MySQL 8 supports it)
- Add new CHECK constraint (validates existing data — may fail if data violates)

### 14.3 Risky changes (require care)

- Drop column → first deploy app version that no longer reads it, then drop in a later migration
- Rename column → expand-contract: add new column, dual-write, backfill, switch reads, drop old (4 deploys)
- Change column type → write a real migration script; never assume the conversion is implicit

### 14.4 Expected near-term changes (post v1)

| Feature | Schema change |
|---|---|
| Per-question explanation visible to admin | Add `explanation TEXT NULL` to `questions` |
| Receipt photo retention | Add `photo_bytes_storage_path VARCHAR(500) NULL` to `payment_receipts` |
| Practice mode | New `practice_attempts` and `practice_answers` tables; could also reuse existing tables with a `kind` column |
| Subscription model | Add `access_expires_at DATETIME NULL` to `users`; logic flips from approval-flag to time-window |

---

## 15. Open Questions for Engineering

1. **Should `questions` be soft-deletable?** If a teacher uploads a typo'd question, today we'd republish (creating a new test and archiving the old). An alternative: edit-in-place + a `version` column on questions. **My recommendation:** keep questions immutable; if a fix is needed, republish. Simpler invariants.

2. **Should we store the user's IP and User-Agent?** Telegram doesn't expose them; we can't. Moot.

3. **Should `answers.is_correct` be a generated column instead of denormalized?** MySQL 8 supports it:

   ```sql
   is_correct TINYINT GENERATED ALWAYS AS
       (selected_option = (SELECT correct_option FROM questions WHERE id = question_id)) VIRTUAL
   ```

   But subqueries in generated columns are not allowed in MySQL. We'd have to JOIN at query time. The denormalized approach is correct.

4. **Why no `audit_log` table?** Discussed in Architecture Spec §1 of "out of scope." Per-row admin attribution (`reviewed_by_admin_id`, `created_by_admin_id`, `updated_by_admin_id`) plus structured logs covers our v1 accountability needs. A dedicated audit table is a v1.1 add.

---

## 16. Acceptance Criteria for v1

DB-side checks before declaring v1 done:

- [ ] Initial migration runs cleanly against a fresh MySQL 8.4 instance
- [ ] All seed settings rows are present after migration
- [ ] All FKs and CHECK constraints listed in §6 are present (verify with `SHOW CREATE TABLE`)
- [ ] All indexes listed in §5 are present
- [ ] `EXPLAIN` on every query in §10 shows index usage (no full table scans on >1K-row tables)
- [ ] Backup script exists, runs in cron, produces a restorable dump
- [ ] At least one full restoration drill has been performed against a non-prod instance
- [ ] Slow query log is enabled with `long_query_time = 0.5`
- [ ] First admin seed script (`scripts/seed_admin.py`) is tested
- [ ] Schema diagram (this document's §4 ASCII) is up to date with the actual deployed schema

---

## 17. Quick-Reference Schema Card

For developers building against this schema, here's the one-page version:

```
users(id, telegram_id*, username, full_name, phone, reference_code*, status,
      bot_blocked, created_at, updated_at, approved_at)
    * = unique

admins(id, telegram_id*, user_id→users, role, added_by_admin_id→admins, added_at)

payment_receipts(id, user_id→users, telegram_file_id, telegram_file_unique_id,
                 image_phash, status, rejection_reason,
                 reviewed_by_admin_id→admins, admin_notification_message_id,
                 created_at, reviewed_at)

tests(id, title, status, duration_seconds, created_by_admin_id→admins,
      created_at, published_at, archived_at)

questions(id, test_id→tests*, section, position*, question_text,
          option_a, option_b, option_c, option_d, correct_option, created_at)
    * = unique together

attempts(id, user_id→users*, test_id→tests*, status, current_position,
         started_at, finished_at, expires_at,
         score_total_correct, score_rus_tili_correct,
         score_pedagogik_correct, score_kasbiy_correct,
         warning_10min_sent_at, warning_5min_sent_at, warning_1min_sent_at)
    * = unique together

answers(id, attempt_id→attempts*, question_id→questions*, selected_option,
        is_correct, answered_at, updated_at)
    * = unique together

settings(key, value, description, updated_by_admin_id→admins, updated_at)
```

---

## End of Phase 3

With this document plus `PRODUCT_BLUEPRINT.md` and `ARCHITECTURE_SPEC.md`, the build phase has everything it needs:

- **Product**: every user-facing rule, message, and flow
- **Architecture**: every module, layer, and runtime concern
- **Database**: every table, column, type, index, constraint, and seed

You can now type `make build` (figuratively) and start writing application code without re-asking design questions.
