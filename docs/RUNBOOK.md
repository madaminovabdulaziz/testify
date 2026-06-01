# Operator's Runbook

How to deploy, observe, recover, and modify the production bot. Audience:
the developer on call. Assumes Linux host with Docker + docker-compose, a
DNS A-record pointing at the server, and a TLS certificate.

This document is the **operational** counterpart to the three design specs
in this folder. When something in production goes wrong, this is the file
you open first.

> **First launch with a real client?** Work
> [`GO_LIVE_CHECKLIST.md`](GO_LIVE_CHECKLIST.md) first — it gates the
> client-handoff steps this runbook assumes are already done (collecting the
> teacher's real payment details, replacing the placeholder settings, and a
> live acceptance pass) before a paying student touches the bot.

---

## 0. Quick reference

| Action                            | Command                                                       |
| --------------------------------- | ------------------------------------------------------------- |
| First-time deploy                 | `make deploy && make seed-admin TELEGRAM_ID=<yours>`          |
| Rolling update                    | `git pull && make deploy`                                     |
| Rollback to previous image        | `git checkout <prev-sha> && make deploy`                      |
| Healthcheck                       | `make smoke` (or `curl https://$NGINX_SERVER_NAME/healthz`)   |
| Tail logs                         | `docker compose -f docker-compose.prod.yml logs -f bot`       |
| Database backup                   | `make backup` (or `./scripts/backup.sh`)                      |
| Restore from backup               | `make restore BACKUP=./backups/attestation-<timestamp>.sql.gz`|
| Add admin                         | `make seed-admin TELEGRAM_ID=<tg-id> [ROLE=moderator]`        |
| Rotate bot token                  | See §5                                                        |
| Rotate webhook secret             | See §6                                                        |

---

## 1. Initial deploy

Prerequisites on the target host:

* Docker Engine + the `docker compose` plugin (v2).
* A DNS A-record pointing your domain at the host's public IP.
* A TLS cert + key, e.g. via Let's Encrypt + certbot on the host.
* Outbound HTTPS open (so the bot can reach `api.telegram.org`).
* Inbound 80 + 443 open (so Telegram can reach the webhook).

### 1.1 Clone + configure

```bash
git clone <repo-url> /opt/attestation-bot
cd /opt/attestation-bot
cp .env.example .env
$EDITOR .env
```

Fill in every value. The non-obvious ones:

* `BOT_TOKEN` — from `@BotFather`. Treat as a credential.
* `WEBHOOK_URL` — `https://$NGINX_SERVER_NAME/webhook/$WEBHOOK_SECRET_PATH`.
  Telegram posts updates here. **Must** match the path nginx routes.
* `WEBHOOK_SECRET_PATH` — random URL segment. Generate with
  `openssl rand -hex 24`. Routed by nginx; nothing else uses it.
* `WEBHOOK_SECRET` — the secret-token *header* the bot verifies on
  every update. Independent of the path. Generate with
  `openssl rand -hex 32`.
* `NGINX_SERVER_NAME` — the public domain (matches the cert).
* `ADMIN_GROUP_ID` — the negative-integer ID of the private admin
  supergroup. Get it by adding `@RawDataBot` to the group.
* `DB_PASSWORD` + `DB_ROOT_PASSWORD` — strong randoms; the root password
  is only used by `scripts/backup.sh`.
* `ENV=prod` (so `app/main.py` runs in webhook mode, not polling).

### 1.2 Drop the cert in place

```bash
mkdir -p certs
cp /etc/letsencrypt/live/$NGINX_SERVER_NAME/fullchain.pem certs/
cp /etc/letsencrypt/live/$NGINX_SERVER_NAME/privkey.pem  certs/
```

Or symlink. The container reads `certs/fullchain.pem` and
`certs/privkey.pem` — keep those exact filenames.

### 1.3 Bring it up

```bash
make deploy
```

`make deploy` runs the §14.4 ARCHITECTURE_SPEC sequence: pull → build →
start mysql+redis → wait healthy → `alembic upgrade head` in a one-shot
container → start bot+nginx.

### 1.4 Seed the first admin

```bash
make seed-admin TELEGRAM_ID=<your-telegram-numeric-id>
```

Until this runs, no one can use the admin commands. The teacher's
Telegram ID is the canonical owner; assistants can be added later from
inside Telegram (`/add_admin` is a v1.1 add; for v1 use
`make seed-admin TELEGRAM_ID=<tg-id> ROLE=moderator` to add each one
manually).

