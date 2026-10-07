#!/bin/sh
# OpenJM Enterprise AI backend entrypoint (VS8).
#
# The database password has ONE source: the Docker secret mounted at
# OPENJM_DB_PASSWORD_FILE and shared with the db service. The DSN is assembled
# here so the backend and the db can never disagree on the password (a mismatch
# would otherwise surface only as a connection failure at runtime). Any
# OPENJM_DATABASE_URL present in the ambient environment is intentionally
# overridden when the secret file is available.
set -e

if [ -n "${OPENJM_DB_PASSWORD_FILE:-}" ] && [ -f "${OPENJM_DB_PASSWORD_FILE}" ]; then
    db_password="$(cat "${OPENJM_DB_PASSWORD_FILE}")"
    export OPENJM_DATABASE_URL="postgresql+asyncpg://${OPENJM_DB_USER:-openjm}:${db_password}@${OPENJM_DB_HOST:-db}:${OPENJM_DB_PORT:-5432}/${OPENJM_DB_NAME:-openjm}"
fi

exec "$@"
