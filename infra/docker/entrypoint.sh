#!/bin/sh
# Container entrypoint. Preferred production flow: run `alembic upgrade head` as a pipeline step with the OWNER role.
# For the single-replica demo, RUN_MIGRATIONS=1 runs it here (trade-off: the container holds the owner URL).
set -eu

if [ "${RUN_MIGRATIONS:-0}" = "1" ]; then
  echo "entrypoint: running migrations"
  alembic upgrade head
fi

if [ "${SEED_DEMO:-0}" = "1" ] && [ "${ENV:-dev}" != "production" ]; then
  echo "entrypoint: seeding demo data (skipped with a message if data already exists)"
  python -m app.cli seed-demo || echo "entrypoint: seed-demo did not run (already seeded?)"
fi

exec "$@"