### 1.5 Smoke test

```bash
make smoke
```

Hits `https://$NGINX_SERVER_NAME/healthz` and expects 200. Then DM
your bot `/start` from a real Telegram client. You should see the
welcome screen.

### 1.6 Set up the backup cron

Add to root's crontab:

```cron
# Nightly DB backup at 03:15 UTC. Logs to /var/log/attestation-backup.log.
15 3 * * * cd /opt/attestation-bot && ./scripts/backup.sh >> /var/log/attestation-backup.log 2>&1
```

Then drill the restore (§4.2) at least once before you trust it.

---

## 2. Rolling update

```bash
cd /opt/attestation-bot
git pull
make deploy
```

`make deploy` is idempotent: pulled images stay running until the new
ones are healthy, then `docker compose up -d` swaps them. Migrations
run in a one-shot container before the bot is replaced (per
ARCHITECTURE_SPEC §14.4), so the live bot is never running against a
schema it does not know about.

**Zero-downtime caveat:** `docker compose up -d bot` briefly restarts
the bot. With one replica, updates that take effect *during* a Telegram
update may drop that one update on the floor — Telegram redelivers
within seconds. The window is sub-second in practice.

---

## 3. Rollback

```bash
cd /opt/attestation-bot
git log --oneline -n 10           # find the previous good sha
git checkout <previous-sha>
make deploy
```

If the rollback requires undoing a schema migration:

```bash
docker compose -f docker-compose.prod.yml run --rm bot alembic downgrade -1
# verify the schema you want is in place
make deploy                       # restart with the old image
```

**Always** ensure the rolled-back code is compatible with the current
schema. If a migration introduced a new NOT-NULL column the old code
doesn't write, the old code will crash on every insert. In that case,
restore from a backup taken *before* the bad migration instead (§4.2).

---

## 4. Database operations

### 4.1 Backup (manual)

```bash
make backup
# or directly:
./scripts/backup.sh
```

Writes `./backups/attestation-<ISO-8601 ts>.sql.gz`. Retains the
newest `BACKUP_KEEP_DAILY` (default 14) and prunes the rest. **Does
not** copy the file off-host — pipe to your existing rsync/S3/restic
cron after this script returns 0.

### 4.2 Restoration drill

Do this on a staging host (or at minimum a stopped bot) **before** you
need it. Acceptance criterion from PRODUCT_BLUEPRINT §17 + DATABASE_SPEC
§12.2.

```bash
# 1. Stop the bot to prevent writes during restore.
docker compose -f docker-compose.prod.yml stop bot

# 2. Restore the latest backup.
make restore BACKUP=$(ls -1t backups/attestation-*.sql.gz | head -1)

# 3. Bring the bot back up.
docker compose -f docker-compose.prod.yml up -d bot

# 4. Verify: an approved user from the backup should still be approved.
docker compose -f docker-compose.prod.yml exec mysql \
    mysql -uroot -p$DB_ROOT_PASSWORD $DB_NAME \
    -e "SELECT COUNT(*) AS approved FROM users WHERE status='approved';"
```

### 4.3 Inspect data ad-hoc

```bash
docker compose -f docker-compose.prod.yml exec mysql \
    mysql -uroot -p$DB_ROOT_PASSWORD $DB_NAME
```

For routine debugging, prefer the in-bot admin commands (`/find`,
`/attempt <id>`) — they are designed exactly for this and you can
trigger them from Telegram in seconds.

---

## 5. Rotating the bot token

When the BotFather token is leaked:

```bash
# 1. Revoke + regenerate in @BotFather (/revoke + new token).
# 2. Update .env:
$EDITOR .env             # set BOT_TOKEN=<new>
# 3. Restart the bot to pick it up.
docker compose -f docker-compose.prod.yml up -d bot
# 4. Re-register the webhook (the bot does this automatically on start;
#    confirm via Telegram's getWebhookInfo if in doubt).
```

The bot's webhook secret + URL stay the same, so no nginx change is
needed.

---

## 6. Rotating the webhook secret

The webhook is protected by two independent secrets — the URL path
(`WEBHOOK_SECRET_PATH`) and the header (`WEBHOOK_SECRET`). Rotate
whichever is leaked.

### 6.1 Header only (more common)

```bash
$EDITOR .env            # set WEBHOOK_SECRET=<new openssl rand -hex 32>
docker compose -f docker-compose.prod.yml up -d bot
# The bot re-registers its webhook on startup with the new secret.
```

### 6.2 URL path

