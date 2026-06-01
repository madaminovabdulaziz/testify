# Go-Live Checklist — handing the bot to the client

**Purpose:** the gate to clear **before a paying student touches the bot.**
This is a *delta* on top of [`RUNBOOK.md`](RUNBOOK.md): the runbook tells you
*how* to deploy and operate; this file tells you *what must be true* and *in
what order* for a first launch with a real teacher and real money.

It exists because `RUNBOOK.md` §1 gets the bot **running**, but a running bot
with placeholder payment details would take students' money to a fake card.
The dangerous gap between "deploys cleanly" and "safe for a paying student" is
almost entirely **configuration + one live acceptance pass** — that gap is
what this checklist closes.

Work the phases in order. Do not skip Phase E (the go/no-go gate).

---

## Phase A — Collect from the client *(before you touch the server)*

These are the `PRODUCT_BLUEPRINT §20` open questions. The bot cannot safely
launch until every value below is a **real** value, not a placeholder. Get
them from the teacher in writing.

- [ ] **Bank card number** for receiving payments (the live default is the
      fake `8600 1234 5678 9012`).
- [ ] **Recipient name** as it appears on the card (default is the literal
      `[ИМЯ ПРЕПОДАВАТЕЛЯ]`).
- [ ] **Payment amount** in UZS — the number (e.g. `150000`) **and** the
      display string (e.g. `150 000 сум`).
- [ ] **Student group chat** exists, and you have a **static invite link**
      for it (`group_invite_link`). Without this, approved students get an
      empty link.
- [ ] **Support contact** username for the "У меня вопрос" button
      (`support_contact`, e.g. `@teacher_help`).
- [ ] **Admin Telegram IDs** — the teacher (owner) + 1–3 assistants
      (moderators). Numeric IDs (via `@RawDataBot`), not @usernames.
- [ ] **Admin group** created, the bot added to it, and its negative
      supergroup ID captured for `ADMIN_GROUP_ID`.
- [ ] **Bot** created in `@BotFather`; you hold the token.
- [ ] **Domain + TLS** — a domain pointed at the host with a valid cert
      (the webhook needs real HTTPS).

> If any Phase-A value is still unknown, **stop** — you cannot complete
> Phase C, and launching with a default is the one failure mode that costs
> the teacher real money.

---

## Phase B — Pre-deploy verification *(on your dev/build machine)*

There is **no CI in this repo** (`.github/workflows` is absent), so these run
by hand and must be green before you cut a release.

- [ ] `make lint` clean (`ruff check` + `ruff format --check`).
- [ ] `make typecheck` clean (`mypy app/services app/repositories`).
- [ ] `make test` clean — **the full suite, including integration.**
      Integration tests need a reachable Docker daemon (they spin up MySQL
      8.4 via `testcontainers`) and `testcontainers` installed in the test
      env. If Docker isn't reachable they **silently skip** — confirm you see
      the integration tests *run*, not skip, or you've only proven the unit
      layer.
- [ ] Confirm you are deploying a **known commit** (tag it), so rollback
      (`RUNBOOK §3`) has a target.

---

## Phase C — Deploy + configure *(on the host)*

### C.1 Deploy and seed admins

Follow [`RUNBOOK §1`](RUNBOOK.md) end-to-end (`.env` fill-in, cert drop,
`make deploy`, `make seed-admin`). Checkpoints:

- [ ] `.env` has **no** `replace-with-…` placeholders left (`BOT_TOKEN`,
      `WEBHOOK_SECRET`, `WEBHOOK_SECRET_PATH`, `WEBHOOK_URL`,
      `ADMIN_GROUP_ID`, `NGINX_SERVER_NAME`, strong `DB_PASSWORD` /
      `DB_ROOT_PASSWORD`) and `ENV=prod`.
- [ ] `SENTRY_DSN` is set (otherwise you are blind to production errors —
      `RUNBOOK §8.2`).
- [ ] `make deploy` completed; `make smoke` returns 200.
- [ ] `make seed-admin TELEGRAM_ID=<owner-id>` ran, plus one per moderator
      with `ROLE=moderator`.

### C.2 Replace the placeholder settings *(the money step)*

The payment copy lives in the `settings` table, **not** in `.env`. It ships
with deliberately fake defaults. From an admin account (owner/moderator),
in the admin group or a DM with the bot, run `/set <key> <value>` for each —
substitute the real Phase-A values:

```
/set payment_card_number 8600 1111 2222 3333
/set payment_recipient_name Иванова Дилноза Каримовна
/set payment_amount 150000
/set payment_amount_display 150 000 сум
/set group_invite_link https://t.me/+RealInviteHash
/set support_contact @real_support_username
```

(`/set` accepts only these eight keys; it also takes `welcome_message` and
`payment_instructions` if the teacher wants to tweak the wording — the seeded
Russian defaults are fine to keep.)

Verify before going further:

- [ ] `/settings` — every payment value is real; **no** `8600 1234 5678 9012`
      and **no** `[ИМЯ ПРЕПОДАВАТЕЛЯ]` remain.
