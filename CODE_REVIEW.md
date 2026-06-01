# Code Review — Russian Attestation Bot

**Reviewer perspective:** senior backend engineer + product architect
**Date:** 2026-05-24
**Scope:** Full codebase under `/app`, jobs, models, middlewares, alembic, deployment.
**Method:** Four parallel deep-dive audits across (1) test-taking, (2) onboarding/payment, (3) admin flows, (4) infrastructure & data layer. Each finding cites file:line and was verified against the actual code, not the spec.

> **Reading guide.** Severity ladder: 🔴 Critical → 🟠 High → 🟡 Medium → 🟢 Low → ⚪ Nit. "Critical" = data-loss, security, or stuck-user. "High" = real money flowing through it, you'd notice the bug in a week of production. The full list is ~60 items; the first 15 are what actually matter for a demo.

---

## TL;DR — what's the actual state of this codebase

**It's good.** Architecture is clean, layering is honest, spec is followed, tests exist, naming is consistent. The author treated CLAUDE.md as a contract and you can feel it.

**But it's not ready to take a paying student yet.** There are three classes of problem you need to fix before launch:

1. **Authorization holes** — the receipt approval buttons in the admin group aren't actually checking who's tapping them (C1). One person added to the admin group as an observer can approve/reject anyone's receipt.
2. **Race conditions around money** — the "max 3 pending receipts" rule isn't enforced under concurrent submissions (C6); approving a banned user silently un-bans them (C2); two admins publishing simultaneously can leave the system with two active tests at once (C8).
3. **Stuck-user scenarios** — if a timer job is lost (Redis flush, edge case), the student's attempt sits `in_progress` forever and the unique-constraint blocks them from ever taking that test again, even after a re-publish (C4). No backstop sweep is running.

The first two are 1–10 line fixes. The third needs a 30-line cron job.

Below: the full findings, ranked.

---

## 🔴 CRITICAL

### C1 — Receipt approve/reject buttons not gated by `AdminOnly` filter

**Files:** `app/bot/bot.py:69-72` (router registration), `app/bot/handlers/admin/receipts.py:69-77, 127-134`

The admin receipts router filters only by `chat.id == admin_group_id`. Every other admin router (`tests.py`, `settings.py`, `operations.py`, `panel.py`) adds `AdminOnly()` explicitly — this one doesn't. `on_approve` does a defensive `services.admin.get_by_telegram_id(...)` and falls back with a misleading "уже обработан" message; `on_reject_init` has **no admin check at all** — a non-admin tap opens the rejection FSM, the bot replies "Укажите причину отказа..." in the group.

**Impact:** Anyone the teacher adds to the admin group as an observer can approve receipts. The entire `admins` table is bypassed.

**Fix (literally one line each):**
```python
admin_receipts.router.callback_query.filter(F.message.chat.id == admin_group_id, AdminOnly())
admin_receipts.router.message.filter(F.chat.id == admin_group_id, AdminOnly())
```

---

### C2 — Approving a receipt silently un-bans a banned user

**Files:** `app/services/receipt_service.py:155`, `app/repositories/user_repository.py:55-70`

`mark_approved` runs `UPDATE users SET status='approved' WHERE id = ?` — no status guard. Scenario: user submits receipt → admin bans them → admin (or a colleague) hits ✅ on the original pending notification. The ban is wiped, no log warning.

**Impact:** `/ban` is not durable. Trust in moderation is broken.

**Fix:** Add `WHERE id = ? AND status <> 'banned'` and treat rowcount=0 as the same "stale receipt" error.

---

### C3 — Orphaned in-progress attempts when a test is archived mid-flight

**Files:** `app/services/test_service.py:142-153` (publish), `app/bot/handlers/test_taking.py:333-381` (`_enter_test_flow`)

PRODUCT_BLUEPRINT §13 promises: "User's attempt is in progress when test gets archived → their attempt continues to completion on the archived test." Code doesn't honor this. `_enter_test_flow` looks up `services.attempt.get_user_attempt_for_test(user.id, active_test.id)` — keyed to the **currently active** test. The in-flight attempt on the just-archived test is invisible everywhere; the user can start a second in-progress attempt on the new active test. Meanwhile the old attempt's scheduled jobs keep ticking and DM the user "10/5/1 min left" + an auto-submit result screen for a test they never finished.

**Impact:** Two result screens appear for the same student (a different one for each test). Leaderboards on archived tests get polluted with auto-expired attempts.

**Fix:** In `TestService.publish`, finalize all `in_progress` attempts on the test being archived (`status='expired'`, `finished_at=now()`, scores from current answers) inside the same transaction. Cancel their scheduled jobs.

---

### C4 — No recurring expired-attempt safety-net sweep — lost timer = permanently bricked user

**Files:** `app/repositories/attempt_repository.py:130-136` (`list_expired_in_progress` exists), `app/jobs/registry.py:108-123` (only schedules pending-receipt reminder)

ARCHITECTURE_SPEC §10.15 and §11 require a periodic sweep that catches `in_progress` attempts past `expires_at` as a backstop for lost timer jobs. The repo method exists but is **never called outside startup reconciliation**. After the bot has been running for an hour, the only insurance against lost jobs is APScheduler's Redis jobstore — which can vanish on a Redis flush, eviction, or write failure.

