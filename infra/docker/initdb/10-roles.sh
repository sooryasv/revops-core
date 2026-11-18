#!/bin/sh
# Runs once, when the compose Postgres volume is first created. Executes contract/roles.sql as superuser.
set -eu
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  -v dbname="$POSTGRES_DB" \
  -v owner_password="$REVOPS_OWNER_PASSWORD" \
  -v app_password="$REVOPS_APP_PASSWORD" \
  -v ai_password="$AI_READER_PASSWORD" \
  -f /contract/roles.sql