If the path leaked, also rotate the path so old Telegram-cached URLs
stop working:

```bash
NEW_PATH=$(openssl rand -hex 24)
$EDITOR .env            # set WEBHOOK_SECRET_PATH=$NEW_PATH and
                        #     WEBHOOK_URL=https://$NGINX_SERVER_NAME/webhook/$NEW_PATH
docker compose -f docker-compose.prod.yml up -d bot nginx
```

Both bot (Telegram-side) and nginx (server-side) need the new path —
the compose up restarts both with the new env.

---

## 7. Admin management

### 7.1 Adding an admin

```bash
make seed-admin TELEGRAM_ID=<tg-id> ROLE=moderator
```

Owner role is for the teacher; moderator is for the 1–3 trusted
assistants (PRODUCT_BLUEPRINT §4.2). The bot does not currently expose
an in-Telegram add-admin command — manual seed for v1.

### 7.2 Removing an admin

There is no `make remove-admin` — direct SQL until v1.1:

```sql
DELETE FROM admins WHERE telegram_id = <tg-id>;
```

The user's `users` row is untouched; only their admin rights are
revoked.

### 7.3 Banning / unbanning students

Use the in-bot admin commands `/ban <user_id>` and `/unban <user_id>`
(see PRODUCT_BLUEPRINT §8.9). DB-level moderation should not be
needed.

---

## 8. Observability

### 8.1 Logs

All logs go to stdout as structured JSON (`structlog`):

```bash
docker compose -f docker-compose.prod.yml logs -f bot
```

Pipe to `jq` for pretty-printing:

```bash
docker compose -f docker-compose.prod.yml logs -f bot \
    | jq -Rr 'try fromjson catch .'
```

Each line includes `request_id`, `telegram_id`, `update_type` so you
can grep one user's session out of the noise.

### 8.2 Sentry

If `SENTRY_DSN` is set, unhandled exceptions ship there with PII
scrubbed (no phone numbers, no receipt photos). Set an alert on
`error_rate > 5/min`.

### 8.3 Healthcheck

* Internal: docker-compose's healthcheck calls `/healthz` on the bot
  every 30s; an unhealthy container shows in `docker compose ps`.
* External: monitor `https://$NGINX_SERVER_NAME/healthz` from your
  uptime checker; it should return `{"status":"ok"}` with 200.

---

## 9. Troubleshooting

| Symptom                                          | First check                                                                  |
| ------------------------------------------------ | ---------------------------------------------------------------------------- |
| Bot doesn't respond to any message               | `make smoke`. If 200, check `docker compose logs bot` for `bot_starting`.    |
| `/healthz` returns 503                           | DB or Redis is down. `docker compose ps`; restart whichever is unhealthy.    |
| Telegram says "Webhook URL is set" but no updates | Verify nginx is forwarding to `/webhook/$WEBHOOK_SECRET_PATH` and 80/443 are open. Curl Telegram's `getWebhookInfo`. |
| Bot logs `403` from webhook handler              | `WEBHOOK_SECRET` on bot side ≠ secret-token Telegram is sending. Rotate per §6.1. |
| `make seed-admin` says "no module named app"     | Run inside the prod bot container (the Makefile target already does so).     |
| Migration fails on `alembic upgrade head`        | Roll back the image (§3) and restore from backup (§4.2). Investigate offline. |
| Backups directory full                           | Lower `BACKUP_KEEP_DAILY` in cron env, prune manually, or move to a bigger volume. |

---

## 10. Release-checklist crib sheet

Before declaring v1 done (PRODUCT_BLUEPRINT §17 + ARCHITECTURE_SPEC §22
+ DATABASE_SPEC §16):

* [ ] `make lint && make typecheck && make test` all clean on `main`.
* [ ] `make deploy` runs cleanly on staging (this runbook end-to-end).
* [ ] `/healthz` returns 200 from the public domain.
* [ ] `/start` from a real Telegram client renders the welcome screen.
* [ ] Receipt approval round-trip (submit → admin tap → DM) < 1s.
* [ ] One full restoration drill (§4.2) passed.
* [ ] Cron backup is configured + the log is being written.
* [ ] Sentry receives a staged exception.
* [ ] `make seed-admin` ran for every intended admin (owner +
      moderators).
* [ ] Owner has reviewed `/settings` once and edited the placeholder
      `payment_card_number`, `payment_recipient_name`,
      `group_invite_link`, `support_contact` (DATABASE_SPEC §8 seed
      values are intentionally TODO-flagged).
