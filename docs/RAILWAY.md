# Deploying to Railway

How to run the attestation bot on [Railway](https://railway.app). The repo ships
the deploy artifacts already: [`Dockerfile`](../Dockerfile),
[`railway.toml`](../railway.toml), [`start.sh`](../start.sh),
[`.dockerignore`](../.dockerignore). This doc is the runbook for wiring them up.

> Pair this with [`GO_LIVE_CHECKLIST.md`](GO_LIVE_CHECKLIST.md) — Railway gets
> the bot *running*; the checklist gets it *safe for a paying student* (real
> payment settings, the first admin, a live acceptance pass).

## How it runs on Railway

- **Webhook mode.** `ENV=prod` makes the bot run its aiohttp server and register
  a Telegram webhook (no polling). Railway terminates TLS at its edge and routes
  the service's public domain to the container's `$PORT` (the app binds `$PORT`).
- **No nginx.** Telegram POSTs straight to `https://<domain><WEBHOOK_PATH>`, so
  the URL path must equal `WEBHOOK_PATH` (the route the app registers).
- **Migrations** run in `start.sh` (`alembic upgrade head`) before the bot
  starts. Safe because the service is pinned to **one replica**.

## 1. Create the project and add the data services

In a new Railway project:

1. **+ New → Database → MySQL** (must be MySQL 8 — the schema uses CHECK
   constraints + a generated column).
2. **+ New → Database → Redis.**
3. **+ New → GitHub Repo →** `madaminovabdulaziz/testify` (the bot service).
   Railway reads `railway.toml` and builds the `Dockerfile`.

## 2. Generate the bot's public domain

Bot service → **Settings → Networking → Generate Domain**. This creates the
`RAILWAY_PUBLIC_DOMAIN` that `start.sh` turns into `WEBHOOK_URL`. (Do this before
the first successful boot, or set `WEBHOOK_URL` by hand.)

## 3. Set the bot service variables

Bot service → **Variables**. The `${{ ... }}` values are Railway *reference
variables* — they pull from the MySQL/Redis services. **Adjust the service name**
(`MySQL` / `Redis`) to match what you named them, and confirm the source
variable names in each service's Variables tab.

| Variable | Value | Notes |
|---|---|---|
| `ENV` | `prod` | Enables webhook mode. **Required.** |
| `BOT_TOKEN` | _(from @BotFather)_ | **Required**, secret. |
| `WEBHOOK_SECRET` | `openssl rand -hex 32` | Verified on every update. **Required.** |
| `ADMIN_GROUP_ID` | _(negative supergroup id)_ | **Required.** Add the bot to the admin group; get the id via @RawDataBot. |
| `WEBHOOK_PATH` | `/webhook/<openssl rand -hex 16>` | Optional but recommended — a path-secret layer. Default `/webhook`. |
| `DATABASE_URL` | `${{ MySQL.MYSQL_URL }}` | **Required.** One variable for the whole DB connection (private host). The app rewrites the driver to `asyncmy` automatically. |
| `REDIS_URL` | `${{ Redis.REDIS_URL }}` | **Required.** Prefer the private URL if exposed. |
| `SENTRY_DSN` | _(your Sentry DSN)_ | Optional but strongly recommended for prod. |

> **Database connection — pick one style.** The simplest is the single
> `DATABASE_URL` above (the app also accepts `MYSQL_URL` directly, so even that
> name works). If you'd rather wire the parts individually, omit `DATABASE_URL`
> and set all five discrete vars instead — they take exactly the same reference
> values:
>
> | Variable | Value |
> |---|---|
> | `DB_HOST` | `${{ MySQL.MYSQLHOST }}` (private `*.railway.internal` host) |
> | `DB_PORT` | `${{ MySQL.MYSQLPORT }}` |
> | `DB_USER` | `${{ MySQL.MYSQLUSER }}` |
> | `DB_PASSWORD` | `${{ MySQL.MYSQLPASSWORD }}` |
> | `DB_NAME` | `${{ MySQL.MYSQLDATABASE }}` |
>
> Reference variables only resolve if the service name in `${{ … }}` exactly
> matches your MySQL service (default `MySQL`). If neither style is fully set,
> the boot fails fast with `Database not configured…`.

`WEBHOOK_URL` is **auto-derived** by `start.sh` as
`https://$RAILWAY_PUBLIC_DOMAIN$WEBHOOK_PATH`. Only set it explicitly if you use
a custom domain.

Do **not** set `PORT` — Railway injects it and the app binds to it.

## 4. Deploy

Railway builds and deploys on push to `main` (and on the first link). On boot,
`start.sh` runs migrations, then the bot registers its webhook and starts
serving. Watch **Deployments → Logs** for:

```
▶ alembic upgrade head
INFO ... Running upgrade ... -> 0004 ...
▶ starting bot (env=prod, port=..., webhook=https://...)
bot started
```

Healthcheck (`/healthz`) must go green (it pings MySQL + Redis) before Railway
routes traffic.

## 5. One-time: seed the first admin

A fresh database has zero admins, so nobody can approve receipts or upload
tests until you seed the owner. From your machine with the
[Railway CLI](https://docs.railway.app/guides/cli) linked to the project:

```bash
railway run --service <bot-service> python -m scripts.seed_admin <teacher-telegram-id>
```

(or open a shell on the service and run the same command). Idempotent.

## 6. One-time: replace placeholder settings

The payment card / recipient / amount and the group link ship as placeholders in
the DB. Set the real values from Telegram as an admin — see
[`GO_LIVE_CHECKLIST.md`](GO_LIVE_CHECKLIST.md) §C.2 (`/set …`, then verify with
`/settings` and `/preview payment`). **Until you do this, students would pay a
fake card.**

## Updating

Push to `main` → Railway rebuilds and redeploys. Migrations re-run (idempotent).
Roll back from the Railway dashboard (Deployments → ⋯ → Redeploy a previous one).

## Troubleshooting

| Symptom | Check |
|---|---|
| Build fails | Logs for the `Dockerfile` build; ensure the repo (incl. `Dockerfile`, `start.sh`) is pushed. |
| Crash loop on boot | Usually DB vars wrong or MySQL not reachable — `alembic upgrade head` fails first. Verify the `${{ MySQL.* }}` refs. |
| `/healthz` never green | MySQL or Redis unreachable from the bot service; confirm both services are in the same project and the refs resolve. |
| Telegram not delivering updates | `WEBHOOK_URL` path ≠ `WEBHOOK_PATH`, or `WEBHOOK_SECRET` mismatch. Check `getWebhookInfo`. |
| `webhook_url and webhook_secret are required` | `ENV=prod` but `WEBHOOK_SECRET` unset or no domain generated (so `WEBHOOK_URL` couldn't derive). |