**Impact:** Once an attempt's timer job is lost, the row sits `in_progress` forever. The `ux_attempts__user_test` unique constraint then blocks that user from ever attempting this test again — even after the teacher republishes a fixed version. The user is bricked with no recovery path.

**Fix:** Register a cron job in `app/jobs/registry.py` (every minute) that calls `attempt_expire_job` for each id returned by `list_expired_in_progress(now_utc())`. The job is already idempotent.

---

### C5 — Webhook startup race: HTTP site accepts updates before scheduler starts

**File:** `app/main.py:154-170`

Order in `_run_webhook` is: `set_webhook → make_app → site.start → _start_jobs`. Between `site.start()` and `_start_jobs()`, Telegram updates land in the dispatcher and `AttemptService.start` calls `schedule_attempt_jobs` against a scheduler that hasn't been `.start()`-ed yet. (`_run_polling` does the reverse — inconsistent.)

**Impact:** A student tapping "Начать тест" during this window can end up with an attempt that has no scheduled timer. Without C4 above, they're permanently stuck.

**Fix:** Reorder to `_start_jobs → site.start → set_webhook` (set_webhook last — that's when Telegram starts delivering).

---

### C6 — TOCTOU race on the 3-pending-receipts cap (anti-abuse defeated)

**File:** `app/services/receipt_service.py:90-92`

```python
pending_count = await self._receipts.count_pending_for_user(user.id)
if pending_count >= self._max_pending:
    raise ReceiptLimitExceededError()
...
await self._receipts.create(...)
```

Count and insert are separate statements, no row lock, no DB constraint. A user firing 4 photos in rapid succession (or sending a media-group — see also M3) blows past the cap. InnoDB's REPEATABLE READ won't serialize them.

**Impact:** A determined user can flood the admin group with receipts. Anti-abuse rule from PRODUCT_BLUEPRINT §8.2 & §14.1.4 not actually enforced.

**Fix:** Cheapest — wrap the submit in a per-user Redis advisory lock. Cleaner — `SELECT … FOR UPDATE` on the user row before count + insert in a single transaction. Best — denormalize a `pending_receipt_count INT NOT NULL CHECK (pending_receipt_count <= 3)` on `users` and update atomically with status transitions.

---

### C7 — Warning-slot claim non-atomic: warnings fire on already-finished attempts

**Files:** `app/services/attempt_service.py:340-365` (`claim_warning_slot`), `app/repositories/attempt_repository.py:110-123` (`mark_warning_sent`)

```python
attempt = await self._attempts.get_by_id(attempt_id)
if attempt is None or attempt.status != "in_progress":
    return None
rowcount = await self._attempts.mark_warning_sent(attempt_id, slot)
```

The status check is in Python; the UPDATE has no status guard. Between SELECT and UPDATE, the user can manually finish or the expire job can fire — the UPDATE then stamps `warning_X_sent_at` on a finalized attempt, and the job proceeds to DM "Осталось 10 минут!" after the student has already seen their result.

**Impact:** User sees a "10 minutes left" warning after their test is already finished. Confusing; erodes trust.

**Fix:** Add `AND status='in_progress'` to the `mark_warning_sent` WHERE clause, drop the pre-SELECT, treat rowcount=0 as "already finalized, skip send".

---

### C8 — Two concurrent test publishes can leave the system with two active tests

**Files:** `app/services/test_service.py:125-165`, `app/models/test.py` (no DB-side uniqueness)

DATABASE_SPEC §5.4 explicitly notes the "one active test" invariant is enforced by application code only. The transaction in `publish` does `archive prior → activate new`, but two admins publishing different drafts simultaneously can both see "no active test" or both see the same prior, and both end up activating. There's no DB unique partial index on `status='active'`.

**Impact:** Two active tests means the broadcast (and the student "Пройти тест" lookup) get inconsistent results. Hard to debug afterwards.

**Fix:** Either (a) take a row-level lock on a synthetic "active-test slot" row before publish, (b) add the generated-column trick from DB spec §5.4, or (c) wrap publish in an explicit transactional `SELECT … FOR UPDATE` on every `tests` row.

---

## 🟠 HIGH

### H1 — `submit_answer` writes to attempts that just got finalized (silent score drop)

**Files:** `app/services/attempt_service.py:226-262`, `app/repositories/answer_repository.py:46-65`

Read attempt → check status → upsert answer. No status guard on the upsert. If the expire job (or user finish) fires between the read and the upsert, the answer lands on a now-finalized attempt; `mark_finished` won't re-score (status-guarded — correctly), so the answer is silently excluded from the score the student saw.

**Impact:** Near-the-buzzer answers get "accepted" UI-wise (green checkmark on next render via the FSM state) but don't count. Student disputes score, no way to explain.

**Fix:** `SELECT … FOR UPDATE` on the attempt row at the top of `submit_answer`, verify status under the lock. Or status-guard the upsert.

---

### H2 — Job-vs-handler race: user gets two result screens

**Files:** `app/jobs/attempt_timer.py:117-186`, `app/services/attempt_service.py:281-336`

`attempt_expire_job` snapshots `attempt.status` (SELECT #1), then calls `finish` (internal SELECT #2 + UPDATE). If a manual-finish lands between #1 and `finish`'s UPDATE, the "already_finished" flag is False, UPDATE rowcount is 0 (no-op), and the job DMs the user "⏰ Время вышло. Тест автоматически завершён." plus a full result screen — moments after they tapped Finish and saw their own result.

**Impact:** Confusing duplicate result messages. Student thinks their submit didn't register.

**Fix:** `mark_finished` returns rowcount; thread it back through `finish` as "we_owned_the_write"; only DM when True.

---

### H3 — Contact not validated against sender

**File:** `app/bot/handlers/onboarding.py:69-74`

Telegram lets users share anyone's contact card. The code doesn't compare `contact.user_id` against `message.from_user.id`. PRODUCT_BLUEPRINT §8.1 explicitly says to log a warning when they differ.

**Impact:** Phone numbers in DB don't reliably belong to the user. `/find` by phone returns wrong rows. Defeats the soft uniqueness check.

**Fix:**
```python
if contact.user_id and contact.user_id != message.from_user.id:
    logger.warning("contact_user_mismatch", contact_user_id=contact.user_id, sender_id=message.from_user.id)
```

---

### H4 — `/start` is not chat-type-filtered — card number leaks into groups

**File:** `app/bot/handlers/common.py:63-70`

`@router.message(CommandStart())` matches any chat. If anyone types `/start` in a group the bot is in, the bot replies with the welcome message + (after onboarding) the payment screen including bank card number and recipient name.

**Impact:** Bank credentials leak into random group chats. Security + privacy issue.

**Fix:** Router-level filter `router.message.filter(F.chat.type == "private")` for the common router (and move `/chatid` to a separate router or flag-skip).

---

### H5 — `cmd_start` clears FSM even when student is mid-test

**File:** `app/bot/handlers/common.py:111-118`

PRODUCT_BLUEPRINT §13 says `/start` should resume current state, not reset. Code unconditionally `state.clear()`s for approved users. A student mid-test who reflexively types `/start` loses their FSM context (DB attempt survives, but until they hit "Пройти тест" again the screen is gone).

**Fix:** Check `await state.get_state()`; if it's `TestState.in_progress`, re-render the test screen instead of the menu.

---

### H6 — Re-onboarding callback mutates approved users' identity columns

**Files:** `app/bot/handlers/onboarding.py:37-54`, `app/services/user_service.py:112-126` (`attach_reference_code`)

`on_start_onboarding` always sets FSM to `waiting_for_phone` — even for already-approved users. Then `set_phone` and `set_name` write at the repo layer **before** checking the user's current status. `attach_reference_code` writes the new code **before** the status guard. Net effect: an approved user scrolling up and tapping "Начать ▶️" has their `phone`, `full_name`, and **reference_code** silently rewritten. The teacher's audit trail of "code X corresponds to bank deposit Y" breaks.

**Fix:**
1. Gate `on_start_onboarding` on `user.status == "new"` and send "Вы уже начали процесс" otherwise.
2. Move status check **above** all writes in `attach_reference_code`, `set_phone`, `set_name`.

---

### H7 — Approve callback's `callback.answer()` is at the END (button spins for seconds)

**File:** `app/bot/handlers/admin/receipts.py:121` (line 121, after all the work)

aiogram best practice (and ARCHITECTURE_SPEC §4.4): call `callback_query.answer()` early. Currently the approve handler does ~6 round-trips (DB SELECTs/UPDATEs, message edit, settings reads, user DM) before answering. On bad networks the admin sees a spinning button, double-taps, and confuses themselves with the "already processed" message.

**Fix:** Move `await callback.answer()` to the first line.

---

### H8 — Image download has no size guard or decode-error handling

**File:** `app/bot/handlers/payment.py:81-83`

```python
buf = BytesIO()
await container.bot.download(photo.file_id, destination=buf)
photo_bytes = buf.getvalue()
```

No `file_size` check before download. If `ImageHasher.hash` raises `ValueError` on a malformed image, the exception falls through to the global error handler → user gets generic "try later" instead of actionable feedback.

**Fix:**
```python
if photo.file_size and photo.file_size > 5 * 1024 * 1024:
    await message.answer("Файл слишком большой. Максимум 5 МБ.")
    return
try:
    await container.bot.download(...)
except TelegramAPIError:
    await message.answer("Не удалось скачать чек. Попробуйте ещё раз.")
    return
```

---

### H9 — Webhook awaits `feed_update` inline — slow handler blocks Telegram

**File:** `app/bot/webhook.py:44`

`await dispatcher.feed_update(...)` blocks until commit. Under DB pool exhaustion or a slow handler, Telegram waits, eventually retries or escalates. Spec promises p95 < 500ms but doesn't actually defend against it.

**Fix:** `asyncio.create_task(dispatcher.feed_update(...))` then return 200 immediately. (Watch the per-user ordering trade-off — for this bot, order rarely matters, but think about back-to-back rapid taps.)

---

### H10 — Throttle middleware fails CLOSED when Redis is down

**File:** `app/bot/middlewares/throttle.py:43-47`

`redis.incr` raises `ConnectionError` if Redis is unreachable. The exception escapes the middleware → global error handler → every user sees "Произошла ошибка." until Redis comes back.

**Fix:** Wrap `incr` + `expire` in `try/except RedisError`; log once and fall through (accept brief loss of rate-limit protection).

---

### H11 — `aiohttp` web app uses default 1MB body limit

**File:** `app/bot/webhook.py:63` (`make_app`)

nginx allows 20MB. aiohttp defaults to 1MB, so large updates (media groups, photo callbacks) get 413'd.

**Fix:**
```python
app = web.Application(client_max_size=20 * 1024 * 1024)
```

---

### H12 — No `engine.dispose()` / `redis.aclose()` on shutdown

**File:** `app/main.py:138-182`

SIGTERM closes the bot session and scheduler but never disposes the SQLAlchemy engine or closes the Redis pool. In containerized prod with quick rolling restarts, MySQL's `max_connections` budget leaks.

**Fix:** `finally:` blocks in both runners — `await container.engine.dispose()` and `await container.redis.aclose()`.

---

### H13 — `attach_reference_code` and `mark_approved` overwrite without status guards

**File:** `app/services/user_service.py:84-126`

(Already covered in H6, but also bears on direct admin/operations paths.) None of the user-mutation services treat status as a precondition; they all write first, then sometimes check. This is the wrong direction for invariants.

**Fix:** Adopt a uniform rule: in services, status check first, write second. In repos, add `WHERE status IN (...)` clauses where applicable, treat rowcount=0 as a known error path.

---

### H14 — Broadcast crash-mid-flight has no resume / dedup

**File:** `app/services/notification_service.py:74-148`

`broadcast_new_test` fans out via `asyncio.gather`. If the bot crashes after sending to 500/1000 students, on restart there's no way to know who got it. Next deploy = no replay.

**Fix:** Persist broadcast progress (`broadcasts` table or `tests.broadcast_offset`). On restart, resume from offset. Acceptable trade-off: some users receive duplicate notifications on crash recovery — that's better than silent partial delivery.

---

### H15 — `TelegramRetryAfter` retry-once-then-drop

**File:** `app/services/notification_service.py:101-128`

Spec says retry once on RetryAfter. Code does. But if the retry **also** RetryAfters (Telegram is aggressively throttling), the message is logged and dropped. A burst of 429s during broadcast = lost notifications for many students.

**Fix:** Exponential backoff with cap (e.g. 3 attempts, sleep 5/15/45s). Or chain into the broadcast-progress mechanism so the next sweep retries.

---

### H16 — Excel upload: file_size check happens AFTER download begins

**File:** `app/bot/handlers/admin/tests.py:117-123`

Telegram's `file_size` is sometimes None for documents. Code only enforces the 5MB cap when `file_size` is present. A malicious `.xlsx` (zip-bomb) downloaded into memory before openpyxl chokes can DoS the bot.

**Fix:** Bail if `file_size is None or file_size > 5MB`. openpyxl supports `read_only=True` — use it (cuts memory + time).

---

### H17 — `/preview` and payment screen `.format()` crash on bad placeholder

**Files:** `app/bot/handlers/admin/settings.py:163-263`, `app/bot/views/payment_screen.py:30-35`

Admin types `/set payment_instructions "...{whoops}..."` → every onboarding user from then on hits `KeyError("whoops")` → "Произошла ошибка". No defense.

**Fix:** Use `string.Template.safe_substitute` or `format_map(_SafeDict())`. The `_safe_format` helper exists for settings.py but isn't used in payment_screen.

---

### H18 — `/find <phone>` does exact string match — no normalization

**Files:** `app/bot/handlers/admin/operations.py:77-100`, `app/services/user_service.py:84-98`

Telegram delivers `phone_number` without `+`. Admin types `+998901234567` → no match. Admin types `998901234567` → match. Fragile.

**Fix:** Normalize on write AND on query — strip non-digits, prepend `998` if length < 12, store/query as canonical E.164 without `+`.

---

### H19 — Approve/reject DMs not HTML-escaped under HTML parse mode

**File:** `app/bot/handlers/admin/receipts.py:107, 203`

Bot is configured with `parse_mode=HTML` globally. Reason text and invite link are interpolated raw via `.format(reason=text)`. If the admin types `<b>` or `&` in the reason, Telegram returns 400 → DM fails → only logged → student never hears the rejection.

**Fix:** `html_escape(text)` and `html_escape(link)` before substitution.

---

### H20 — Banning a user mid-attempt doesn't clean up

**File:** `app/services/user_service.py:155-175` (`ban`)

Sets status to `banned`. The user's `in_progress` attempt is not finalized; its scheduled jobs keep firing and DMing the banned user warning messages. `UserLoaderMiddleware` short-circuits incoming messages, but **outbound DMs from scheduled jobs don't check the ban**.

**Fix:** In `ban()`, find all `in_progress` attempts for the user, finalize them (status='expired'), cancel jobs. Optionally: have the timer job re-check the user's ban status before DMing.

---

## 🟡 MEDIUM

### M1 — Authorization fallthrough: `IntegrityError` from parallel `start_test` taps surfaces as "Произошла ошибка"

**Files:** `app/services/attempt_service.py:106-140`

Two simultaneous "Начать" taps both pass the `get_by_user_and_test` check, both reach INSERT; the second fails on `ux_attempts__user_test`. Not caught → global handler → generic error instead of friendly "Вы уже проходили".

**Fix:** Catch `IntegrityError`, re-read existing attempt, raise `AttemptAlreadyExistsError`.

---

### M2 — Name validator accepts emoji-only / RTL marks / control characters

**File:** `app/services/user_service.py:182-186`

`len()` counts code points; `any(ch.isalpha())` only requires one letter anywhere. 79 emoji + 1 letter passes. RTL override `‮` passes — could make admin captions render right-to-left.

**Fix:** Reject any Unicode category starting with `C` (control/format) or `Z`. NFC-normalize before storing.

---

### M3 — Media-group submission bypasses pending-receipt cap

**File:** `app/bot/handlers/payment.py:56-95`

User sends 4 photos in one message (media_group). Each arrives as a separate update; handler doesn't dedup by `media_group_id`. Combined with C6 (TOCTOU on cap), a fresh user can submit 4+ receipts at once.

**Fix:** `if message.media_group_id: dedup via Redis SETNX media_group:{id}` with 60s TTL.

---

### M4 — `_show_finished` skips the ownership check (defense-in-depth gap)

**File:** `app/bot/handlers/test_taking.py:412-435`

Calls `get_attempt(attempt_id)` directly — no `user_id=` filter. Current callers route through `get_state` first (which does check), so it's safe today. But it's a footgun for future maintainers.

**Fix:** Always pass `user_id` and use `get_state` inside `_show_finished`.

---

### M5 — `get_state` raises `SystemError` on ownership mismatch (Sentry noise + bad UX)

**File:** `app/services/attempt_service.py:194-222`

Treats "this user doesn't own this attempt" as a system bug. Sentry gets noise from normal user behavior (stale buttons from earlier sessions); user sees generic error.

**Fix:** Define `AttemptNotVisibleError(UserError)` with a Russian `user_message`; raise that instead.

---

### M6 — `set_current_position` UPDATE has no status guard

**File:** `app/services/attempt_service.py:264-277`

Same TOCTOU as H1 but for cursor. Lower impact (cursor is UX, not score), but produces audit anomalies: `current_position` written to an `expired` attempt with `finished_at` set.

**Fix:** Add `AND status='in_progress'` to the UPDATE WHERE clause.

---

### M7 — `mark_bot_blocked` only flipped during broadcast, not on warning/approve DMs

**Files:** `app/services/notification_service.py:186-193`, `app/bot/handlers/admin/receipts.py:108-119`, `app/jobs/attempt_timer.py:154-184`

Every `send_message` that catches `TelegramForbiddenError` just logs. Approved students who blocked the bot stay `bot_blocked=False`, get included in the next broadcast, only get flagged on that next failure.

**Fix:** Centralize the "send DM; flip bot_blocked on Forbidden" pattern in `NotificationService.send_user_message`.

---

### M8 — Phone uniqueness "soft" check missing entirely

**File:** Searched in `app/services/receipt_service.py:73-133`, `app/services/user_service.py:84-98` — not found

PRODUCT_BLUEPRINT §14.2 says: "if a phone number is already attached to a different *approved* user, flag the new submission in the admin notification." Not implemented.

**Fix:** Add `UserRepository.find_approved_by_phone(phone, exclude_user_id)`. Call from `ReceiptService.submit`, append `"⚠️ Этот телефон уже привязан к одобренному пользователю."` to warning list.

---

### M9 — Cross-user pending duplicate-receipt collisions not flagged

**File:** `app/services/receipt_service.py:96-101`

Only scans against the same user's pending queue and against approved (any user). Two collaborators submitting the same screenshot simultaneously — both go through with no warning.

**Fix:** Extend scan to include pending-from-other-users with a different warning.

---

### M10 — Rejected receipts not scanned for pHash duplicates

**File:** `app/services/receipt_service.py:96-111`

Fraudster pattern: submit fake → get rejected → submit tweaked version. Original fake's hash is in DB (`status='rejected'`) but never compared against.

**Fix:** Extend the duplicate scan to include `rejected` status, with `"⚠️ Похожий чек был ранее отклонён"`.

---

### M11 — `is_correct` denormalization → answer-time correctness can desync with score

**Files:** `app/services/attempt_service.py:254-262`, `app/services/scoring_service.py:31-60`

If `questions.correct_option` is edited (out of spec policy, but possible), old answers' `is_correct` is stale until the user re-taps the question. Mixed-state at finish time.

**Fix:** Either DB-trigger reject `UPDATE` on `correct_option` when any answer references it, or recompute `is_correct` at scoring time via JOIN. DATABASE_SPEC §15.3 already contemplates the latter.

---

### M12 — Started-at vs expires-at clock skew

**Files:** `app/models/attempt.py:58-64`, `app/services/attempt_service.py:122-128`

`started_at` is a DB server default (`CURRENT_TIMESTAMP(6)`), `expires_at = now_utc() + 3200s` from the Python side. If MySQL and the bot host clocks drift, the first render's timer is wrong by N seconds.

**Fix:** Compute both from Python's `now_utc()` and INSERT both explicitly.

---

### M13 — Background broadcast tasks not awaited on shutdown

**File:** `app/bot/handlers/admin/tests.py:43, 251-293`

`_BACKGROUND_TASKS` module-global. On shutdown, no `gather` over it. In-flight broadcasts get cancelled abruptly.

**Fix:** Track tasks in container; await on shutdown with a timeout.

---

### M14 — Throttle is innermost → banned users still cost a DB SELECT per spam message

**File:** `app/bot/bot.py:59-62`

Order is `Logging → DbSession → UserLoader → Throttle`. Banned users go through 2 layers of plumbing before being dropped.

**Fix:** Add a Redis-cached banned-id check at the very front of `UserLoaderMiddleware`.

---

### M15 — `pending_receipt_reminder_job` holds DB connection during Telegram sends

**File:** `app/jobs/pending_receipt_reminder.py:54-87`

Reads receipts and sends admin-group messages inside `async with session_factory()`. A slow Telegram day with 100 pending receipts = pool slot held for many seconds.

**Fix:** Read receipts into a list, exit session, iterate sends. Redis marker still dedupes.

---

### M16 — `/unban` blindly sets status to `approved`

**File:** `app/services/user_service.py:155-175`

Unbanning a user who never paid grants them approved status. Audit trail wrong.

**Fix:** Store `pre_ban_status` on ban; restore it on unban. Or refuse to unban if the user was never approved before being banned.

---

### M17 — `attempt_expire_job` DMs after commit — silent failure leaves student unaware

**File:** `app/jobs/attempt_timer.py:161-184`

DB transaction commits → row is finalized. Then bot DMs the user. If Telegram returns 5xx or bot is down for 30s, the DM is lost; only an admin log line exists. Student's last view is the test screen with stale timer.

**Fix:** Retry the send with exponential backoff. Or persist a `result_dm_sent_at` column and have a backup job that resends if it's NULL N minutes after `finished_at`.

---

### M18 — Bot restart can lose start-of-attempt jobs (split transaction)

**File:** `app/services/attempt_service.py:106-140`

`INSERT attempt → scheduler.add_job → handler returns → middleware commits`. Jobs are written to Redis BEFORE the DB transaction commits. If the handler raises after `add_job` but before commit, Redis has ghost jobs for a non-existent attempt. Inverse: if Redis is briefly down during `add_job` but DB commits, attempt has no jobs.

**Fix:** Schedule jobs from a SQLAlchemy `after_commit` event listener.

---

### M19 — Rejection-reason: no length cap, no HTML escape

**File:** `app/bot/handlers/admin/receipts.py:201-205`

Admin's rejection-reason text. If > 500 chars, DB raises `DataError` at flush → rollback → receipt stays pending; admin sees "OK" echo. If contains HTML chars, DM to user fails (see H19).

**Fix:** Truncate to 500 in handler; escape before DM substitution.

---

### M20 — Settings cache invalidation timing

**File:** `app/services/settings_service.py:48-51`

DB write + Redis cache delete happen, but DB commit is later (via middleware). Between cache delete and DB commit, another request reads → cache miss → reads pre-commit DB value → re-caches stale.

**Fix:** Invalidate cache from a SQLAlchemy `after_commit` listener, not synchronously with the UPDATE.

---

### M21 — `Services.admin` is a Repository, leaked through to handlers

**File:** `app/core/container.py:53`

Handlers do `services.admin.get_by_telegram_id(...)` — repo call from handler layer. Documented carve-out, but violates ARCHITECTURE_SPEC §4.1.

**Fix:** Wrap in a tiny `AdminService` (even just delegation) to preserve the layering invariant.

---

### M22 — APScheduler `coalesce + misfire_grace=300s` silently drops late expiry jobs

**File:** `app/core/scheduler.py:39-46`

If the bot is down >5 min past an attempt's `expires_at`, the scheduler drops the job. Reconciliation runs at startup (catches it). But if the bot is *up* and the event loop is blocked >5 min, the job dies silently — and there's no recurring sweep (see C4).

**Fix:** Solved by C4's safety-net sweep.

---

### M23 — Hamming threshold `5` hardcoded, not editable via settings

**File:** `app/services/image_hasher.py:20`

PRODUCT_BLUEPRINT §15.4 says "all numbers configurable via settings". Threshold isn't.

**Fix:** Read from `SettingsService.get_int("phash_hamming_threshold", default=5)`.

---

### M24 — `Sentry` scrubber matches keys by exact name; `user_phone` slips through

**File:** `app/core/sentry.py:17-26`

`_SENSITIVE_KEYS` contains `phone` exactly. `user_phone`, `from_phone`, `phone_number` are not scrubbed. Today no code logs these; latent.

**Fix:** Substring match: `if any(s in key.lower() for s in _SENSITIVE_KEYS)`.

---

### M25 — `structlog` scrubber doesn't recurse into nested dicts

**File:** `app/core/logging.py:84-89`

Top-level keys only. If anyone logs `update=update.model_dump()` and the update contains a `contact.phone_number`, it's not scrubbed.

**Fix:** Recursive walk like Sentry's `_scrub_mapping`.

---

## 🟢 LOW

### L1 — `cmd_chatid` is unauthenticated (anyone can leak the bot's chat IDs)

**File:** `app/bot/handlers/common.py:166-182`

Group IDs aren't secret per se but the command is dev-only.

**Fix:** Admin-only after initial deploy or remove.

---

### L2 — `bot_blocked` never cleared when a user unblocks

**File:** `app/repositories/user_repository.py:72-75`

Once flagged, the user is permanently excluded from broadcasts. Spec implies "normal flow resumes" but it's only true for handlers.

**Fix:** In `UserLoaderMiddleware`, when loading a user with `bot_blocked=True` and successfully receiving from them, clear the flag.

---

### L3 — `ReferenceCodeGenerationError` is unhandled at the handler

**File:** `app/bot/handlers/onboarding.py:104-105`, `app/services/reference_code.py:24, 49-51`

Astronomically unlikely (~31^6 codes), but if 5 collisions occur, user falls to global error handler with no recovery.

**Fix:** Catch + log + send "Технические работы".

---

### L4 — Empty phone allowed

**File:** `app/services/user_service.py:84-98`

Telegram Contact with empty phone_number string is accepted.

**Fix:** Reject empty after strip.

---

### L5 — Approve-DM-Forbidden silent path

**File:** `app/bot/handlers/admin/receipts.py:108-119`

User blocked the bot before approval; DM fails; admin sees "✅ Одобрено" with no warning. User never knows.

**Fix:** Catch `TelegramForbiddenError` specifically; warn in admin group: "Одобрено, но пользователь заблокировал бота".

---

### L6 — Admin username not HTML-escaped in resolution caption

**File:** `app/bot/handlers/admin/receipts.py:51-63`

Telegram usernames are constrained to `[A-Za-z0-9_]`, so safe in practice. Theoretical XSS if Telegram ever loosens.

**Fix:** `html_escape()` — defensive.

---

### L7 — `ReceiptService` constructs its own `UserService` (DI shortcut)

**File:** `app/services/receipt_service.py:69`

Breaks future ability to decorate `UserService` (caching, audit) — `ReceiptService` bypasses.

**Fix:** Inject `UserService` via container.

---

### L8 — `Services` re-instantiates `ExcelParser` / `ImageHasher` per request

**File:** `app/core/container.py:88, 99, 112`

Both stateless. Cheap to build but pointless.

**Fix:** Hoist to `Container` fields.

---

### L9 — `webhook_handler` returns 200 even when `feed_update` raised

**File:** `app/bot/webhook.py:44-45`

Errors are caught by aiogram's error handler, but defensive try/except would be better.

**Fix:** Wrap `feed_update` in try/except, log, return 200.

---

### L10 — `DbSessionMiddleware` doesn't roll back if `commit()` itself raises

**File:** `app/bot/middlewares/db_session.py:31-38`

`commit()` in the `else:` branch. If it raises, no explicit rollback — relies on `async with` close.

**Fix:** Wrap commit in try/except → rollback → re-raise.

---

### L11 — `_run_webhook` requires `webhook_url` even in dev (polling mode)

**File:** `app/core/config.py:25`

Polling never uses the webhook URL; mandatory env var = friction.

**Fix:** Make optional; validate only when `env != "dev"`.

---

### L12 — No CI test that `static/template.xlsx` matches parser expectations

**Files:** `scripts/generate_template.py`, `app/services/excel_parser.py`

Drift between `_HEADERS` and `_REQUIRED_COLUMN_LABELS` is silent.

**Fix:** Add `tests/unit/services/test_template.py` that loads the bundled file and asserts parser accepts it.

---

### L13 — `Settings.db_url` materializes password into a plain string

**File:** `app/core/config.py:63-74`

Cached property. Password is no longer SecretStr after access. If anything ever logs `container.settings.db_url`, password leaks.

**Fix:** Add an explicit `__repr__` that masks; or wrap in SecretStr.

---

### L14 — APScheduler logs at INFO flood stdout

**File:** `app/core/logging.py:54-58`

`basicConfig(force=True)` doesn't set per-logger levels. APScheduler is chatty.

**Fix:** `logging.getLogger("apscheduler").setLevel(WARNING)`.

---

### L15 — `pending_receipt_reminder` Redis marker key not env-namespaced

**File:** `app/jobs/pending_receipt_reminder.py:39-41`

`receipt_reminder:<id>:<label>` collides across dev/staging/prod if Redis shared.

**Fix:** Prefix with `{settings.env}:`.

---

### L16 — APScheduler `RedisJobStore` not env-namespaced

**File:** `app/core/scheduler.py:29-34`

Same as L15 but for jobstore.

**Fix:** Use `jobs_key=`/`run_times_key=` with env prefix.

---

### L17 — `_admin/panel.py:panel_upload_test` doesn't swap to cancel keyboard

Admin can tap "Загрузить тест", then tap another panel button → FSM state lingers, next file upload anywhere triggers parse.

**Fix:** Show `admin_cancel_keyboard()` after entering `waiting_for_file`.

---

### L18 — Banned user gets a `bot.send_message` per spam attempt (no rate limit on the ban reply)

**File:** `app/bot/middlewares/user_loader.py:56-62`

PRODUCT_BLUEPRINT §14.2: "no data leakage" → spec implies no reply at all.

**Fix:** Rate-limit the ban reply (1 per 5 min via Redis), or don't reply at all.

---

## ⚪ NIT (cosmetic / housekeeping)

- N1 — `_NON_PHOTO_REMINDER` text trailing period differs from spec (`payment.py:34`)
- N2 — `welcome_keyboard` uses string callback instead of CallbackData factory (`keyboards/onboarding.py:15`)
- N3 — `image_phash` stored as signed BIGINT (documented divergence; correct)
- N4 — Result screen percentage hardcoded `/ 50`; broadcast text hardcodes "53:20" (`result_screen.py:106`)
- N5 — Duplicate `_scores_from_attempt` helpers in two modules (`test_taking.py:438`, `attempt_service.py:376`)
- N6 — `assert refreshed is not None` after re-read (stripped under `-O`)
- N7 — `cmd_chat` doesn't HTML-escape the invite link
- N8 — `cmd_history` message confusing for `pending_payment` users
- N9 — Excel parser: empty file generates "0 questions" error but proceeds (harmless)
- N10 — `Settings.webhook_secret` required even in dev
- N11 — Engine pool `pool_size=10 + overflow=5` could starve under broadcast load; bump default to 20

---

## Architecture observations (not bugs, but worth thinking about)

### A1 — DB-side defense missing on key invariants

DATABASE_SPEC §6.2 explicitly delegates these to the application:

- "exactly one active test"
- "≤ 3 pending receipts per user"
- "receipt approval irreversible"

C2, C6, C8 above all stem from this. **Recommendation:** add the partial unique index on `tests.status='active'` (DB spec already shows the trick at §5.4) and a CHECK on a denormalized `users.pending_receipt_count`. Even if the app layer is right today, defense in depth is cheap and the alternative is debugging silent state corruption at 3am.

### A2 — Service-vs-repository boundary leaks

`services.admin: AdminRepository`, `services.attempt._scheduler: AsyncIOScheduler` directly. Two places where the strict layering of ARCHITECTURE_SPEC §4.1 has cracks. Neither is a bug today; both make future test refactors harder. **Recommendation:** introduce `AdminService` and `AttemptJobScheduler` (a protocol over APScheduler that takes attempt-aware methods).

### A3 — Single-flight `attempt_id` callbacks are not bound to FSM

Test-taking callbacks (A/B/C/D, nav, finish) carry `attempt_id` in callback_data, not in FSM. This is fine and resilient (stale messages keep working), but it means a user's FSM state and what they're actually doing can diverge. Two implications:
1. Authorization must be checked on every callback (currently is, via `get_state(attempt_id, user_id=...)`).
2. The FSM state machine described in PRODUCT_BLUEPRINT §10.1 isn't load-bearing — it's a hint, not a contract. Worth documenting.

### A4 — No backpressure on broadcast retries

If Telegram is throttling, broadcast retries cascade. Combined with H15 (drop-after-one-retry), pathological cases lose notifications silently. **Recommendation:** broadcast progress tracking + persistent retry queue; consider moving broadcast to a separate worker as v1.1 scales.

### A5 — Receipt photos rely on Telegram's `file_id` for retention

DATABASE_SPEC §21 (Open Question #5) acknowledges this. In practice for very old receipts, the file_id may expire. Once that happens, the admin's `/attempt` and audit tools can't show the original. **Recommendation:** for v1.1, download + persist a small thumbnail (~5KB) of every approved receipt to S3 — keeps audit trail viable.

### A6 — Settings table is global; no per-user / per-segment overrides

Acceptable for v1 (one teacher). If multi-teacher comes (v2), this becomes a refactor.

---

## Priority order for fixing

If you're cutting a "fix before client demo" branch, do these in order. The first six are 1–10 line changes.

1. **C1** — `AdminOnly` filter on receipts router. (1-line bot.py change)
2. **C2** — `WHERE status <> 'banned'` in `mark_approved`. (1-line repo change)
3. **H4** — `F.chat.type == "private"` on common router. (1-line bot.py change)
4. **H7** — Move `await callback.answer()` to the top of `on_approve`. (1-line handler change)
5. **H19** — `html_escape` on rejection reason + invite link in DM templates. (2 lines)
6. **C5** — Reorder `_run_webhook` startup. (3-line reorder in main.py)

Then the harder ones that need design:

7. **C4** — Recurring expired-attempt sweep job. (30-line cron job + registry hook)
8. **C6** — Per-user lock or DB constraint on pending count. (Redis advisory lock is the cheapest path)
9. **C3** — Finalize in-progress attempts in `TestService.publish`. (transaction logic, ~50 lines)
10. **C7** + **C8** — Status-guarded UPDATEs (one-line each) + DB unique on active test (migration).

Then the High and Medium tier — many are 1–5 line fixes once you've internalized the patterns above.

---

## What's NOT a problem (verified, listed for confidence)

- Ownership checks on every test-taking callback — verified, consistent.
- `mark_finished` IS status-guarded (`attempt_repository.py:97`) — H1's score-drop only applies to `submit_answer`, not the finish path.
- "Message is not modified" Telegram error is correctly swallowed (`test_taking.py:504-506`).
- `TestService.publish` archive-then-activate IS in one DB transaction (C8 is the *cross-publish* race, not the within-publish race).
- `_pick_next_position` boundaries are correct (1..50, wrap-around).
- Webhook secret-token verification works correctly.
- UTC datetime handling is consistent (`now_utc()` everywhere).
- Server-side `CURRENT_TIMESTAMP(6)` defaults match `UTCDateTime` round-tripping.
- `pool_pre_ping=True` is set.
- All FK ON DELETE policies in models match DATABASE_SPEC §7.
- Initial migration creates all CHECK constraints from DB spec §6 (with one documented divergence: `ck_receipts__reviewed_has_admin` is service-enforced due to MySQL 8.4 limitation).

---

## Final word

This codebase is **above average for a one-developer Telegram bot project** — better-structured than 80% of what you'd see in this category. The author respected the spec, separated concerns cleanly, and wrote tests. None of the criticals are architectural; all are tactical issues that surface once you start running real users.

The pattern that recurs across nearly every finding is the same: **status-check happens in Python after a DB read, and the subsequent write doesn't repeat the check in the SQL WHERE clause.** Fixing that single discipline across the codebase eliminates C2, C6, C7, H1, M6, M11, and a fistful of medium-tier issues. Worth treating as a one-week refactor sprint before launch.

Demo to the client confidently — but don't take a paying student until at least C1, C2, C4, C6 are fixed. Those are the ones that lose money or trust.
