#!/bin/sh
# Runs once on first PostgreSQL initialization. Creates the three FinPath
# identities expected by the application and makes the migration identity the
# database owner. Migrations grant the actual table/column privileges.
set -eu

: "${POSTGRES_DB:?POSTGRES_DB is required}"
: "${POSTGRES_USER:?POSTGRES_USER is required}"
: "${FINPATH_DB_PASSWORD:?FINPATH_DB_PASSWORD is required}"
: "${FINPATH_MIGRATION_DB_PASSWORD:?FINPATH_MIGRATION_DB_PASSWORD is required}"
: "${FINPATH_REGISTRATION_DB_PASSWORD:?FINPATH_REGISTRATION_DB_PASSWORD is required}"

psql -v ON_ERROR_STOP=1 \
  --username "$POSTGRES_USER" \
  --dbname "$POSTGRES_DB" \
  -v db_name="$POSTGRES_DB" \
  -v app_password="$FINPATH_DB_PASSWORD" \
  -v migration_password="$FINPATH_MIGRATION_DB_PASSWORD" \
  -v registration_password="$FINPATH_REGISTRATION_DB_PASSWORD" <<'EOSQL'
CREATE ROLE finpath_migrator LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
  NOREPLICATION NOBYPASSRLS PASSWORD :'migration_password';
CREATE ROLE finpath_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
  NOREPLICATION NOBYPASSRLS PASSWORD :'app_password';
CREATE ROLE finpath_registration LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE
  NOREPLICATION NOBYPASSRLS PASSWORD :'registration_password';

ALTER DATABASE :"db_name" OWNER TO finpath_migrator;
ALTER SCHEMA public OWNER TO finpath_migrator;
GRANT USAGE, CREATE ON SCHEMA public TO finpath_migrator;
GRANT CONNECT ON DATABASE :"db_name" TO finpath_app, finpath_registration;
EOSQL
