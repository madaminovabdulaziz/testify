# Product Blueprint — Russian Language Attestation Bot

**Document type:** Product specification (Phase 1 of 3)
**Version:** 1.0
**Date:** 2026-05-21
**Status:** Draft, pending review
**Author role:** Senior Product Architect / Product Manager
**Next phases:** (2) Technical architecture spec, (3) Database engineering spec

---

## 1. Executive Summary

A Telegram bot that serves as the **digital companion to a single teacher's offline classroom** for Russian-language teachers preparing for the Uzbekistan state attestation exam (DTM). The bot does three things and three things only: gatekeep access via one-time payment, deliver faithful 50-question mock exams on demand, and funnel approved students into a private group chat where the teacher's actual instruction takes place.

The product is intentionally narrow. It is **not** an EdTech platform. It is **not** a content product. It is the digital arm of a real teacher's existing business — automating the parts she repeats every week (taking payments, distributing tests, scoring) so she can spend her time teaching.

---

## 2. Product Vision

> **"The teacher presses a button after class; her students take the real exam, in miniature, on their phones — and walk into the group chat afterwards already knowing how they did."**

Success looks like a teacher who never again has to manually print test papers, chase payments, or score answer sheets.

---

## 3. Problem & Context

### 3.1 The exam being simulated

Russian-language teachers in Uzbekistan must pass the **DTM attestation** to maintain or upgrade their teaching certification. The test is fixed in format:

| Section | Questions | Weight | Content |
|---|---|---|---|
| **Русский язык** (Rus tili) | 1–35 | 70% of items | Subject knowledge — grammar, lexicology, methodology of Russian |
| **Педагогическое мастерство** (Pedagogik mahorat) | 36–45 | 20% of items | Pedagogy, didactics, classroom management |
| **Профессиональный стандарт** (Kasbiy standart) | 46–50 | 10% of items | Education law, professional standards |

- Single-choice format (A/B/C/D), with some questions where the correct answer is a *combination* of items expressed as one option (e.g., "B. 1, 2, 3").
- Linear navigation **plus** free random access via a number grid.
- Time allocation: **64 seconds per question × 50 = 3,200 seconds (53 min 20 sec)** as one global countdown.

### 3.2 The teacher's current workflow (today)

1. Holds in-person classes
2. Wants to test students at the end of a session or as homework
3. Currently does this manually — paper tests, manual scoring, WhatsApp groups for discussion
4. Wants students to pay before getting access to her materials
5. Maintains a group chat with paying students where she explains answers, gives feedback, and builds loyalty

### 3.3 The opportunity

Automating the painful parts (payment, test delivery, scoring) while preserving the **valuable part she does manually** (teaching in the group chat). The bot must respect this division — it deliberately gives students **only the score**, never the answer key, because answer review is what she does live.

---

## 4. Target Users & Personas

### 4.1 Student (primary user) — "Malika"

- 28-year-old female Russian-language teacher at a public school in Tashkent
- Preparing for her 2-year recertification
- Hears about the teacher from a colleague or social media
- Owns an Android phone with limited data
- Comfortable with Telegram but not necessarily with web forms
- Speaks Russian as her professional language but Uzbek as her mother tongue
- Anxious about the exam, wants realistic practice and a clear sense of "am I ready?"
- Will not pay unless she trusts the source; the teacher's reputation is what converts her

**Her job-to-be-done:** "I want to know what the real attestation will feel like, and find out where my weak spots are, so I don't fail and lose my certification."

### 4.2 Teacher / Admin (secondary user but the customer) — "Dilnoza opa"

- The Russian teacher who owns the bot
- Mid-40s, well-known locally, has hundreds of students per year
- Tech-comfortable but not technical (uses Telegram, Instagram, Excel)
- Manages the bot via a private admin group with 1–3 trusted assistants
- Approves payments, uploads tests, runs the group chat
- Does **not** want to learn a web admin panel; will refuse to use anything outside Telegram and Excel

**Her job-to-be-done:** "Stop being a payment-collector and test-photocopier; let me focus on the teaching itself."

### 4.3 Operator (us / developers)

- Maintains the bot
- Needs structured logs, error tracking, ability to debug a specific student's attempt
- Needs deploys to be safe and reversible

---

## 5. Goals & Non-Goals

### 5.1 Goals (v1)

- **G1.** Reliable one-time payment-and-approve flow that doesn't drop receipts.
- **G2.** Faithful reproduction of the DTM exam UX (50 questions, 3-section structure, grid navigation, 53:20 timer).
- **G3.** Excel-based test authoring — teacher uploads a file, bot does the rest.
- **G4.** Strict access control — non-paying users cannot start a test.
- **G5.** Group chat funnel — approved students receive a private invite link automatically.
- **G6.** Survivability — a bot restart during an active exam must not lose the student's progress.
- **G7.** Admin can manage the bot entirely from inside Telegram (no web panel required for v1).

### 5.2 Non-Goals (v1)

