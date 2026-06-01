#!/usr/bin/env bash
# backup.sh — nightly mysqldump → gzip → ./backups/attestation-<ts>.sql.gz
#
# DATABASE_SPEC §12. Cron-friendly: silent on success aside from the
# emitted file name; non-zero exit on any failure. Runs against the
# ``mysql`` container of ``docker-compose.prod.yml``.
#
# Environment:
#   COMPOSE_FILE      Override compose file path (default: docker-compose.prod.yml)
#   BACKUP_DIR        Where to write the .sql.gz (default: ./backups)
#   BACKUP_KEEP_DAILY How many daily backups to keep on disk (default: 14)
#   DB_NAME           Database to dump (default: $DB_NAME from .env, else attestation)
#   DB_ROOT_PASSWORD  Root password for ``mysqldump`` (default: $DB_ROOT_PASSWORD from .env)
#
# Off-host retention is out of scope: pipe the produced file to your
# existing rsync / S3 / restic cron after this script returns 0.
#
# Example crontab line (runs daily at 03:15 UTC):
#   15 3 * * * cd /opt/attestation-bot && ./scripts/backup.sh >> /var/log/attestation-backup.log 2>&1

set -euo pipefail

# Move to the repository root so ``docker compose -f docker-compose.prod.yml`` resolves.
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &> /dev/null && pwd)"
cd "${SCRIPT_DIR}/.."

# Load .env if present so DB_NAME / DB_ROOT_PASSWORD are picked up here too.
if [[ -f .env ]]; then
    # shellcheck disable=SC1091
    set -a; . ./.env; set +a
fi

COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.prod.yml}"
BACKUP_DIR="${BACKUP_DIR:-./backups}"
BACKUP_KEEP_DAILY="${BACKUP_KEEP_DAILY:-14}"
DB_NAME="${DB_NAME:-attestation}"

if [[ -z "${DB_ROOT_PASSWORD:-}" ]]; then
    echo "ERROR: DB_ROOT_PASSWORD must be set (in .env or the environment)." >&2
    exit 2
fi

mkdir -p "${BACKUP_DIR}"

TIMESTAMP="$(date -u +'%Y-%m-%dT%H%M%SZ')"
OUT_FILE="${BACKUP_DIR}/attestation-${TIMESTAMP}.sql.gz"

# --single-transaction keeps the dump consistent against InnoDB without
# acquiring a global lock (the bot stays online during the dump).
# --routines + --triggers cover anything we add to the schema later.
docker compose -f "${COMPOSE_FILE}" exec -T mysql \
    mysqldump \
        --single-transaction \
        --routines \
        --triggers \
        --skip-lock-tables \
        -uroot \
        -p"${DB_ROOT_PASSWORD}" \
        "${DB_NAME}" \
    | gzip -9 > "${OUT_FILE}"

# mysqldump errors short-circuit the pipe via ``set -o pipefail``, but
# a 0-byte file from a failed dump is still possible if gzip got an
# empty stream. Defend against it.
if [[ ! -s "${OUT_FILE}" ]]; then
    echo "ERROR: backup file is empty — dump likely failed." >&2
    rm -f "${OUT_FILE}"
    exit 3
fi

echo "Wrote ${OUT_FILE} ($(du -h "${OUT_FILE}" | cut -f1))"

# Retention: keep the newest BACKUP_KEEP_DAILY files, delete the rest.
# Using ``ls -t`` + ``tail`` is fine because we control the filenames
# (lexicographically sorted == chronologically sorted thanks to ISO 8601).
mapfile -t OLD_FILES < <(
    find "${BACKUP_DIR}" -maxdepth 1 -type f -name 'attestation-*.sql.gz' \
        -printf '%T@ %p\n' \
        | sort -nr \
        | tail -n +"$((BACKUP_KEEP_DAILY + 1))" \
        | awk '{print $2}'
)

for old in "${OLD_FILES[@]}"; do
    [[ -z "${old}" ]] && continue
    rm -f "${old}"
    echo "Pruned ${old}"
done