- [ ] `/preview payment` — renders the real card, recipient, amount, and a
      reference code with no leftover `{placeholders}`.
- [ ] `/preview welcome` — reads correctly to the teacher.
- [ ] `group_invite_link` opens the **correct** student group.
- [ ] `support_contact` resolves to a real account.

---

## Phase D — Live acceptance pass *(real Telegram clients)*

`make smoke` only proves `/start` renders. These are the
`ARCHITECTURE_SPEC §17.2` critical flows plus the image feature — run each
once against the **production** bot with throwaway test accounts, then clean
up the test users.

- [ ] **Onboarding → payment → approval → first test → score.** New account:
      `/start` → share contact → name → see payment screen with the **real**
      card → send a receipt photo → it lands in the admin group with the
      user's details + reference code → admin taps ✅ → student gets the
      approval DM **with the working group link** → `Пройти тест` → answer
      questions → finish → score screen (score only, no answer key).
- [ ] **Receipt rejection + resubmit.** Admin taps ❌, types a reason → the
      student gets the reason and can submit a new receipt.
- [ ] **Timer / auto-submit.** (Optional but recommended on staging with a
      shortened `TEST_DURATION_SECONDS`.) Let an attempt expire → it
      auto-submits and DMs the result exactly once.
- [ ] **Resume after restart.** Start a test, then `docker compose ... restart
      bot` mid-attempt → the student's next tap resumes at the right question
      (state survives in MySQL + Redis).
- [ ] **Duplicate receipt.** Submit the same receipt image twice → the admin
      notification is flagged as a possible duplicate.
- [ ] **Banned user.** `/ban <user_id>` → that user gets only
      "Доступ ограничён" and no further interaction.
- [ ] **Question images (new).** Upload a test (`/upload_test`) where at least
      one row has `has_image = да` → the bot prompts for that question's photo
      → send it → preview shows "С изображениями: N" → publish → as a student,
      navigate to that question and confirm the **image renders in the
      question**, and that moving to/from it (and finishing) doesn't strand a
      stale screen.
- [ ] **Admin tooling.** `/stats`, `/find <phone|username|code>`,
      `/leaderboard <test_id>`, `/attempt <id>` all respond.

---

## Phase E — Go / No-Go gate

**Do not launch unless every box below is checked.** These are the
hard blockers — each one either loses money, locks people out, or hides
failures.

- [ ] Phase A: all client values collected (no unknowns).
- [ ] Phase B: `lint` + `typecheck` + full `test` green (integration ran,
      not skipped).
- [ ] Phase C.2: `/settings` shows **zero** placeholder payment values.
- [ ] At least the **first** Phase-D flow (onboard → pay → approve → test →
      score) passed end-to-end on the production bot.
- [ ] `make seed-admin` ran for the owner (someone can actually approve
      receipts).
- [ ] `SENTRY_DSN` set and a staged exception was received.
- [ ] One **restoration drill** passed (`RUNBOOK §4.2`) and the nightly
      backup cron is installed (`RUNBOOK §1.6`).

If any box is unchecked: **No-Go.** Fix it, re-verify, re-gate.

---

## Phase F — First 48 hours

- [ ] Tail logs for the first several real users
      (`docker compose -f docker-compose.prod.yml logs -f bot | jq -Rr 'try fromjson catch .'`).
- [ ] Watch Sentry; set an alert on `error_rate > 5/min` (`RUNBOOK §8.2`).
- [ ] Confirm the **first real payment** approval round-trip worked and the
      money actually arrived on the teacher's card.
- [ ] Confirm the pending-receipt reminder fires (a receipt left unreviewed
      pings the admin group after `RECEIPT_REMINDER_AFTER_HOURS`).
- [ ] Verify the first nightly backup file was written and is non-empty.

---

## Known gaps / accepted risks for v1

Flag these to the client so they're choices, not surprises:

- **No CI.** Tests run only when an operator runs them. A regression won't be
  caught automatically — re-run `make test` before every deploy.
- **Receipt photo & question-image longevity.** Only Telegram `file_id`s are
  stored, never bytes (`ARCHITECTURE_SPEC §21.5`). This is efficient and
  matches the receipt design, but a `file_id` is not guaranteed permanent
  forever. In practice they last well beyond a test's active window; if an old
  image ever fails to load, re-upload the test. Durable byte storage is the
  documented v1.1 upgrade.
- **Question images: one per question; sent one at a time.** The authoring
  prompt names the exact question before each photo. If the teacher sends an
  *album* instead of one photo at a time, photos fill positions in arrival
  order, which Telegram doesn't guarantee — instruct her to send them singly.
  Previewing the actual images back for confirmation is a v1.1 add.
- **Single replica.** A bot restart can drop the single in-flight Telegram
  update; Telegram redelivers within seconds (`RUNBOOK §2`).
- **Manual refunds, manual admin removal.** Both are out of scope for v1
  (`PRODUCT_BLUEPRINT §9.8`, `RUNBOOK §7.2`).