- ❌ Showing students which questions they got wrong, or showing the correct answers
- ❌ Per-question explanations
- ❌ Adaptive difficulty / personalized learning paths
- ❌ Subscriptions, tiered pricing, coupons, referral codes
- ❌ Multiple teachers / multi-tenant architecture
- ❌ Web admin panel
- ❌ Native mobile app
- ❌ Voice or video questions
- ❌ Time-boxed scheduled cohorts ("everyone takes Saturday at 7pm")
- ❌ Public leaderboards (private to the admin only)

These are deliberate non-goals because either (a) they conflict with the teacher's business model — she sells the *post-test discussion* — or (b) they expand scope beyond what one developer should build in v1.

---

## 6. Product Principles

These are the trade-off resolvers when future decisions are ambiguous:

1. **Respect the teacher's business model.** The bot exists to support her offline classroom, not replace it. When in doubt, do less, not more.
2. **Telegram-native.** No links to external sites, no "open in browser." Everything happens inside the chat. The student should never leave Telegram during onboarding, payment, or test-taking.
3. **Survive the network.** Uzbek mobile internet is flaky. Every state must persist server-side. A user closing the app mid-exam and reopening 10 minutes later must resume cleanly.
4. **Admin trust over automation.** When the admin's judgment differs from the bot's logic, the admin wins. Always provide a manual override.
5. **No clever features.** Every feature must be justifiable by the teacher's actual workflow. If she wouldn't ask for it, don't build it.
6. **Russian first, Russian only.** The product is for Russian teachers. The interface language is Russian. No multilingual toggle in v1.

---

## 7. User Journeys (High-Level)

### 7.1 Student happy path

```
Discovers bot (via teacher's referral)
   ↓
/start → Welcome message
   ↓
Onboarding: share contact → enter name
   ↓
Sees payment instructions + unique reference code
   ↓
Pays via bank app (outside bot) → returns
   ↓
Sends receipt screenshot
   ↓
Waits (notification on decision)
   ↓
[Approved] → receives success message + group invite link
   ↓
Joins group chat
   ↓
Later: teacher publishes a test
   ↓
Student opens bot → "Пройти тест" → reads rules → confirms
   ↓
Takes 50 questions with 3,200s timer
   ↓
Sees final score
   ↓
Joins post-test discussion in group chat
```

### 7.2 Student unhappy paths

