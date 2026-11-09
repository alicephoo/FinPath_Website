#!/bin/sh
set -eu

# Fail before starting the API or touching the database if production is
# missing its token-signing secret. The application already fails closed for
# production email and refresh-cookie configuration.
environment=$(
  printf '%s' "${FINPATH_ENVIRONMENT:-development}" | tr '[:upper:]' '[:lower:]'
)
case "$environment" in
  production|prod)
    if [ -z "$(printf '%s' "${FINPATH_ACCESS_TOKEN_SECRET:-}" | tr -d '[:space:]')" ]; then
      echo "FINPATH_ACCESS_TOKEN_SECRET is required in production." >&2
      exit 1
    fi
    ;;
esac

# Local Docker Compose keeps the original behavior: migrate and seed on
# startup. Deployment platforms such as Render set this to false so a cold
# start does not run Alembic or the seeder on every wake-up.
run_startup_migrations=$(
  printf '%s' "${FINPATH_RUN_STARTUP_MIGRATIONS:-true}" | tr '[:upper:]' '[:lower:]'
)

case "$run_startup_migrations" in
  1|true|yes|on)
    echo "Applying FinPath database migrations..."
    alembic upgrade head

    echo "Seeding FinPath reference data..."
    python -m app.db.seed
    ;;
  0|false|no|off)
    echo "Skipping startup migrations and reference seed (FINPATH_RUN_STARTUP_MIGRATIONS=${run_startup_migrations})."
    ;;
  *)
    echo "Invalid FINPATH_RUN_STARTUP_MIGRATIONS value. Use true or false." >&2
    exit 1
    ;;
esac

echo "Starting FinPath API..."
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" --proxy-headers
