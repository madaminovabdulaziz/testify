#!/usr/bin/env bash
# Railway entrypoint for the attestation bot.
#
# Railway runs ONE replica of this service, terminates TLS at its edge, and
# routes the service's public domain to $PORT. There is no nginx and no
# separate migration container, so this script:
#   1. applies DB migrations (idempotent),
#   2. derives WEBHOOK_URL from Railway's public domain when not set,
#   3. execs the bot in webhook mode (ENV must be "prod"/"staging").
#
# See docs/RAILWAY.md for the full deploy setup.
set -euo pipefail

# 1. Schema migrations. Idempotent (no-op at head). Running them here — rather
#    than in a one-shot container as ARCHITECTURE_SPEC §14.4 prescribes — is
#    safe on Railway because numReplicas=1 (railway.toml), so there is no
#    multi-replica migration race. If a migration fails the container exits and
#    Railway surfaces it instead of serving against a half-migrated schema.
echo "▶ alembic upgrade head"
alembic upgrade head

# 2. WEBHOOK_URL: the path in the URL MUST equal WEBHOOK_PATH (the route the app
#    registers and the path Telegram POSTs to — there is no proxy to rewrite
#    it). Default path is /webhook; set WEBHOOK_PATH to /webhook/<random> in
#    Railway for an extra path-secret layer. The X-Telegram-Bot-Api-Secret-Token
#    header (WEBHOOK_SECRET) is verified in-app regardless.
: "${WEBHOOK_PATH:=/webhook}"
if [ -z "${WEBHOOK_URL:-}" ] && [ -n "${RAILWAY_PUBLIC_DOMAIN:-}" ]; then
  export WEBHOOK_URL="https://${RAILWAY_PUBLIC_DOMAIN}${WEBHOOK_PATH}"
fi

echo "▶ starting bot (env=${ENV:-prod}, port=${PORT:-8080}, webhook=${WEBHOOK_URL:-<unset>})"

# 3. Hand off (PID 1) so SIGTERM reaches the bot for a graceful shutdown.
exec python -m app.main
