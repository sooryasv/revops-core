#!/usr/bin/env bash
# Starts the full stack for E2E: fresh database, roles, migrations, deterministic demo seed, then the real app (FastAPI
# serving the production SPA build) with the FAKE payment provider. Used by playwright.config.ts (webServer) and by CI.
#
#   E2E_ADMIN_URL   superuser DSN (default: local sandbox cluster)
#   E2E_PORT        port for the app (default 8001)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
ADMIN_URL="${E2E_ADMIN_URL:-postgresql://postgres@127.0.0.1:5433/postgres}"
PORT="${E2E_PORT:-8001}"
DB="revops_e2e"
# Obviously fake, throwaway passwords for a database that is dropped and recreated on every run.
OWNER_PW="e2e-owner-pw"; APP_PW="e2e-app-pw"; AI_PW="e2e-ai-pw"
export SEED_DEMO_PASSWORD="${SEED_DEMO_PASSWORD:-e2e-demo-password-1}"

HOSTPORT="${ADMIN_URL#*@}"; HOSTPORT="${HOSTPORT%%/*}"
HOST="${HOSTPORT%%:*}"; DBPORT="${HOSTPORT##*:}"
psql_admin() { psql -X -q -h "$HOST" -p "$DBPORT" -U postgres "$@"; }

psql_admin -d postgres -v ON_ERROR_STOP=1 -c "DROP DATABASE IF EXISTS $DB WITH (FORCE)" -c "CREATE DATABASE $DB"
psql_admin -d "$DB" -v ON_ERROR_STOP=1 -v dbname="$DB" -v owner_password="$OWNER_PW" -v app_password="$APP_PW" \
  -v ai_password="$AI_PW" -f "$ROOT/contract/roles.sql"

export MIGRATION_DATABASE_URL="postgresql+asyncpg://revops_owner:$OWNER_PW@$HOST:$DBPORT/$DB"
export DATABASE_URL="postgresql+asyncpg://revops_app:$APP_PW@$HOST:$DBPORT/$DB"
export ENV=dev
export PAYMENT_PROVIDER=fake
export ALLOWED_ORIGINS="http://localhost:$PORT,http://127.0.0.1:$PORT"
export PUBLIC_BASE_URL="http://localhost:$PORT"
export STATIC_DIR="$ROOT/frontend/dist"
export LOG_LEVEL=WARNING

[ -f "$STATIC_DIR/index.html" ] || { echo "build the SPA first: (cd frontend && npm run build)" >&2; exit 1; }

cd "$ROOT/backend"
RUNNER=(python -m)
if command -v uv >/dev/null 2>&1 && [ -z "${E2E_NO_UV:-}" ]; then RUNNER=(uv run python -m); fi
"${RUNNER[@]}" alembic upgrade head
"${RUNNER[@]}" app.cli seed-demo --reset
exec "${RUNNER[@]}" uvicorn app.main:create_app --factory --host 127.0.0.1 --port "$PORT" --proxy-headers --forwarded-allow-ips 127.0.0.1