- **Receipt rejected** → user sees reason → can resubmit a new receipt
- **Time expires during test** → auto-submit with current answers → score shown
- **User abandons test** (closes app, doesn't come back) → on next return, can resume if still within time window; otherwise auto-submitted on time expiry
- **User has no Telegram username** → fine, we use telegram_id

### 7.3 Admin happy path

```
Receives receipt in admin group with user details + unique reference code
   ↓
Verifies in their bank app, taps ✅ Одобрить (or ❌ Отклонить + reason)
   ↓
[Later] Builds questions in Excel template
   ↓
Sends Excel file to bot
   ↓
Bot parses + shows preview ("50 questions: 35+10+5 ✓")
   ↓
Admin taps "Опубликовать" (optional: ☑ "Уведомить студентов")
   ↓
Test is now active for all approved students
   ↓
[Later] Reviews leaderboard for that test
```

---

## 8. Detailed Feature Specifications

### 8.1 Onboarding

**Purpose:** Capture minimum identity data, set expectations, route user to payment.

**Trigger:** `/start` command from a user not yet onboarded.

**Flow:**

1. **Welcome message** sent (see §11.1 for exact copy). Contains explanation of what the bot does, what the test looks like, and a single button: `[Начать ▶️]`
2. User taps `Начать`. Bot replies with a request for contact using a `KeyboardButton(request_contact=True)`:

   > Чтобы продолжить, поделитесь, пожалуйста, своим номером телефона. Это нужно, чтобы преподаватель могла связаться с вами при необходимости.
   >
   > `[📱 Поделиться номером]`

3. On contact received, bot stores the phone number. Bot asks for full name:

   > Спасибо! Теперь напишите, пожалуйста, ваше полное имя (как в документе).

4. User sends name as text. Bot validates:
   - Length: 2–80 characters
   - Must contain at least one letter (not pure digits or symbols)
   - Trimmed of surrounding whitespace
   - If invalid: "Пожалуйста, введите корректное имя." and stay in this state.

5. Bot generates a unique 6-character alphanumeric reference code (e.g., `A7F2K9`) and assigns it to the user.

6. Bot transitions to payment screen (see §8.2).

**Edge cases:**
- User shares contact that doesn't match their Telegram account → accept it anyway (some users have multiple SIMs), but log a warning for the admin
- User tries to skip contact share by sending text → bot reminds: "Пожалуйста, нажмите кнопку 'Поделиться номером' ниже."
- User /start's again mid-onboarding → bot resumes from the current state, doesn't reset

### 8.2 Payment Instructions

**Purpose:** Show bank details and reference code, prompt receipt upload.

**Behavior:**

1. Bot sends a message containing:
   - Brief explanation (one paragraph) of what payment unlocks
   - Bank card number (16 digits, hardcoded as placeholder for now — see §11.3)
   - Recipient name
   - Amount in UZS
   - The user's unique reference code, formatted prominently
   - Instruction to include the reference code in the payment comment

2. Two inline buttons:
   - `[💳 Я оплатил, отправить чек]` → transitions user to "awaiting receipt" state
   - `[❓ У меня вопрос]` → sends user a contact message (e.g., admin's Telegram handle) and stays in same state

3. **Awaiting receipt state:** Bot accepts the next photo from the user. Any non-photo message during this state gets a gentle reminder ("Пожалуйста, отправьте фото чека").

4. On photo received:
   - Compute perceptual hash of the image
   - If hash matches any previously approved receipt (any user) → flag for admin review with warning "⚠️ Похожий чек уже был одобрен ранее"
   - If hash matches a pending receipt from the same user → reject silently: "Этот чек уже отправлен на проверку."
   - Otherwise → create a `payment_receipt` record with `status=pending`
   - Forward photo to the admin group (see §8.3)
   - Reply to user: "✅ Чек получен. Мы проверим его в ближайшее время и сообщим вам о решении."
   - Transition user state to `pending_approval`

5. **Receipt submission limit:** A user can have at most **3 pending receipts** at a time. The 4th attempt is rejected with: "У вас уже есть чеки на проверке. Пожалуйста, дождитесь решения."

### 8.3 Admin Receipt Review

**Purpose:** Let admins approve/reject receipts inside the admin group.

**Behavior:**

1. When a receipt is submitted, bot posts to the **admin group** (configured via env var `ADMIN_GROUP_ID`):
   - The receipt photo
   - Caption:
     ```
     🧾 Новый чек на проверку

     👤 Имя: {full_name}
     📱 Телефон: {phone}
     🆔 Username: @{username or "—"}
     🔖 Код: #{reference_code}
     ⏱ Отправлен: {timestamp}

     {⚠️ warning if duplicate hash detected}
     ```
   - Inline buttons:
     - `[✅ Одобрить]`
     - `[❌ Отклонить]`

2. **On Approve:**
   - Mark receipt as `approved`, store `reviewed_by_admin_id` and `reviewed_at`
   - Mark user as `approved` (this is the global "paid student" flag)
   - DM the user:
     ```
     🎉 Поздравляем! Ваш платёж подтверждён.

     Вот ссылка на закрытый чат студентов:
     {group_invite_link}

     Когда преподаватель опубликует тест, вы получите уведомление, и сможете пройти его в этом боте.
     ```
   - Edit the original admin-group message to show: "✅ Одобрено @{admin_username} в {timestamp}" and remove the buttons.

3. **On Reject:**
   - Bot replies in the admin group: "Укажите причину отказа (или 'отмена'):"
   - Admin types a reason as a normal reply to that message
   - Bot stores rejection reason
   - Marks receipt as `rejected`
   - DM the user:
     ```
     ❌ К сожалению, ваш чек не был одобрен.

     Причина: {rejection_reason}

     Вы можете отправить новый чек.
     ```
   - User state returns to "awaiting receipt"
   - Edit original admin-group message to show: "❌ Отклонено @{admin_username}: {reason}" and remove the buttons.

4. **Idempotency:** If two admins tap a button on the same receipt simultaneously, the second tap is acknowledged ("Этот чек уже обработан @{admin}") but produces no double-write.

5. **Re-approval:** If a user is in state `rejected` and submits a new receipt, the flow repeats normally. If a user is already `approved` and somehow sends another receipt, bot replies: "Вы уже студент. Дополнительная оплата не требуется."

### 8.4 Test Publishing (Admin)

**Purpose:** Admin authors a test in Excel and publishes it to all students.

**Authoring flow:**

1. Admin sends `/upload_test` command in the admin group (or DM with the bot).
2. Bot replies: "Отправьте файл Excel с тестом. Шаблон: /template"
3. Admin sends an `.xlsx` file.
4. Bot parses the file (see §12 for template spec). On success:
   - Replies in same chat with a preview:
     ```
     📋 Загружен новый тест

     Всего вопросов: 50
       • Русский язык: 35 ✓
       • Педагогическое мастерство: 10 ✓
       • Профессиональный стандарт: 5 ✓

     Название: {auto-generated, e.g. "Тест от 2026-05-21"}

     [✏️ Изменить название] [📢 Опубликовать с уведомлением] [📤 Опубликовать тихо] [🗑 Отменить]
     ```

5. On parse failure, bot replies with specific errors (line number + reason). Examples:
   - "❌ Строка 12: пустой текст вопроса"
   - "❌ Строка 27: 'correct_option' должен быть один из A, B, C, D"
   - "❌ Ожидалось 35 вопросов в разделе 'Русский язык', найдено 34"

**Publishing flow:**

1. On `Опубликовать с уведомлением`:
   - Mark this test as `active`
   - Mark the previously-active test (if any) as `archived`
   - Send a broadcast message to all `approved` users:
     ```
     📢 Доступен новый тест!

     Откройте бота и нажмите "Пройти тест", чтобы начать.

     ⏱ У вас будет 53 минуты 20 секунд.
     ```
   - Reply in admin chat: "✅ Тест опубликован. Уведомление отправлено {N} студентам."

2. On `Опубликовать тихо`: same as above but without the broadcast. The test is available but students learn about it from the teacher in class.

3. On `Отменить`: discard the upload.

**Rules:**
- Only one test can be `active` at a time.
- Publishing a new test does not affect in-progress attempts on the old test — those students continue and submit normally.
- Archived tests retain all their data (questions + past attempts) for reporting.

### 8.5 Test Taking (Student)

**Purpose:** Deliver the exam experience.

**Entry points:**
- Student receives broadcast notification → taps `Пройти тест` button
- Student opens bot and sees a persistent menu button `Пройти тест` (or sends `/test`)

**Pre-test screen:**

```
📝 Тест готов к прохождению

Структура:
  • Русский язык: вопросы 1–35
  • Педагогическое мастерство: вопросы 36–45
  • Профессиональный стандарт: вопросы 46–50

⏱ Время: 53 минуты 20 секунд (на весь тест)
📊 Результат: только балл, без разбора (разбор — в чате)

⚠️ Внимание: как только вы нажмёте "Начать", таймер запустится.
Тест можно пройти только один раз.

[▶️ Начать тест] [🔙 Назад]
```

**On "Начать":**

1. Create an `attempt` record with `started_at = now()`, `status = in_progress`
2. Save state in Redis: `attempt_id`, `current_question = 1`, `answers = {}`
3. Render the **test screen** (see below) as a single message
4. Send a **separate** pinned timer message that shows just the current question number and remaining time — but actually, no, this complicates state. Single message is better.

**Test screen layout (single message, edited on every action):**

```
⏱ Осталось: 42:15  ·  Вопрос 5/50  ·  Раздел: Русский язык

Какой из следующих глаголов относится к первому спряжению?

A. Видеть
B. Слышать
C. Читать
D. Держать

[A] [B] [C] [D]

[⬅️ Назад] [Вперёд ➡️]
[🏁 Завершить тест]

Русский язык (1–35):
[1✅][2✅][3 ][4✅][🔴5][6 ][7 ][8 ][9 ][10]
[11][12][13][14][15][16][17][18][19][20]
[21][22][23][24][25][26][27][28][29][30]
[31][32][33][34][35]

Педагогическое мастерство (36–45):
[36][37][38][39][40][41][42][43][44][45]

Профессиональный стандарт (46–50):
[46][47][48][49][50]
```

**Symbol legend:**
- `🔴N` = current question
- `N✅` = answered question
- `N` = unanswered question

**Interactions:**

- Tap **A/B/C/D** → save answer, advance to next unanswered question (or next sequential if all later are answered, or stay if last)
- Tap **⬅️ Назад** → previous question
- Tap **Вперёд ➡️** → next question
- Tap any number in the grid → jump to that question
- Tap **🏁 Завершить тест** → confirmation dialog:
  ```
  Вы ответили на {X} из 50 вопросов.
  {Y} вопросов остались без ответа.

  Завершить тест и узнать результат?

  [✅ Да, завершить] [↩️ Продолжить]
  ```

**Timer behavior:**
- Timer is calculated from `attempt.started_at + 3200s`
- Displayed value is refreshed on every user action (every button tap updates the message and recomputes time)
- **No background timer updates** — the displayed time only refreshes when the user interacts. This avoids race conditions and reduces edit-rate (Telegram throttles message edits).
- **Warning messages** sent as separate messages at:
  - T-10:00 — "⏱ Осталось 10 минут"
  - T-5:00 — "⏱ Осталось 5 минут"
  - T-1:00 — "⏱ Осталась 1 минута"
- These are sent via a scheduled job (APScheduler), keyed to `attempt_id`.
- **At T-0:00:** force auto-submit. Scheduled job triggers; attempt status becomes `expired`; result calculated; result message sent.

**Resume behavior:**
- If user closes Telegram and returns, they can tap `Пройти тест` again.
- If they have an `in_progress` attempt within the time window, bot re-renders the test screen with current state from DB.
- If their time has expired in absentia, they see the expired-attempt result message.

**Network resilience:**
- All button taps are processed idempotently using Telegram's `callback_query.id` as a dedup key.
- If the user double-taps a button, the second tap is ignored.

### 8.6 Results

**Purpose:** Show the student their score immediately upon submission.

**On submit (manual or auto):**

1. Calculate scores:
   - Total: `(correct_count / 50) * 100` → rounded to 1 decimal
   - Per section: same formula scoped to the section's questions
2. Update attempt: `finished_at`, `score`, `status = submitted` or `expired`
3. Render result message:

```
🏁 Тест завершён!

📊 Ваш результат: {score}/50  ({percentage}%)

По разделам:
  • Русский язык: {rus_correct}/35
  • Педагогическое мастерство: {ped_correct}/10
  • Профессиональный стандарт: {std_correct}/5

⏱ Затрачено времени: {duration}

Разбор вопросов — в чате студентов.

[💬 Перейти в чат]
```

4. Bot does **not** reveal which questions were right or wrong, nor the correct answers. This is by deliberate design.

5. Student's state returns to "approved, idle" — they wait for the next test.

**One attempt per test rule:** Each user can take each test exactly once. If they try to take an already-completed test, bot shows their previous result instead of starting over.

### 8.7 Group Chat Access

**Purpose:** Funnel approved students into the teacher's discussion group.

**Behavior:**
- On approval, bot sends user an invite link generated via Telegram's `createChatInviteLink` (or a static link, depending on admin preference — see Open Questions).
- For v1, use a **static invite link** configured by the admin via settings. Simpler; if abuse becomes an issue, rotate manually.
- Link is also re-shown:
  - In the result message after each completed test
  - When the user sends `/chat` to the bot

### 8.8 Settings Management (Admin)

**Purpose:** Let the admin change copy and numbers without redeploying code.

**Settings (all editable via admin commands in the admin group):**

| Key | Description | Default |
|---|---|---|
| `welcome_message` | First message shown on /start | See §11.1 |
| `payment_amount` | Display amount in UZS | 150000 |
| `payment_card_number` | 16-digit display card number | `8600 1234 5678 9012` (placeholder) |
| `payment_recipient_name` | Name shown to user | "[ИМЯ ПРЕПОДАВАТЕЛЯ]" |
| `payment_instructions` | Free-text additional notes | See §11.2 |
| `group_invite_link` | Static invite link to the student group | (empty until set) |
| `support_contact` | Username for "У меня вопрос" button | (empty until set) |

**Commands:**
- `/settings` — show current values
- `/set <key> <value>` — update a value
- `/preview welcome` — see how the welcome message will render to a user

Only admins (defined in the `admins` table) can use these commands.

### 8.9 Admin Operations

Additional commands available to admins:

- `/stats` — global counters (total users, approved, pending, tests published, attempts submitted)
- `/find <phone or username or reference_code>` — locate a user, show their status and recent activity
- `/ban <user_id>` — mark user as banned (cannot use bot)
- `/unban <user_id>`
- `/leaderboard <test_id>` — top 20 scores for a given test (admin's eyes only)
- `/attempt <attempt_id>` — full detail of one attempt (for support/debug)
- `/template` — bot sends the Excel template file to the requester

---

## 9. Business Rules

These are invariants that must hold throughout the system:

1. **One Telegram account = one user.** Phone number is collected but is not the primary identifier; `telegram_id` is.
2. **A user is "approved" globally**, not per test. Once approved, all future tests are unlocked.
3. **Only one test is "active" at any given time.** Publishing a new test archives the previous one.
4. **A user can take each test at most once.** Re-entry to a completed test shows results, not a fresh attempt.
5. **An attempt, once started, consumes the test slot** even if abandoned. Auto-submitted at time expiry.
6. **Receipt review is irreversible.** Once approved or rejected, the status cannot be flipped (admin must re-engage with the user manually if a mistake was made).
7. **Banned users cannot interact with the bot.** All messages return "Доступ ограничён." No data leakage.
8. **No payment refunds.** Out of scope. If a refund is required, admin handles it manually via the bank.
9. **Question correctness is determined at upload time.** If a question is later found to have a typo or wrong answer, the admin can either (a) leave past attempts as-is or (b) re-publish the test with fixes (this archives the old one and creates a new one — past attempts retain their scores).
10. **Test time limit is fixed at 3,200 seconds** for v1, hardcoded. Configurability can come later.

---

## 10. State Machines

### 10.1 User state machine

```
[new]
  │ /start
  ▼
[onboarding_phone]
  │ contact shared
  ▼
[onboarding_name]
  │ valid name entered
  ▼
[pending_payment]
  │ receipt photo received
  ▼
[pending_approval]
  │
  ├── admin approves ──▶ [approved] ◀──┐
  │                         │           │
  │                         │ /test     │
  │                         ▼           │
  │                    [in_test]        │
  │                         │           │
  │                         │ submit /  │
  │                         │ expire    │
  │                         └───────────┘
  │
  └── admin rejects ──▶ [rejected]
                           │ new receipt sent
                           ▼
                      [pending_approval]

[banned]  ← reachable from any state via admin action
```

### 10.2 Receipt state machine

```
[pending] ──approve──▶ [approved] (terminal)
          ──reject───▶ [rejected] (terminal)
```

### 10.3 Test state machine

```
[draft] ──publish──▶ [active] ──new test published──▶ [archived]
        ──cancel──▶ (deleted)
```

### 10.4 Attempt state machine

```
[in_progress] ──user submits──▶ [submitted]
              ──timer expires──▶ [expired]
```

---

## 11. Content & Copy (Russian)

All bot-facing copy is in Russian. Stored in settings table for editability.

### 11.1 Welcome message (default)

```
Здравствуйте! 👋

Это бот для подготовки к аттестации учителей русского языка.

Здесь вы сможете:
✅ Пройти полный пробный тест (50 вопросов)
✅ Узнать свой балл и оценить готовность
✅ Попасть в закрытый чат студентов, где преподаватель разбирает каждый тест

Структура теста:
📚 Русский язык — 35 вопросов
👨‍🏫 Педагогическое мастерство — 10 вопросов
📋 Профессиональный стандарт — 5 вопросов

⏱ На весь тест отводится 53 минуты 20 секунд.

Чтобы начать, нам нужно немного познакомиться.

[Начать ▶️]
```

### 11.2 Payment instructions (default)

```
Чтобы получить доступ к тестам, оплатите подготовку:

💰 Сумма: 150 000 сум
💳 Карта: 8600 1234 5678 9012
👤 Получатель: [ИМЯ ПРЕПОДАВАТЕЛЯ]

📌 ВАЖНО: в комментарии к платежу укажите ваш код:
#{reference_code}

Это поможет нам быстро найти ваш платёж.

После оплаты нажмите кнопку ниже и отправьте скриншот чека.

[💳 Я оплатил, отправить чек]
[❓ У меня вопрос]
```

### 11.3 Other key strings

| Context | Text |
|---|---|
| Receipt accepted | "✅ Чек получен. Мы проверим его в ближайшее время и сообщим вам о решении." |
| Approved | "🎉 Поздравляем! Ваш платёж подтверждён.\n\nВот ссылка на закрытый чат студентов: {link}\n\nКогда преподаватель опубликует тест, вы получите уведомление." |
| Rejected | "❌ К сожалению, ваш чек не был одобрен.\n\nПричина: {reason}\n\nВы можете отправить новый чек." |
| New test broadcast | "📢 Доступен новый тест!\n\nОткройте бота и нажмите «Пройти тест», чтобы начать.\n\n⏱ У вас будет 53 минуты 20 секунд." |
| Time warning 10min | "⏱ Осталось 10 минут до конца теста." |
| Time warning 5min | "⏱ Осталось 5 минут!" |
| Time warning 1min | "⏱ Осталась 1 минута!" |
| Auto-submitted | "⏰ Время вышло. Тест автоматически завершён." |
| Already attempted | "Вы уже проходили этот тест.\n\nВаш результат: {score}/50" |
| No active test | "Сейчас нет доступных тестов. Преподаватель опубликует следующий — мы вам сообщим." |
| Banned access attempt | "Доступ к боту ограничён." |

All copy is finalized in the settings table at deploy; the teacher can adjust later via `/set` commands.

---

## 12. Excel Template Specification

**File format:** `.xlsx` (Microsoft Excel 2007+ format)
**Sheet name:** `Questions` (other sheets ignored)
**Encoding:** UTF-8 (default for xlsx)

**Columns (in this exact order, first row is header):**

| # | Column | Type | Required | Validation |
|---|---|---|---|---|
| A | `section` | string | yes | One of: `rus_tili`, `pedagogik`, `kasbiy` |
| B | `position` | int | yes | 1–50, unique within file |
| C | `question_text` | string | yes | 1–1000 chars |
| D | `option_a` | string | yes | 1–300 chars |
| E | `option_b` | string | yes | 1–300 chars |
| F | `option_c` | string | yes | 1–300 chars |
| G | `option_d` | string | yes | 1–300 chars |
| H | `correct_option` | string | yes | One of: `A`, `B`, `C`, `D` |

**Validation rules at parse time:**

1. Exactly 50 rows (excluding header).
2. Positions 1–50, no gaps, no duplicates.
3. Section counts: `rus_tili = 35`, `pedagogik = 10`, `kasbiy = 5`.
4. Section-to-position mapping:
   - `rus_tili` rows must have positions 1–35
   - `pedagogik` rows must have positions 36–45
   - `kasbiy` rows must have positions 46–50
5. No empty cells in required columns.
6. `correct_option` case-insensitive but stored uppercase.

**Errors are line-referenced** so the admin can fix them quickly:

> ❌ Строка 12: текст вопроса пустой
> ❌ Строка 27: значение в колонке 'correct_option' должно быть A, B, C или D
> ❌ Вопросы раздела 'pedagogik' должны быть на позициях 36–45 (строка 33: позиция 47)

**Optional column** (v1.1+, not in v1):
- `explanation` — Hidden from students, shown only to admin in `/attempt` detail. Useful for the teacher's own notes.

The bot provides a downloadable template via `/template`. The template has the headers pre-filled and 5 example rows.

---

## 13. Edge Cases & Error Handling

| Scenario | Behavior |
|---|---|
| User blocks the bot, then unblocks | Bot's outbound messages to that user will fail; bot catches `Forbidden` errors gracefully and logs them. On the user's next message, normal flow resumes. |
| User /start's mid-onboarding | Resume from current state. /start does not reset state after onboarding starts (except for `new` users). |
| User tries to /test before approval | Bot replies: "Сначала нужно оплатить подготовку. Отправьте /start, чтобы начать." |
| User sends a photo when not awaiting one | Bot ignores the photo silently or replies: "Я не ожидаю фото сейчас." |
| Admin sends Excel file outside `/upload_test` flow | Bot replies in admin group: "Чтобы загрузить тест, сначала используйте /upload_test." |
| Excel file has wrong extension | Bot replies: "Нужен файл .xlsx" |
| Excel file is huge (>5MB) | Reject with: "Файл слишком большой. Максимум 5 МБ." |
| Excel parse crashes the bot | Wrapped in try/except; admin gets "Не удалось прочитать файл. Проверьте формат." Stack trace goes to Sentry. |
| User has 0 username AND name parsing failed | Use telegram_id as identifier in admin notifications. |
| Two admins try to approve the same receipt | First wins, second sees "Этот чек уже обработан @{first_admin}". |
| User tries to take an archived test | Cannot — only the currently `active` test is accessible. Past attempts on archived tests remain viewable in admin tools. |
| User's attempt is in progress when test gets archived | Their attempt continues to completion on the archived test. |
| Bot restarts during an active attempt | Redis FSM and DB attempt record survive. User's next button tap resumes correctly. |
| Telegram API rate limit hit during broadcast | Implement throttled broadcast (30 msg/sec); on 429, back off and retry. |
| User sends multiple photos in one message (media group) | Process only the first photo; reply: "Спасибо. Если нужно, отправьте дополнительные чеки отдельными сообщениями." |
| User is in `pending_approval` for >7 days | Send a reminder to the admin group: "⏰ Чек от {user} ждёт проверки 7 дней." |

---

## 14. Anti-Abuse & Security

### 14.1 Receipt fraud

- **Perceptual image hashing (pHash)** on every receipt. Duplicates against previously approved receipts → flagged in admin notification with warning. Duplicates against currently pending → silently dropped.
- **Receipt submission rate limit:** max 3 pending per user.
- **Reference code:** unique per user, included in admin notification, so admin can cross-check with their bank app.

### 14.2 Account abuse

- **One Telegram ID = one user.** No way around it.
- **Phone uniqueness check (soft):** if a phone number is already attached to a different `approved` user, flag the new submission in the admin notification.
- **Ban list.** Banned users get a uniform "Доступ ограничён" and no other interaction.

### 14.3 Admin security

- Admin actions are gated by membership in the `admins` table.
- Admin group ID is configured via env var, not hardcoded.
- The bot ignores admin commands sent outside the admin group (except DM from registered admins for some commands like `/upload_test`, `/template`).
- All admin actions are logged with `admin_id` + `timestamp`.

### 14.4 Data protection

- Receipt photos are not downloaded or stored on disk. Only Telegram's `file_id` is persisted.
- Phone numbers are stored in plain MySQL (not E2EE) but the database is access-controlled.
- No third-party trackers, no analytics SDKs that exfiltrate user data.

### 14.5 Webhook security

- Webhook URL contains a long random secret path.
- `X-Telegram-Bot-Api-Secret-Token` header verified on every incoming update.

---

## 15. Non-Functional Requirements

### 15.1 Performance

- **Bot response time:** p95 < 500ms for any user action under normal load.
- **Broadcast time:** publishing a test to 1,000 students completes within 60 seconds.
- **Concurrent attempts:** support 200 simultaneous in-progress test attempts without degradation.

### 15.2 Reliability

- **Uptime target:** 99.5% (≈ 3.5 hours downtime/month).
- **Data durability:** no data loss on bot restart. All FSM state in Redis with AOF persistence; all business data in MySQL.
- **Graceful degradation:** if Redis is down, bot replies with "Технические работы, попробуйте через минуту." rather than crashing.

### 15.3 Observability

- **Structured JSON logs** for all handlers.
- **Sentry** for unhandled exceptions.
- **Metrics** (Prometheus-compatible): handler latency, error rate, attempts per hour, broadcast throughput.
- **Admin debug command** `/attempt <id>` exposes full state of any attempt for support.

### 15.4 Maintainability

- All copy in DB settings table — editable without redeploy.
- All numbers (payment amount, time limit, etc.) configurable via settings.
- Alembic migrations for schema changes.
- Docker-based deploy; rollback = redeploy previous image.

### 15.5 Localization

- v1: Russian only.
- Future: Uzbek toggle. Designed so all user-facing strings live in one place to enable translation later.

---

## 16. Analytics & KPIs

These should be queryable via `/stats` and built into the schema, even if no dashboard exists in v1.

**Funnel metrics:**
- /start clicks
- Onboarding completions (name + phone captured)
- Receipts submitted
- Receipts approved
- First-test attempts
- Repeat-test attempts (took 2+ tests)

**Engagement metrics:**
- Active students per week (≥1 test attempt)
- Average attempts per student
- Median attempt duration
- Auto-submit rate (expired vs manual submit)

**Quality metrics:**
- Per-question correctness rate (helps the teacher identify ambiguous or trick questions)
- Per-section score distribution

**Operational metrics:**
- Average receipt review time
- Receipt rejection rate
- Duplicate-receipt flag rate

---

## 17. Acceptance Criteria for v1

A v1 release is shippable when **all** of these are true:

- [ ] A new user can complete onboarding → payment → approval → first test attempt → score, without developer intervention
- [ ] Admin can approve and reject receipts via inline buttons in the admin group, with rejection reasons reaching the user
- [ ] Admin can upload an Excel test, see the preview, and publish it (with or without student notification)
- [ ] A student can take a 50-question test with a working timer, grid navigation, and forced submit at time expiry
- [ ] Bot restart during an active attempt does not lose the student's progress
- [ ] All user-facing copy is in Russian and editable via settings
- [ ] Duplicate receipts are flagged for admin attention
- [ ] Banned users get no response other than "Доступ ограничён"
- [ ] Admin can run `/stats`, `/find`, `/leaderboard`, `/attempt` for support
- [ ] Sentry receives unhandled exceptions
- [ ] Webhook secret token is verified
- [ ] At least one staging deploy has run a 50-student simulated load test without errors

---

## 18. Out of Scope (v1) — explicit list

To prevent scope creep during build:

- Web admin panel
- Multi-language UI
- Payment via Telegram's built-in payments / cards / crypto
- Subscription model
- Coupons, referrals, discounts
- Multi-teacher / multi-tenant
- Question explanations shown to students
- Adaptive testing
- Practice mode (untimed, individual questions)
- Public leaderboards
- Student-to-student messaging via bot
- Push notifications outside Telegram
- Email
- Bot analytics dashboard (raw queries are sufficient for v1)
- Mobile app
- Exporting test results to PDF/Excel for students

---

## 19. Future Roadmap (v1.1+)

Possible features, prioritized by likely teacher demand:

1. **v1.1 — Quality of life**
   - `explanation` column in Excel template (admin-only viewing)
   - Per-question correctness analytics surfaced to admin
   - Better `/leaderboard` formatting

2. **v1.2 — Practice mode**
   - Untimed individual-question practice
   - Pulled from archived tests
   - Free or paid (TBD)

3. **v1.3 — Light web admin panel**
   - For test authoring without Excel
   - Question bank management
   - Student CRM view

4. **v1.4 — Subscription option**
   - Monthly access alongside one-time payment
   - Auto-expiry of approved status

5. **v2 — Multi-tenant**
   - Multiple teachers, each with their own bot instance or namespace
   - Shared question bank optional

---

## 20. Open Questions & Assumptions

### Open questions to confirm with the teacher

1. **Group invite link** — static or per-user-generated? Recommendation: static for v1, rotate manually if abuse occurs.
2. **Payment amount** — placeholder 150,000 UZS. Needs final number.
3. **Recipient name on card** — placeholder. Need real name.
4. **Card number** — placeholder. Need real one before launch.
5. **Support contact** — what's the username students should reach for issues?
6. **Initial admin list** — who are the 1–3 trusted assistants?
7. **Admin group** — does she have one already, or do we create a new one for the bot?

### Assumptions made (please flag if any are wrong)

- The teacher does not want a public landing page; all marketing happens via her existing channels (Instagram, word of mouth, in-class referral).
- The teacher does not need analytics export — `/stats` in Telegram is enough.
- The teacher is fine with manual refunds (we won't build a refund flow).
- The teacher will not need to handle students who lost access to their Telegram account (edge case; manual handling fine).
- Tests are not time-of-day restricted (a student can take a published test at 3am if they want).

---

## 21. Glossary

| Term | Meaning |
|---|---|
| **Attestation** | The DTM teacher recertification exam in Uzbekistan |
| **Approved** | A user who has paid and whose receipt was confirmed |
| **Active test** | The single test currently available to take |
| **Archived test** | A previously published test, retained for history but not takeable |
| **Attempt** | One user's session of taking one test |
| **Reference code** | 6-character alphanumeric code shown to user during payment, used by admin to match receipts |
| **Admin group** | The private Telegram group where the bot posts receipt-approval requests |
| **FSM** | Finite State Machine — used to track each user's progress through multi-step flows |
| **Section** | One of the three blocks of the DTM exam: `rus_tili`, `pedagogik`, `kasbiy` |

---

## 22. Document History

| Version | Date | Author | Notes |
|---|---|---|---|
| 1.0 | 2026-05-21 | Product (Phase 1) | Initial draft based on discovery conversation |

---

## Next Phases

**Phase 2 — Technical Architecture Spec** (to be written by Senior Software Engineer role):
- Service layering, module breakdown, dependency injection approach
- Aiogram handler organization, FSM structure
- Redis key conventions
- Background job design (APScheduler usage)
- Deployment topology (Docker, nginx, webhook setup)
- Logging, monitoring, alerting

**Phase 3 — Database Engineering Spec** (to be written by Senior DB Engineer role):
- Complete schema with all tables, columns, types, indexes, constraints
- Migration strategy (Alembic)
- Indexing strategy for performance under load
- Backup and recovery
- Sample queries for `/stats`, `/leaderboard`, `/attempt`
