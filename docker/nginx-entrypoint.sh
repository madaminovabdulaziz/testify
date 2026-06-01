#!/bin/sh
# nginx-entrypoint.sh — render docker/nginx.conf.template via envsubst, then exec nginx.
#
# The official nginx-alpine image has an envsubst-on-templates mechanism
# (NGINX_ENVSUBST_*) but it only processes files in /etc/nginx/templates/
# and writes them to /etc/nginx/conf.d/ as server-block fragments. Our
# template is a full nginx.conf, so we render it into place ourselves.
#
# Required env vars (set in .env / docker-compose.prod.yml):
#   NGINX_SERVER_NAME    e.g. bot.example.com
#   WEBHOOK_SECRET_PATH  the random suffix baked into WEBHOOK_URL

set -eu

: "${NGINX_SERVER_NAME:?NGINX_SERVER_NAME is required}"
: "${WEBHOOK_SECRET_PATH:?WEBHOOK_SECRET_PATH is required}"

TEMPLATE="/etc/nginx/nginx.conf.template"
TARGET="/etc/nginx/nginx.conf"

# Only substitute the two variables we own — leave the literal $-prefixed
# nginx variables ($host, $remote_addr, $http_…) alone.
envsubst '${NGINX_SERVER_NAME} ${WEBHOOK_SECRET_PATH}' < "$TEMPLATE" > "$TARGET"

# nginx -t verifies the rendered config before we exec so a typo gives
# us a fail-fast on container start rather than a runtime 502.
nginx -t -c "$TARGET"

exec nginx -g "daemon off;" -c "$TARGET"
