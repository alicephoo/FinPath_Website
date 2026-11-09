#!/usr/bin/env bash
# Start the FinPath API for browser E2E tests against the dedicated test
# database. Secrets are read from backend/.env and are never printed.
#
# Safety:
# - The runtime, migration, and registration URLs are replaced with the
#   FINPATH_TEST_* values, so the E2E server can never use the development DB.
# - No migrations, seeds, schema changes, or destructive cleanup are run.
set -euo pipefail

backend_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../backend" && pwd)"
cd "$backend_dir"

set -a
# shellcheck disable=SC1091
. ./.env
set +a

: "${FINPATH_TEST_DATABASE_URL:?FINPATH_TEST_DATABASE_URL is required}"
: "${FINPATH_TEST_MIGRATION_DATABASE_URL:?FINPATH_TEST_MIGRATION_DATABASE_URL is required}"
: "${FINPATH_TEST_REGISTRATION_DATABASE_URL:?FINPATH_TEST_REGISTRATION_DATABASE_URL is required}"

export FINPATH_DATABASE_URL="$FINPATH_TEST_DATABASE_URL"
export FINPATH_MIGRATION_DATABASE_URL="$FINPATH_TEST_MIGRATION_DATABASE_URL"
export FINPATH_REGISTRATION_DATABASE_URL="$FINPATH_TEST_REGISTRATION_DATABASE_URL"
export FINPATH_ENVIRONMENT="test"
export FINPATH_FRONTEND_ORIGINS="http://localhost:5173,http://127.0.0.1:5173"

exec .venv/bin/uvicorn app.main:app \
  --host 127.0.0.1 \
  --port "${E2E_BACKEND_PORT:-8000}"
